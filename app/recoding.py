import pandas as pd
from .utils import preprocess_data, get_matches_deduped, fuzzy_match, add_spaces


def add_substance(
    col_with_substances: pd.Series,
    col_with_ref_substances: pd.Series,
    threshold: float = 0.85,
    max_per_match_id: int = 2,
    only_first_match: bool = True,
    lookup_table: pd.DataFrame | None = None,
    progress_callback=None,
) -> pd.DataFrame:
    """
    Pipeline for extracting / standardising substance names from free text.

    Uses ``get_matches_deduped`` internally: unique preprocessed texts are
    matched only once, then results are joined back to the full dataset.
    This can be significantly faster when many rows share the same input.

    Arguments
    ---------
    col_with_substances:
        Column with raw free-text substance descriptions.
    col_with_ref_substances:
        Reference Series of canonical substance names.
    threshold:
        Fuzzy-match similarity threshold in [0, 1]. Default 0.85.
    max_per_match_id:
        Maximum hits returned per unique resolved substance. Default 2.
    only_first_match:
        Return only the top hit per input row. Default True.
    lookup_table:
        Optional DataFrame with columns ``label`` and ``substance``
        (and optionally ``ATC_code``).
    progress_callback:
        Optional callable(current: int, total: int) for progress reporting.
    """
    preprocessed_out = preprocess_data(col_with_substances)

    final_output = get_matches_deduped(
        preprocessed_out,
        col_with_ref_substances,
        threshold=threshold,
        max_per_match_id=max_per_match_id,
        only_first_match=only_first_match,
        lookup_table=lookup_table,
        progress_callback=progress_callback,
    )

    return final_output


def add_protocol(
    col_with_protocols: pd.Series,
    col_with_ref: pd.Series,
    threshold: float = 0.9,
) -> pd.DataFrame:
    """
    Returns DataFrame with extracted protocol code and similarity (0..1).
    """
    protocol_df = col_with_protocols.to_frame(name="Original")
    protocol_df[["Extracted_Code", "Similarity"]] = protocol_df["Original"].apply(
        lambda x: pd.Series(fuzzy_match(x, col_with_ref, threshold=threshold))
    )

    protocol_df["Preprocessed"] = add_spaces(protocol_df["Original"])
    protocol_df["Similarity"] = protocol_df["Similarity"] / 100.0
    protocol_df = protocol_df[["Original", "Extracted_Code", "Similarity"]]
    protocol_df.rename(columns={
        "Extracted_Code": "Extracted_Protocol_Code",
        "Similarity": "SimilarityCode",
    }, inplace=True)

    return protocol_df
