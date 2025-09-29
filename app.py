# app.py (fixed)
import os
import io
import uuid
import base64
import traceback
from flask import Flask, request, render_template, send_file, redirect, url_for, abort
import pandas as pd
import matplotlib

# Force headless backend for matplotlib (needed in Docker)
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from processor import process_df

app = Flask(__name__, template_folder="templates")

TOKEN_MAP = {}   # token -> processed CSV path
UPLOAD_FOLDER = "/app/tmp"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


@app.route("/", methods=["GET"])
def index():
    return render_template("form.html")


@app.route("/process", methods=["POST"])
def process():
    """
    Accept two uploaded files and parameters, run processing and return result page.
    """
    try:
        data_file = request.files.get("data_file")
        ref_file = request.files.get("ref_file")

        if not data_file:
            return render_template("result.html", error="No data CSV uploaded. Please choose a data CSV.")
        if not ref_file:
            return render_template("result.html", error="No reference CSV uploaded. Please choose a reference CSV.")

        # Read CSVs as pandas dataframes (semicolon-separated in your setup)
        try:
            data_df = pd.read_csv(data_file, sep=";")
        except Exception as e:
            return render_template("result.html", error=f"Failed to read data CSV (must be semicolon separated!): {e}")

        try:
            ref_df = pd.read_csv(ref_file, sep=";")
        except Exception as e:
            return render_template("result.html", error=f"Failed to read reference CSV (must be semicolon separated!): {e}")

        # Read params from form (defaults as you configured)
        substance_col = request.form.get("substance_col", "Bezeichnung").strip() or "Bezeichnung"
        ref_substance_col = request.form.get("ref_substance_col", "Substanz").strip() or "Substanz"
        threshold = request.form.get("threshold", "0.85").strip()
        max_per_match_id = request.form.get("max_per_match_id", "2").strip()
        only_first_match = request.form.get("only_first_match", None)

        params = {
            "substance_col": substance_col,
            "ref_substance_col": ref_substance_col,
            "threshold": threshold,
            "max_per_match_id": max_per_match_id,
            "only_first_match": "true" if only_first_match is not None else "false",
        }

        # Call processor (which calls add_substance internally)
        processed_df, stats = process_df(data_df, ref_df, params)

        # Create a unique token before saving the file and use it in filename
        token = str(uuid.uuid4())
        file_path = os.path.join(UPLOAD_FOLDER, f"processed_{token}.csv")

        # Save processed CSV (semicolon separated)
        processed_df.to_csv(file_path, sep=";", index=False)

        # Store mapping for download
        TOKEN_MAP[token] = file_path

        # Create the bar chart in-memory and encode as base64 for embedding
        try:
            found = stats.get("total_rows", 0) - stats.get("missing_count", 0)
            missing = stats.get("missing_count", 0)
            labels = ["Found", "Missing"]
            values = [found, missing]

            fig, ax = plt.subplots(figsize=(6, 4))
            bars = ax.bar(labels, values)
            ax.set_title("Matches: Found vs Missing")
            ax.set_ylabel("Count")

            # annotate bars with values
            for bar in bars:
                height = bar.get_height()
                ax.annotate(f"{int(height)}",
                            xy=(bar.get_x() + bar.get_width() / 2, height),
                            xytext=(0, 3), textcoords="offset points", ha="center", va="bottom")

            ymax = max(values) if values else 1
            ax.set_ylim(0, ymax * 1.2 + 1)  # add margin to avoid clipping
            fig.tight_layout()

            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
            plt.close(fig)
            buf.seek(0)
            plot_base64 = base64.b64encode(buf.getvalue()).decode("ascii")
        except Exception:
            plot_base64 = None
            app.logger.exception("Failed to create plot")

        # Render result page: we pass stats, download token, and the embedded plot
        return render_template(
            "result.html",
            stats=stats,
            download_token=token,
            plot_base64=plot_base64
        )

    except Exception as e:
        # Unexpected error: log and show traceback on the page (dev only)
        tb = traceback.format_exc()
        app.logger.error("Unhandled error in /process: %s", tb)
        return render_template("result.html", error="Processing failed. See trace below.", error_trace=tb)


@app.route("/download")
def download():
    token = request.args.get("token")
    if not token:
        abort(404, "Download token missing")

    # derive the expected filename from the token (must match how you saved it)
    file_path = os.path.join(UPLOAD_FOLDER, f"processed_{token}.csv")

    real_upload_dir = os.path.realpath(UPLOAD_FOLDER)
    real_path = os.path.realpath(file_path)
    if not real_path.startswith(real_upload_dir + os.sep) and real_path != real_upload_dir:
        abort(403, "Forbidden")

    if not os.path.exists(file_path):
        abort(404, "File not found or expired")

    return send_file(
        file_path,
        as_attachment=True,
        download_name="processed_substances.csv",
        mimetype="text/csv"
    )


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=8000)
