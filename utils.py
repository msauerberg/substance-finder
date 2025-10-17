"""
Substance matching relies on
data wrangling using pandas and
fuzzy string machting with spacy and spaczz
"""
import re
import pandas as pd
import spacy
from spaczz.matcher import FuzzyMatcher
import numpy as np

# helper functions


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
    """removes words that are shorter than 3 characters
    """    
    return " ".join([word for word in text.split() if len(word) >= 3])


def find_5FU(text: str) -> str:
    """5FU and all the variants are abbrevations for
    Flourouracil. The function translate it to the actual substance name
    and catches common misspellings. In principle, fuzzy matching should be able
    to deal with misspellings but since this is a very common substance it might make sense
    to explicitly take care of them
    """    
    pattern = (
        r"5 fu|5fu|5-fu|5_fu|Fluoruracil|flourouracil|5-fluoruuracil|"
        r"5-fluoro-uracil|5-fluoruuracil|5-fluoruracil|floururacil|"
        r"5-fluorounacil|flourouraci|5-fluourouracil"
    )
    return re.sub(pattern, "fluorouracil", text, flags=re.IGNORECASE)


def calciumfolinat_to_folin(text: str) -> str:
    """This is again a common substance and depending on the threshold parameter
    for fuzzy matching it might be overlooked by fuzzy matching. This is why 
    this function translates it.
    """    
    return re.sub(r"\b(Calciumfolinat)\b", "folinsäure", text, flags=re.IGNORECASE)


def find_gemcitabin(text: str) -> str:
    """Another common substance that should be found, independend of the
    threshold parameter of the fuzzy matcher.
    """    
    return re.sub(
        r"Gemcibatin(?:e)?(?: Mono)?", "gemcitabin", text, flags=re.IGNORECASE
    )


def find_Paclitaxel_nab(text: str) -> str:
    """Sometimes the algorithm does not find nab-Paclitaxel since
    it puts "nab" in the fron. Here, we translate it to the correct
    substance name "Paclitaxel nab"
    """    
    return re.sub(
        r"\bnab[\s\-]?Paclitaxel\b", "Paclitaxel nab", text, flags=re.IGNORECASE
    )

def add_spaces(s: pd.Series) -> pd.Series:
    """
    Preprocess a pandas Series of strings to improve fuzzy matching:
    - Adds spaces around (), [], {}, commas, semicolons, colons
    - Separates letters from digits
    - Separates words from punctuation (except / and - so units like mg/kg and hyphenated words stay intact)
    - Keeps Unicode letters like ä, ü, ö intact
    - Normalizes whitespace
    - Lowercases text
    """
    def clean_text(text: str) -> str:
        if text is None:
            return ""
        text = str(text)

        # Add spaces around specific punctuation
        text = re.sub(r'([()\[\]{},:;])', r' \1 ', text)

        # Separate letters and digits (handles Unicode letters too)
        text = re.sub(r'(\d)(\D)', r'\1 \2', text)
        text = re.sub(r'(\D)(\d)', r'\1 \2', text)

        # Separate letters from non-word, non-space, excluding / and -
        text = re.sub(r'([^\w\s/\-])(\w)', r'\1 \2', text)
        text = re.sub(r'(\w)([^\w\s/\-])', r'\1 \2', text)

        # Collapse multiple spaces and lowercase
        text = re.sub(r'\s+', ' ', text).strip().lower()
        return text

    return s.map(clean_text)

# preprocessing using helper functions
def preprocess_data(col_with_free_text: pd.Series) -> pd.DataFrame:
    """Applies functions from above sequentially to input data
    """    
    df = prepare_free_text(col_with_free_text)
    processed = (
        df["Original"]
        .apply(find_5FU)
        .apply(find_gemcitabin)
        .apply(find_Paclitaxel_nab)
        .apply(calciumfolinat_to_folin)
        .apply(remove_short_words)
        .str.strip()
    )

    df["Preprocessed_text"] = add_spaces(processed)

    return df

# find matches with FuzzyMatcher from spaczz


def get_matches(
    preprocessed_data: pd.DataFrame,
    ref_substance: pd.Series,
    threshold: float = 0.85,
    max_per_match_id: int = 2,
    only_first_match: bool = False,
) -> pd.DataFrame:
    """
    Extracts substances from the input text.
    Each row is taken as one input text. In principle,
    there should be only one substance per row. However, 
    often there are more than one (e.g., "sub1; sub2 and sub3").
    The FuzzyMatcher is capable of finding more than one match per row.
    It stores matches in additional columns. Use "only_first_match" to 
    remove extra columns and return only the first matched substance per row.
    The max_per_match_id parameter defines how many matches are returned per word.
    For instance, a row might include "Interferon alpha" and the algorithm might
    find "Interferon alpha 2a", "Interferon alpha 2b" and "Peginterferon alpha"
    as potential matches. If the parameter is set to 2, it would only return 2 matches-
    the two best matches measured by the similarity score. Please note that the
    parameter controlls the number of matches per word. For instance, if
    the input is "sub1; sub2 and sub3" It can return two (if set to two) per ID,
    meaning the output can include up to 6 potential matches for this input.
    It is recommanded to use a rather high threshold parameter because substance
    names are often very similar to each other.
    """
    import spacy
    from spaczz.matcher import FuzzyMatcher
    import re

    # create blank German nlp as before
    nlp = spacy.blank("de")
    matcher = FuzzyMatcher(nlp.vocab)

    # Use partial_token fuzzy function and pass min_r derived from threshold.
    # min_r is expected as percentage (e.g. 85), so convert threshold (0.85) -> 85
    min_r_value = int(threshold * 100)

    for sub in ref_substance.dropna().astype(str):
        # one kwargs dict per pattern (we add one pattern -> one dict)
        matcher.add(sub, [nlp(sub)], kwargs=[{"fuzzy_func": "partial_token", "min_r": min_r_value}])

    results = []

    for _, row in preprocessed_data.iterrows():
        text = row["Preprocessed_text"]  # uses preprocessed text for FuzzyMatcher
        original = row["Original"]
        doc = nlp(text)
        matches = matcher(doc)

        # keep original filtering behaviour (ratio is 0-100)
        matches_filtered = [m for m in matches if m[3] >= threshold * 100]
        matches_sorted = sorted(matches_filtered, key=lambda x: x[3], reverse=True)

        result_row = {"Original": original}
        result_row["Preprocessed"] = text
        match_id_counts = {}
        match_idx = 1

        for match_id, start, end, ratio, _ in matches_sorted:
            count = match_id_counts.get(match_id, 0)
            if count >= max_per_match_id:
                continue

            result_row[f"Hit{match_idx}"] = doc[start:end].text
            result_row[f"Mapped_to{match_idx}"] = match_id
            result_row[f"Similarity{match_idx}"] = ratio / 100

            match_id_counts[match_id] = count + 1
            match_idx += 1

        results.append(result_row)

    out = pd.DataFrame(results)

    if only_first_match:
        cols_to_keep = ["Original", "Mapped_to1", "Similarity1"]
        available_columns = [col for col in cols_to_keep if col in out.columns]
        dta_col_selected = out[available_columns]
        dta_col_selected.columns = [
            re.sub(r"\d+$", "", col) for col in dta_col_selected.columns
        ]
        return dta_col_selected

    return out



def create_substance_service_var(
    col_with_substances: pd.Series,
    col_with_ref_substances: pd.Series,
    threshold: float = 0.85,
    max_per_match_id: int = 2,
    only_first_match: bool = False,
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



def test():
    print("this is just a test")
    return