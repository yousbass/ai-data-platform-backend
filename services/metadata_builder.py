from services.data_profiler import profile_dataframe, detect_domain, detect_capabilities


# Substrings that indicate a "price"-like name is actually text or metadata,
# not a money amount. Mirrors data_profiler.TEXT_OVERRIDE_TOKENS.
_TEXT_OVERRIDE_TOKENS = [
    "color", "colour", "size", "currency", "shipping", "warranty",
    "return", "availability", "merchant", "source", "url", "flavor",
    "policy", "condition", "name",
]


def _correct_role_guess(column_info: dict) -> tuple[str, float]:
    """
    Correct basic role guesses before sending metadata to the local/Ollama/OpenAI layers.

    Deterministic guardrail. The profiler already does role inference; this is a
    last-mile correction for obvious cases. Critically, money/date guesses must
    respect the actual detected dtype — a text column named "prices.color" is not money.
    """
    name = str(column_info.get("name", "")).lower()
    detected_type = column_info.get("detected_type")
    role_guess = column_info.get("role_guess", "unknown")
    role_confidence = float(column_info.get("role_confidence", 0.5) or 0.5)

    # Trust the profiler when it gave a strong, dtype-consistent guess.
    if role_guess in (
        "date", "money", "brand", "color", "category", "size",
        "multi_value_list", "rating", "url", "product",
    ) and role_confidence >= 0.85:
        return role_guess, role_confidence

    if any(key in name for key in ["date", "time", "datetime", "created", "closed", "joined", "join", "added", "updated"]):
        return "date", 0.90

    if any(key in name for key in ["coffee", "product", "item", "sku", "menu"]):
        return "product", 0.90

    if any(key in name for key in ["cash", "payment", "card", "method"]):
        return "payment_method", 0.90

    if any(key in name for key in ["qty", "quantity", "units", "stock"]) or (
        "count" in name and detected_type == "number"
    ):
        return "quantity", 0.88

    money_kw = any(key in name for key in ["money", "amount", "sales", "revenue", "price", "cost", "salary"])
    text_override = any(tok in name for tok in _TEXT_OVERRIDE_TOKENS)
    if money_kw and not text_override and detected_type == "number":
        return "money", 0.90

    if any(key in name for key in ["region", "city", "country", "branch", "location"]):
        return "location", 0.85

    if any(key in name for key in ["department", "category", "supplier"]):
        return "category", 0.82

    if "status" in name:
        return "status", 0.82

    if any(key in name for key in [" id", "uuid", " code", "_id", "_code"]) or name in ("id", "code", "number", "no"):
        return "identifier", 0.78

    if detected_type == "number" and role_guess == "unknown":
        return "measure", max(role_confidence, 0.65)

    return role_guess, role_confidence


def _summarize_column(c: dict, corrected_role: str, corrected_confidence: float) -> dict:
    """
    Build the per-column dict that the AI sees. Includes real distribution data
    (sample values, top values, ranges) so the model can suggest dimension-aware
    charts (e.g., "products by color", "revenue by brand by month") instead of
    flying blind from column names alone.
    """
    out = {
        "name": c["name"],
        "detected_type": c.get("detected_type"),
        "role_guess": corrected_role,
        "role_confidence": corrected_confidence,
        "missing_percent": c.get("missing_percent"),
        "unique_count": c.get("unique_count"),
        "unique_ratio": c.get("unique_ratio"),
        "sample_values": c.get("sample_values", []),
    }
    top_values = c.get("top_values") or []
    if top_values:
        out["top_values"] = top_values
    mv = c.get("multi_value") or {}
    if mv.get("is_multi_value"):
        out["multi_value"] = {
            "separator": mv.get("separator"),
            "avg_token_count": mv.get("avg_token_count"),
            "max_token_count": mv.get("max_token_count"),
        }
    for k in ("min", "max", "mean", "median", "min_date", "max_date"):
        if c.get(k) is not None:
            out[k] = c[k]
    return out


def build_safe_metadata(df, business_context="auto", objective="auto-generate useful dashboard"):
    profile = profile_dataframe(df)
    domain = detect_domain(profile)
    capabilities = detect_capabilities(profile)

    enriched_columns = []
    for c in profile["columns"]:
        corrected_role, corrected_confidence = _correct_role_guess(c)
        enriched_columns.append(_summarize_column(c, corrected_role, corrected_confidence))

    # Hint columns the AI should consider for cross-tab analysis.
    suggested_dimensions = [
        c["name"] for c in enriched_columns
        if c["role_guess"] in ("brand", "color", "category", "size", "dimension", "multi_value_list", "location", "status", "product")
        and (c.get("unique_count") or 0) >= 2
    ]
    suggested_dates = [c["name"] for c in enriched_columns if c["role_guess"] == "date"]
    suggested_measures = [
        c["name"] for c in enriched_columns
        if c["role_guess"] in ("money", "measure", "quantity", "rating")
    ]
    multi_value_columns = [
        {
            "name": c["name"],
            "separator": c.get("multi_value", {}).get("separator"),
            "avg_token_count": c.get("multi_value", {}).get("avg_token_count"),
        }
        for c in enriched_columns if c.get("multi_value")
    ]

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
        "analysis_hints": {
            "suggested_date_columns": suggested_dates,
            "suggested_dimension_columns": suggested_dimensions,
            "suggested_measure_columns": suggested_measures,
            "multi_value_columns": multi_value_columns,
        },
        "columns": enriched_columns,
    }
