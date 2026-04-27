from services.data_profiler import profile_dataframe, detect_domain, detect_capabilities


def _correct_role_guess(column_info: dict) -> tuple[str, float]:
    """
    Correct basic role guesses before sending metadata to the local/Ollama/OpenAI layers.

    This is a deterministic guardrail for obvious cases where the basic profiler
    may misclassify a column, for example coffee_name being guessed as money.
    """
    name = str(column_info.get("name", "")).lower()
    detected_type = column_info.get("detected_type")
    role_guess = column_info.get("role_guess", "unknown")
    role_confidence = float(column_info.get("role_confidence", 0.5) or 0.5)

    if any(key in name for key in ["date", "time", "datetime", "created", "closed", "joined", "join"]):
        return "date", 0.90

    if any(key in name for key in ["coffee", "product", "item", "sku", "menu"]):
        return "product", 0.90

    if any(key in name for key in ["cash", "payment", "card", "method"]):
        return "payment_method", 0.90

    if any(key in name for key in ["qty", "quantity", "units", "stock", "count"]):
        return "quantity", 0.88

    if any(key in name for key in ["money", "amount", "sales", "revenue", "price", "cost", "salary"]):
        return "money", 0.90

    if any(key in name for key in ["region", "city", "country", "branch", "location"]):
        return "location", 0.85

    if any(key in name for key in ["department", "category", "supplier"]):
        return "category", 0.82

    if "status" in name:
        return "status", 0.82

    if any(key in name for key in ["id", "uuid", "code", "number", "no"]):
        return "identifier", 0.78

    if detected_type == "number" and role_guess == "unknown":
        return "measure", max(role_confidence, 0.65)

    return role_guess, role_confidence


def build_safe_metadata(df, business_context="auto", objective="auto-generate useful dashboard"):
    profile = profile_dataframe(df)
    domain = detect_domain(profile)
    capabilities = detect_capabilities(profile)

    corrected_columns = []
    for c in profile["columns"]:
        corrected_role, corrected_confidence = _correct_role_guess(c)
        corrected_columns.append({
            "name": c["name"],
            "detected_type": c["detected_type"],
            "role_guess": corrected_role,
            "role_confidence": corrected_confidence,
            "missing_percent": c["missing_percent"],
            "unique_count": c["unique_count"],
        })

    return {
        "privacy_mode": "metadata_only_no_raw_rows",
        "business_context": business_context,
        "objective": objective,
        "dataset_summary": {
            "row_count": profile["row_count"],
            "column_count": profile["column_count"],
            "duplicate_rows": profile["duplicate_rows"],
            **domain,
        },
        "capabilities": capabilities,
        "columns": corrected_columns,
    }
