"""
SubstanceMapper — FastAPI web application
=========================================
Upload a CSV/Excel file with free-text substance fields, a reference list,
and an optional lookup table. The app preprocesses the data, runs fuzzy
matching (on unique values only for speed), and returns a downloadable CSV
with extracted substances and similarity scores, plus summary statistics.
"""
import io
import os
import re
import uuid
import asyncio
import threading
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from fastapi import FastAPI, File, Form, UploadFile, Request, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.recoding import add_substance

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
RESULT_DIR = BASE_DIR / "results"
RESULT_DIR.mkdir(exist_ok=True)

app = FastAPI(title="SubstanceMapper")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

# In-memory job store:  job_id -> {"status", "progress", "total", "result_path", "stats", "error"}
JOBS: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _read_uploaded_file(content: bytes, filename: str) -> pd.DataFrame:
    """Read CSV or Excel bytes into a DataFrame."""
    fname = filename.lower()
    if fname.endswith(".csv"):
        # Try common separators
        for sep in [";", ",", "\t"]:
            try:
                df = pd.read_csv(io.BytesIO(content), sep=sep)
                if df.shape[1] > 1:
                    return df
            except Exception:
                continue
        return pd.read_csv(io.BytesIO(content))
    elif fname.endswith((".xlsx", ".xls")):
        return pd.read_excel(io.BytesIO(content))
    else:
        raise ValueError(f"Unsupported file type: {filename}. Use CSV or Excel.")


def _compute_stats(original: pd.Series, result_df: pd.DataFrame) -> dict:
    """Compute summary statistics after matching."""
    n_rows = len(original)

    # Detect the substance and similarity columns (may be named with or without trailing digit)
    substance_col = next(
        (c for c in result_df.columns if re.match(r"Extracted_Substance\d*$", c)), None
    )
    similarity_col = next(
        (c for c in result_df.columns if re.match(r"Similarity\d*$", c)), None
    )

    if substance_col is None:
        return {"n_rows": n_rows, "error": "No substance column found in output."}

    extracted = result_df[substance_col].dropna()
    n_extracted = int(extracted.notna().sum())
    n_unique_extracted = int(extracted.nunique())
    n_unique_input = int(original.nunique())

    stats: dict = {
        "n_rows": n_rows,
        "n_unique_input": n_unique_input,
        "n_extracted": n_extracted,
        "n_not_extracted": n_rows - n_extracted,
        "pct_extracted": round(100 * n_extracted / n_rows, 1) if n_rows else 0,
        "n_unique_extracted": n_unique_extracted,
    }

    if similarity_col is not None:
        sims = result_df[similarity_col].dropna()
        if len(sims):
            stats["sim_mean"] = round(float(sims.mean()), 3)
            stats["sim_median"] = round(float(sims.median()), 3)
            stats["sim_min"] = round(float(sims.min()), 3)
            stats["sim_max"] = round(float(sims.max()), 3)

            # Distribution buckets
            bins = [0, 0.7, 0.8, 0.9, 0.95, 1.01]
            labels = ["<0.70", "0.70–0.79", "0.80–0.89", "0.90–0.94", "0.95–1.00"]
            cut = pd.cut(sims, bins=bins, labels=labels, right=False)
            dist = cut.value_counts().sort_index()
            stats["sim_distribution"] = {str(k): int(v) for k, v in dist.items()}

    return stats


# ---------------------------------------------------------------------------
# Background processing
# ---------------------------------------------------------------------------

