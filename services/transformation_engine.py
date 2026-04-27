import pandas as pd


def apply_schema_suggestions(df: pd.DataFrame, suggestions: dict):
    out = df.copy()
    log = {"renamed_columns": [], "created_columns": [], "rejected_suggestions": []}

    for item in suggestions.get("rename_columns", []):
        old, new = item["old_name"], item["new_name"]
        if old in out.columns and new not in out.columns:
            out = out.rename(columns={old: new})
            log["renamed_columns"].append({"old_name": old, "new_name": new})
        else:
            log["rejected_suggestions"].append({"suggestion": item, "reason": "Old column missing or new column already exists."})

    for item in suggestions.get("derived_columns", []):
        new = item.get("new_column") or item.get("name")
        formula = item.get("formula")
        ftype = item.get("formula_type")
        inputs = item.get("input_columns") or item.get("source_columns") or []

        if not new:
            log["rejected_suggestions"].append({"suggestion": item, "reason": "Missing derived column name."})
            continue

        if new in out.columns:
            log["rejected_suggestions"].append({"suggestion": item, "reason": "Derived column already exists."})
            continue

        try:
            if formula:
                try:
                    from services.formula_dsl import execute_formula
                except Exception as import_error:
                    log["rejected_suggestions"].append({
                        "suggestion": item,
                        "reason": f"Formula DSL engine is not available yet: {import_error}",
                    })
                    continue

                out[new] = execute_formula(out, formula)
                log["created_columns"].append({
                    "column": new,
                    "formula": formula,
                    "source_columns": inputs,
                    "engine": "formula_dsl",
                })
                continue

            if not ftype:
                log["rejected_suggestions"].append({"suggestion": item, "reason": "Missing formula or formula_type."})
                continue

            if not all(col in out.columns for col in inputs):
                log["rejected_suggestions"].append({"suggestion": item, "reason": "Required input columns not found after renaming."})
                continue

            if ftype == "multiply":
                out[new] = pd.to_numeric(out[inputs[0]], errors="coerce") * pd.to_numeric(out[inputs[1]], errors="coerce")
            elif ftype == "extract_date":
                out[new] = pd.to_datetime(out[inputs[0]], errors="coerce").dt.date.astype(str)
            elif ftype == "extract_month":
                out[new] = pd.to_datetime(out[inputs[0]], errors="coerce").dt.to_period("M").astype(str)
            elif ftype == "extract_quarter":
                dates = pd.to_datetime(out[inputs[0]], errors="coerce")
                out[new] = dates.dt.year.astype(str) + "-Q" + dates.dt.quarter.astype(str)
            elif ftype == "extract_year":
                out[new] = pd.to_datetime(out[inputs[0]], errors="coerce").dt.year
            elif ftype == "extract_hour":
                out[new] = pd.to_datetime(out[inputs[0]], errors="coerce").dt.hour
            elif ftype == "extract_weekday":
                out[new] = pd.to_datetime(out[inputs[0]], errors="coerce").dt.day_name()
            else:
                log["rejected_suggestions"].append({"suggestion": item, "reason": f"Unsupported formula_type {ftype}."})
                continue

            log["created_columns"].append({"column": new, "formula_type": ftype, "input_columns": inputs})
        except Exception as e:
            log["rejected_suggestions"].append({"suggestion": item, "reason": str(e)})
    return out, log
