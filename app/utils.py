"""
Substance matching relies on
data wrangling using pandas and
fuzzy string matching with spacy and spaczz.
"""
import re
import pandas as pd
import spacy
from spaczz.matcher import FuzzyMatcher
import numpy as np
from rapidfuzz import process, fuzz
from tqdm import tqdm


# ---------------------------------------------------------------------------
# Helper / preprocessing functions
# ---------------------------------------------------------------------------

def prepare_free_text(input_col: pd.Series) -> pd.DataFrame:
    """Prepares data by renaming, stripping, and cleaning null or empty entries."""
    input_data = pd.DataFrame(
        {
            "ID": range(1, len(input_col) + 1),
            "Original": input_col.fillna("NA").replace("", "NA"),
        }
    )
    input_data["Original"] = input_data["Original"].astype(str).str.strip()
    return input_data


def remove_short_words(text: str) -> str:
    """Removes words shorter than 2 characters."""
    return " ".join([word for word in text.split() if len(word) >= 2])


def find_5FU(text: str) -> str:
    """Translates 5-FU variants and common misspellings to 'fluorouracil'."""
    pattern = (
        r"5 fu|5fu|5-fu|5_fu|Fluoruracil|flourouracil|5-fluoruuracil|"
        r"5-fluoro-uracil|5-fluoruuracil|5-fluoruracil|floururacil|"
        r"5-fluorounacil|flourouraci|5-fluourouracil"
    )
    return re.sub(pattern, "fluorouracil", text, flags=re.IGNORECASE)


def find_gemcitabin(text: str) -> str:
    """Normalises common misspellings of gemcitabin."""
    return re.sub(
        r"Gemcibatin(?:e)?(?: Mono)?", "gemcitabin", text, flags=re.IGNORECASE
    )


def find_Paclitaxel_nab(text: str) -> str:
    """Translates 'nab-Paclitaxel' variants to the canonical 'Paclitaxel nab'."""
    return re.sub(
        r"\bnab[\s\-]?Paclitaxel\b", "Paclitaxel nab", text, flags=re.IGNORECASE
    )


def add_spaces(s: pd.Series) -> pd.Series:
    """
    Preprocesses a Series of strings to improve fuzzy matching:
    - Adds spaces around brackets, commas, semicolons, colons
    - Separates letters from digits
    - Normalises whitespace and lowercases
    """
    def clean_text(text: str) -> str:
        if text is None:
            return ""
        text = str(text)
        text = re.sub(r'-', ' ', text)
        text = re.sub(r'([()\[\]{},:;])', r' \1 ', text)
        text = re.sub(r'(\d)(\D)', r'\1 \2', text)
        text = re.sub(r'(\D)(\d)', r'\1 \2', text)
        text = re.sub(r'([^\w\s/\-])(\w)', r'\1 \2', text)
        text = re.sub(r'(\w)([^\w\s/\-])', r'\1 \2', text)
        text = re.sub(r'\s+', ' ', text).strip().lower()
        return text

    return s.map(clean_text)


def remove_conjunctions(text: str) -> str:
    """Removes standalone 'und' and 'and', preserving substrings (e.g. 'Fundus')."""
    text = re.sub(r'\bund\b', ' ', text, flags=re.IGNORECASE)
    text = re.sub(r'\band\b', ' ', text, flags=re.IGNORECASE)
    return text


def _protect_atc_codes(text: str) -> tuple[str, dict]:
    """
    Replaces ATC codes in *text* with placeholder tokens before ``add_spaces``
    runs, preventing them from being split into separate tokens.
    """
    placeholders: dict[str, str] = {}

    def replace(match: re.Match) -> str:
        code = match.group(0).upper()
        key = f"ATCPLACEHOLDER{chr(65 + len(placeholders))}X"
        placeholders[key.lower()] = code
        return key

    protected = re.sub(
        r'\b[A-Z]\d{2}[A-Z]{2}\d{2}\b', replace, text, flags=re.IGNORECASE
    )
    return protected, placeholders


def _restore_atc_codes(text: str, placeholders: dict) -> str:
    """Restores ATC codes from placeholders inserted by ``_protect_atc_codes``."""
    for key, code in placeholders.items():
        text = text.replace(key, code)
    return text