def _run_job(
    job_id: str,
    data_bytes: bytes,
    data_filename: str,
    substance_col_name: str,
    ref_bytes: bytes,
    ref_filename: str,
    ref_col_name: str,
    lookup_bytes: Optional[bytes],
    lookup_filename: Optional[str],
    lookup_label_col: Optional[str],
    lookup_substance_col: Optional[str],
    threshold: float,
    max_per_match: int,
    only_first_match: bool,
):
    try:
        JOBS[job_id]["status"] = "running"

        # --- Load data file ---
        data_df = _read_uploaded_file(data_bytes, data_filename)
        if substance_col_name not in data_df.columns:
            raise ValueError(
                f"Column '{substance_col_name}' not found in uploaded file. "
                f"Available: {list(data_df.columns)}"
            )
        col_with_substances = data_df[substance_col_name].astype(str)

        # --- Load reference list ---
        ref_df = _read_uploaded_file(ref_bytes, ref_filename)
        if ref_col_name not in ref_df.columns:
            raise ValueError(
                f"Column '{ref_col_name}' not found in reference file. "
                f"Available: {list(ref_df.columns)}"
            )
        col_with_ref = ref_df[ref_col_name].dropna().astype(str)

        # --- Optional lookup table ---
        lookup_table = None
        if lookup_bytes is not None:
            lt_df = _read_uploaded_file(lookup_bytes, lookup_filename)
            # Rename user-chosen columns to the expected names
            rename_map = {}
            if lookup_label_col and lookup_label_col in lt_df.columns:
                rename_map[lookup_label_col] = "label"
            if lookup_substance_col and lookup_substance_col in lt_df.columns:
                rename_map[lookup_substance_col] = "substance"
            if rename_map:
                lt_df = lt_df.rename(columns=rename_map)
            if "label" not in lt_df.columns or "substance" not in lt_df.columns:
                raise ValueError(
                    "Lookup table must have 'label' and 'substance' columns "
                    "(or you must specify which columns to use)."
                )
            lookup_table = lt_df

        JOBS[job_id]["total"] = len(col_with_substances)

        def progress_cb(current: int, total: int):
            JOBS[job_id]["progress"] = current
            JOBS[job_id]["total"] = total

        result_df = add_substance(
            col_with_substances=col_with_substances,
            col_with_ref_substances=col_with_ref,
            threshold=threshold,
            max_per_match_id=max_per_match,
            only_first_match=only_first_match,
            lookup_table=lookup_table,
            progress_callback=progress_cb,
        )

        # Attach any other columns from the original data (except the substance column itself)
        other_cols = [c for c in data_df.columns if c != substance_col_name]
        if other_cols:
            final_df = pd.concat(
                [data_df[other_cols].reset_index(drop=True), result_df.reset_index(drop=True)],
                axis=1,
            )
        else:
            final_df = result_df

        # Save result
        result_path = RESULT_DIR / f"{job_id}.csv"
        final_df.to_csv(result_path, index=False)

        # Stats
        stats = _compute_stats(col_with_substances, result_df)

        JOBS[job_id].update(
            {
                "status": "done",
                "result_path": str(result_path),
                "stats": stats,
                "columns": list(final_df.columns),
            }
        )

    except Exception as exc:
        import traceback
        JOBS[job_id]["status"] = "error"
        JOBS[job_id]["error"] = str(exc)
        JOBS[job_id]["traceback"] = traceback.format_exc()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/submit")
async def submit_job(
    request: Request,
    data_file: UploadFile = File(...),
    substance_col: str = Form(...),
    ref_file: UploadFile = File(...),
    ref_col: str = Form(...),
    lookup_file: Optional[UploadFile] = File(None),
    lookup_label_col: Optional[str] = Form(None),
    lookup_substance_col: Optional[str] = Form(None),
    threshold: float = Form(0.85),
    max_per_match: int = Form(2),
    only_first_match: str = Form("true"),  # received as string, parsed below
):
    only_first_match_bool = only_first_match.lower() in ("true", "1", "yes")
    job_id = str(uuid.uuid4())
    JOBS[job_id] = {"status": "queued", "progress": 0, "total": 0}

    data_bytes = await data_file.read()
    ref_bytes = await ref_file.read()

    lookup_bytes = None
    lookup_filename = None
    if lookup_file and lookup_file.filename and lookup_file.size != 0:
        lookup_bytes = await lookup_file.read()
        lookup_filename = lookup_file.filename

    thread = threading.Thread(
        target=_run_job,
        args=(
            job_id,
            data_bytes,
            data_file.filename,
            substance_col,
            ref_bytes,
            ref_file.filename,
            ref_col,
            lookup_bytes,
            lookup_filename,
            lookup_label_col or None,
            lookup_substance_col or None,
            threshold,
            max_per_match,
            only_first_match_bool,
        ),
        daemon=True,
    )
    thread.start()

    return JSONResponse({"job_id": job_id})


@app.get("/status/{job_id}")
async def job_status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JSONResponse(job)


@app.get("/download/{job_id}")
async def download_result(job_id: str):
    job = JOBS.get(job_id)
    if not job or job.get("status") != "done":
        raise HTTPException(status_code=404, detail="Result not ready or job not found")

    result_path = Path(job["result_path"])
    if not result_path.exists():
        raise HTTPException(status_code=404, detail="Result file missing")

    content = result_path.read_bytes()
    return StreamingResponse(
        io.BytesIO(content),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=substance_results_{job_id[:8]}.csv"},
    )


@app.get("/preview/{job_id}")
async def preview_result(job_id: str, n: int = 10):
    """Return the first n rows of the result as JSON for UI preview."""
    job = JOBS.get(job_id)
    if not job or job.get("status") != "done":
        raise HTTPException(status_code=404, detail="Result not ready")
    result_path = Path(job["result_path"])
    df = pd.read_csv(result_path, nrows=n)
    return JSONResponse({"columns": list(df.columns), "rows": df.fillna("").values.tolist()})
