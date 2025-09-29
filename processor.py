import pandas as pd
import time
from utils import preprocess_data, get_matches

def add_substance(
    col_with_substances: pd.Series,
    col_with_ref_substances: pd.Series,
    threshold: float = 0.85,
    max_per_match_id: int = 2,
    only_first_match: bool = True,
) -> pd.DataFrame:
    """
    This is the pipeline for creating the service variable
    for substances using ZfKD data.
    The functions are described in detail in utils.py.
    In short, the functions takes a pandasDataFrame column
    as an input and preprocesses its entries first.
    This results in a pandasDataFrame with the original
    input in one column and the preprocessed text in another one.
    The fuzzy matching relies on FuzzyMatcher from spaczz.
    It uses the preprocessed input and a reference list that
    the uses needs to provide. The reference list must be 
    a pandasDataFrame column (pd.Series) with substance names.
    The output is a pandasDataFrame with the original input,
    the preprocessed text and all possible matches with similary score.
    Use parameters to control output and sensitivity of the matcher. 
    
    arguments:
        col_with_substances: column with substances to be recoded
        col_with_ref_substances: column with reference substances
        threshold: similarity threshold, default 0.85
        max_per_match_id: maximum number of matches per ID, default 2
        only_first_match: return only the first match per ID
    """
    preprocessed_out = preprocess_data(col_with_substances)

    final_output = get_matches(
        preprocessed_out,
        col_with_ref_substances,
        threshold=threshold,
        max_per_match_id=max_per_match_id,
        only_first_match=only_first_match,
    )

    return final_output

def _parse_bool(value, default=False):
    """Tolerant boolean parser for form values."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    return s in ("1", "true", "yes", "on")

def _find_mapped_column(df: pd.DataFrame):
    """
    Find the column name in df that holds the mapped reference identifier.
    Prefer exact 'Mapped_to' (no digit), otherwise first column starting with 'Mapped_to'.
    Return None if none found.
    """
    if "Mapped_to" in df.columns:
        return "Mapped_to"
    # look for columns like Mapped_to1, Mapped_to2...
    for col in df.columns:
        if col.startswith("Mapped_to"):
            return col
    return None

def process_df(data_df: pd.DataFrame, ref_df: pd.DataFrame, params: dict):
    """
    Calls add_substance and computes stats. Uses the match_df returned by add_substance
    to detect missing mappings, aggregates the missing original input values and returns
    the top-20 most frequent missing inputs as (value, count) tuples in stats['unique_missing_examples'].
    """
    start = time.time()

    # Parse params
    try:
        threshold = float(params.get("threshold", 0.85))
    except Exception:
        threshold = 0.85
    try:
        max_per_match_id = int(params.get("max_per_match_id", 2))
    except Exception:
        max_per_match_id = 2
    only_first_match = _parse_bool(params.get("only_first_match", True))

    # Column selection (support common param names)
    substance_col = params.get("substance_col") or "substance"
    ref_substance_col = params.get("ref_substance_col") or "substance"

    # Prepare series to call add_substance
    if substance_col in data_df.columns:
        col_series = data_df[substance_col].astype(str)
    else:
        col_series = pd.Series([""] * len(data_df))

    if ref_substance_col in ref_df.columns:
        ref_series = ref_df[ref_substance_col].astype(str)
    else:
        ref_series = pd.Series([], dtype=str)

    # Call function from spacy_matching
    match_df = add_substance(
        col_with_substances=col_series,
        col_with_ref_substances=ref_series,
        threshold=threshold,
        max_per_match_id=max_per_match_id,
        only_first_match=only_first_match,
    )

    data_reset = data_df.reset_index(drop=True)
    match_reset = match_df.reset_index(drop=True)
    combined = pd.concat([data_reset, match_reset], axis=1)

    # Determine mapped column from match_df
    mapped_col = _find_mapped_column(match_reset)

    total_rows = len(combined)

    # Determine missing mask using mapped_col located in match_df
    if mapped_col is None:
        # If no mapping columns present, all rows considered missing
        missing_mask = pd.Series([True] * total_rows)
    else:
        missing_mask = match_reset[mapped_col].isna() | (match_reset[mapped_col].astype(str).str.strip() == "")

    missing_count = int(missing_mask.sum())
    missing_pct = round((missing_count / total_rows) * 100, 2) if total_rows > 0 else 0.0

    # Use match_df to compute aggregated top-20 missing original inputs
    unique_missing_examples = []  # list of (value, count) tuples
    if "Original" in match_reset.columns:
        # Get the Original values for rows that are missing
        original_missing = (
            match_reset.loc[missing_mask, "Original"]
            .astype(str)
            .str.strip()
            .replace({"": None})
            .dropna()
        )

        if not original_missing.empty:
            # count occurrences of each original missing value
            freq = original_missing.value_counts(dropna=True)
            # top 20 most frequent missing values
            top20 = freq.head(20)
            # convert to list of (value, count) tuples
            unique_missing_examples = [(str(idx), int(cnt)) for idx, cnt in top20.items()]

        # unique total distinct original input values
        original_all = match_reset["Original"].astype(str).str.strip().replace({"": None}).dropna()
        unique_total = int(original_all.nunique())
        unique_missing_count = int(original_missing.nunique())
        unique_missing_pct = round((unique_missing_count / (unique_total or 1)) * 100, 2)
    else:
        unique_total = 0
        unique_missing_count = missing_count
        unique_missing_pct = round((unique_missing_count / (total_rows or 1)) * 100, 2)
        unique_missing_examples = []

    processing_seconds = round(time.time() - start, 4)

    stats = {
        "total_rows": total_rows,
        "missing_count": missing_count,
        "missing_pct": missing_pct,
        "unique_total_values": unique_total,
        "unique_missing_count": unique_missing_count,
        "unique_missing_pct": unique_missing_pct,
        "processing_seconds": processing_seconds,
        "columns_out": list(match_df.columns),
        "mapped_column": mapped_col,
        "unique_missing_examples": unique_missing_examples,  # list of (value, count)
    }

    return match_df, stats
