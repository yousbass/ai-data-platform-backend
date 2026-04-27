import pandas as pd


def clean_dataframe(df: pd.DataFrame):
    cleaned = df.copy()
    log = {"original_rows": int(len(df)), "actions": []}

    before = len(cleaned)
    cleaned = cleaned.dropna(how="all")
    if before - len(cleaned):
        log["actions"].append({"action": "remove_empty_rows", "rows_removed": int(before - len(cleaned))})

    before = len(cleaned)
    cleaned = cleaned.drop_duplicates()
    if before - len(cleaned):
        log["actions"].append({"action": "remove_exact_duplicates", "rows_removed": int(before - len(cleaned))})

    for col in cleaned.select_dtypes(include=["object"]).columns:
        cleaned[col] = cleaned[col].astype(str).str.strip()
        cleaned[col] = cleaned[col].replace({"nan": None, "None": None, "": None})
    log["actions"].append({"action": "trim_text_columns", "columns": list(cleaned.select_dtypes(include=["object"]).columns)})

    converted_numeric = []
    converted_dates = []
    for col in cleaned.columns:
        if cleaned[col].dtype == "object":
            numeric = pd.to_numeric(cleaned[col], errors="coerce")
            if numeric.notna().mean() >= 0.90:
                cleaned[col] = numeric
                converted_numeric.append(col)
                continue
            dates = pd.to_datetime(cleaned[col], errors="coerce", format="mixed")
            if dates.notna().mean() >= 0.85:
                cleaned[col] = dates
                converted_dates.append(col)

    if converted_numeric:
        log["actions"].append({"action": "convert_numeric_columns", "columns": converted_numeric})
    if converted_dates:
        log["actions"].append({"action": "convert_date_columns", "columns": converted_dates})

    log["final_rows"] = int(len(cleaned))
    return cleaned, log