def _protect_and_clean(text: str) -> str:
    """
    Protects ATC codes in *text*, runs the same cleaning logic as
    ``add_spaces`` on the result, then restores the original ATC codes.
    """
    protected, placeholders = _protect_atc_codes(text)

    t = str(protected)
    t = re.sub(r'-', ' ', t)
    t = re.sub(r'([()\[\]{},:;])', r' \1 ', t)
    t = re.sub(r'(\d)(\D)', r'\1 \2', t)
    t = re.sub(r'(\D)(\d)', r'\1 \2', t)
    t = re.sub(r'([^\w\s/\-])(\w)', r'\1 \2', t)
    t = re.sub(r'(\w)([^\w\s/\-])', r'\1 \2', t)
    t = re.sub(r'\s+', ' ', t).strip().lower()

    return _restore_atc_codes(t, placeholders)


def preprocess_data(col_with_free_text: pd.Series) -> pd.DataFrame:
    """Applies all preprocessing steps sequentially to the input series."""
    df = prepare_free_text(col_with_free_text)
    processed = (
        df["Original"]
        .apply(find_5FU)
        .apply(find_gemcitabin)
        .apply(find_Paclitaxel_nab)
        .apply(remove_conjunctions)
        .apply(remove_short_words)
        .str.strip()
    )
    df["Preprocessed_text"] = processed.apply(_protect_and_clean)
    return df


def clean_series(s: pd.Series) -> pd.Series:
    """
    Applies consistent string preprocessing to a Series.
    ATC codes are protected before cleaning and restored afterwards.
    """
    return s.astype(str).apply(_protect_and_clean)


ATC_CODE_PATTERN = re.compile(r'^[A-Z]\d{2}[A-Z]{2}\d{2}$', re.IGNORECASE)


def is_atc_code(text: str) -> bool:
    """Returns True if text matches the ATC code format (e.g. L02BX01)."""
    return bool(ATC_CODE_PATTERN.match(text.strip()))


def _build_atc_label_to_substance(
    lookup_table: pd.DataFrame,
    label_clean: pd.Series,
) -> dict[str, str]:
    """
    Builds a mapping of raw uppercase ATC code → substance name from the
    lookup table.
    """
    label_raw_upper = lookup_table["label"].astype(str).str.strip().str.upper()
    substances = lookup_table["substance"].astype(str)

    if "ATC_code" in lookup_table.columns:
        atc_mask = lookup_table["ATC_code"].astype(int) == 1
    else:
        atc_mask = label_clean.map(is_atc_code)

    return dict(zip(label_raw_upper[atc_mask.values], substances[atc_mask.values]))


def _match_atc_from_lookup(
    text: str,
    atc_label_to_substance: dict[str, str],
) -> list[dict]:
    """
    Scans whitespace-delimited tokens in *text* for exact (case-insensitive)
    matches against known ATC codes from the lookup table.
    """
    matches = []
    seen: set[str] = set()

    for token in text.split():
        token_upper = token.strip().upper()
        substance = atc_label_to_substance.get(token_upper)
        if substance is not None and substance not in seen:
            seen.add(substance)
            matches.append({
                "hit_text": token,
                "mapped_to": substance,
                "similarity": 1.0,
            })

    return matches


def get_matches(
    preprocessed_data: pd.DataFrame,
    ref_substance: pd.Series,
    threshold: float = 0.85,
    max_per_match_id: int = 2,
    only_first_match: bool = False,
    lookup_table: pd.DataFrame | None = None,
    progress_callback=None,
) -> pd.DataFrame:
    """
    Match preprocessed free-text substance entries against a reference
    vocabulary using fuzzy matching, with optional ATC code resolution.

    For efficiency, this function should be called on *unique* preprocessed
    texts only (via ``get_matches_deduped``), then results joined back to
    the full dataset.
    """
    ref_original = ref_substance.dropna().astype(str)
    ref_clean = clean_series(ref_original)
    clean_to_original = dict(zip(ref_clean, ref_original))

    label_to_substance: dict[str, str] = {}
    atc_label_to_substance: dict[str, str] = {}
    fuzzy_vocab: set[str] = set(ref_clean)

    if lookup_table is not None:
        if not all(col in lookup_table.columns for col in ["label", "substance"]):
            raise KeyError("lookup_table must contain columns 'label' and 'substance'.")
        lookup_table = lookup_table.dropna(subset=["label", "substance"]).reset_index(drop=True)
        label_clean = clean_series(lookup_table["label"])

        atc_label_to_substance = _build_atc_label_to_substance(
            lookup_table, label_clean
        )
        label_raw_upper = lookup_table["label"].astype(str).str.strip().str.upper()
        atc_raw_upper = set(atc_label_to_substance.keys())

        non_atc_mask = ~label_raw_upper.isin(atc_raw_upper)
        label_to_substance = dict(
            zip(
                label_clean[non_atc_mask],
                lookup_table["substance"].astype(str)[non_atc_mask],
            )
        )
        fuzzy_vocab = set(ref_clean) | set(label_to_substance.keys())

    nlp = spacy.blank("de")
    matcher = FuzzyMatcher(nlp.vocab)

    for term in fuzzy_vocab:
        matcher.add(term, [nlp(term)])

    results = []
    synthetic_ratio = int(threshold * 100)
    total = len(preprocessed_data)

    for i, (_, row) in enumerate(preprocessed_data.iterrows()):
        text = row["Preprocessed_text"]
        original = row["Original"]

        doc = nlp(text)
        matches = list(matcher(doc))

        existing_match_ids = {m[0] for m in matches}
        text_lower = text.lower()

        for term in fuzzy_vocab:
            term_lower = term.lower()
            if term_lower == "" or term in existing_match_ids:
                continue
            if term_lower in text_lower:
                start_char = text_lower.find(term_lower)
                end_char = start_char + len(term_lower)
                span = doc.char_span(start_char, end_char, alignment_mode="expand")
                if span is not None:
                    start_token_idx = span.start
                    end_token_idx = span.end
                else:
                    start_token_idx, end_token_idx = 0, len(doc)
                matches.append((
                    term,
                    start_token_idx,
                    end_token_idx,
                    synthetic_ratio,
                    nlp(term),
                ))
                existing_match_ids.add(term)

        matches_filtered = [m for m in matches if m[3] >= threshold * 100]
        matches_sorted = sorted(matches_filtered, key=lambda x: x[3], reverse=True)

        accepted: list[tuple[int, int]] = []
        substance_counts: dict[str, int] = {}
        result_row: dict = {"Original": original, "Preprocessed": text}
        match_idx = 1

        if lookup_table is not None:
            for atc_m in _match_atc_from_lookup(text, atc_label_to_substance):
                result_row[f"Hit{match_idx}"] = atc_m["hit_text"]
                result_row[f"Mapped_to{match_idx}"] = atc_m["mapped_to"]
                result_row[f"Similarity{match_idx}"] = atc_m["similarity"]
                match_idx += 1

        for match_id, start, end, ratio, _ in matches_sorted:
            if match_id in label_to_substance:
                mapped_substance = label_to_substance[match_id]
            else:
                mapped_substance = clean_to_original.get(match_id, match_id)

            count = substance_counts.get(mapped_substance, 0)
            if count >= max_per_match_id:
                continue

            overlaps = any(
                not (end <= a_start or start >= a_end)
                for a_start, a_end in accepted
            )
            if overlaps:
                continue

            try:
                hit_text = doc[start:end].text
            except Exception:
                hit_text = text

            result_row[f"Hit{match_idx}"] = hit_text
            result_row[f"Mapped_to{match_idx}"] = mapped_substance
            result_row[f"Similarity{match_idx}"] = ratio / 100

            accepted.append((start, end))
            substance_counts[mapped_substance] = count + 1
            match_idx += 1

        results.append(result_row)

        if progress_callback is not None:
            progress_callback(i + 1, total)

    out = pd.DataFrame(results)

    # FIX: use .copy() to avoid SettingWithCopyWarning on column rename
    cleaned_df = out[
        [c for c in out.columns if c.startswith(("Original", "Mapped_to", "Similarity"))]
    ].copy()
    cleaned_df.columns = [
        re.sub(r"^Mapped_to", "Extracted_Substance", col)
        for col in cleaned_df.columns
    ]

    if only_first_match:
        cols_to_keep = ["Original", "Extracted_Substance1", "Similarity1"]
        available_columns = [col for col in cols_to_keep if col in cleaned_df.columns]
        # FIX: use .copy() to avoid SettingWithCopyWarning on column rename
        dta_selected = cleaned_df[available_columns].copy()
        dta_selected.columns = [
            re.sub(r"\d+$", "", col) for col in dta_selected.columns
        ]
        return dta_selected

    return cleaned_df


def get_matches_deduped(
    preprocessed_data: pd.DataFrame,
    ref_substance: pd.Series,
    threshold: float = 0.85,
    max_per_match_id: int = 2,
    only_first_match: bool = True,
    lookup_table: pd.DataFrame | None = None,
    progress_callback=None,
) -> pd.DataFrame:
    """
    Efficiency wrapper around ``get_matches`` that deduplicates on
    ``Preprocessed_text`` before matching, then joins results back to
    the full dataset. This avoids running the (expensive) fuzzy matcher
    multiple times on the same input string.

    Returns a DataFrame aligned with ``preprocessed_data`` (same row order).
    """
    unique_texts = (
        preprocessed_data[["Original", "Preprocessed_text"]]
        .drop_duplicates(subset=["Preprocessed_text"])
        .reset_index(drop=True)
    )

    unique_results = get_matches(
        unique_texts,
        ref_substance,
        threshold=threshold,
        max_per_match_id=max_per_match_id,
        only_first_match=only_first_match,
        lookup_table=lookup_table,
        progress_callback=progress_callback,
    )

    # Merge unique results back onto the full (potentially duplicated) data
    # using Preprocessed_text as the join key
    unique_results_with_key = unique_results.copy()
    unique_results_with_key["Preprocessed_text"] = unique_texts["Preprocessed_text"].values

    full_df = preprocessed_data[["Original", "Preprocessed_text"]].copy()
    merged = full_df.merge(
        unique_results_with_key.drop(columns=["Original"]),
        on="Preprocessed_text",
        how="left",
    )

    # Drop the join key from the output to match get_matches signature
    merged = merged.drop(columns=["Preprocessed_text"])
    return merged.reset_index(drop=True)


def fuzzy_match(text, ref_codes, threshold):
    """
    text : str (may be nan)
    ref_codes : sequence (list/Series) of choices
    threshold : float between 0 and 1 (e.g. 0.9)
    Returns (best_match, score) or (np.nan, np.nan)
    """
    if pd.isna(text):
        return np.nan, np.nan

    ref_codes_list = list(ref_codes)
    ref_codes_lower = [str(r).lower() for r in ref_codes_list]
    lower_to_orig = {ref_codes_lower[i]: ref_codes_list[i] for i in range(len(ref_codes_list))}

    text_str = str(text)
    split_pattern = r"\s*(?:\||;|/|,|\+|\band\b|\bmit\b|\bund\b)\s*"
    tokens = [tok.strip() for tok in re.split(split_pattern, text_str) if tok and tok.strip()]
    if not tokens:
        return np.nan, np.nan

    score_cutoff = int(threshold * 100)
    best_match = None
    best_score = -1

    for tok in tokens:
        tok_lower = tok.lower()

        if tok_lower in ref_codes_lower:
            return lower_to_orig[tok_lower], 100

        if "-" in tok_lower:
            parts = tok_lower.split("-")
            for i in range(len(parts), 0, -1):
                prefix = "-".join(parts[:i])
                if prefix in ref_codes_lower:
                    return lower_to_orig[prefix], 100

        if " " in tok_lower:
            first_word = tok_lower.split()[0]
            if first_word in ref_codes_lower:
                return lower_to_orig[first_word], 100

        match = process.extractOne(tok_lower, ref_codes_lower, scorer=fuzz.ratio, score_cutoff=score_cutoff)
        if match:
            matched_lower, score = match[0], match[1]
            if score > best_score:
                best_score = score
                best_match = lower_to_orig.get(matched_lower, matched_lower)

    if best_match is not None:
        return best_match, best_score
    else:
        return np.nan, np.nan
