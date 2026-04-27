"""Rule-based placeholder for external Smart AI.
Later this file can call OpenAI/Claude using the same JSON inputs/outputs.
"""


def _find_by_role(metadata, role):
    return [c["name"] for c in metadata["columns"] if c["role_guess"] == role]


def get_schema_suggestions(metadata):
    cols = metadata["columns"]
    renames = []
    derived = []
    name_map = {
        "date": "Date",
        "datetime": "Transaction Datetime",
        "money": "Amount",
        "cash_type": "Payment Method",
        "coffee_name": "Product"
    }
    existing = {c["name"] for c in cols}
    for c in cols:
        lower = c["name"].lower().strip()
        if lower in name_map and c["name"] != name_map[lower] and name_map[lower] not in existing:
            renames.append({"old_name": c["name"], "new_name": name_map[lower], "confidence": 0.92, "reason": "Clearer business-friendly dashboard name."})

    date_cols = _find_by_role(metadata, "date")
    if date_cols:
        # Prefer datetime if available
        source = "Transaction Datetime" if any(r.get("new_name") == "Transaction Datetime" for r in renames) else date_cols[0]
        derived += [
            {"new_column": "Day", "formula_type": "extract_date", "input_columns": [source], "priority": "high", "reason": "Allows daily trend analysis."},
            {"new_column": "Month", "formula_type": "extract_month", "input_columns": [source], "priority": "high", "reason": "Allows monthly aggregation."},
            {"new_column": "Hour", "formula_type": "extract_hour", "input_columns": [source], "priority": "medium", "reason": "Shows peak business hours."},
            {"new_column": "Weekday", "formula_type": "extract_weekday", "input_columns": [source], "priority": "medium", "reason": "Shows day-of-week patterns."}
        ]

    money_cols = _find_by_role(metadata, "money")
    quantity_cols = _find_by_role(metadata, "quantity")
    if money_cols and quantity_cols:
        derived.append({"new_column": "Total Value", "formula_type": "multiply", "input_columns": [quantity_cols[0], money_cols[0]], "priority": "high", "reason": "Combines quantity and amount to estimate value."})

    return {"rename_columns": renames, "derived_columns": derived}


def get_kpi_dashboard_suggestions(metadata):
    cols = [c["name"] for c in metadata["columns"]]
    roles = {c["name"]: c["role_guess"] for c in metadata["columns"]}
    money_cols = [n for n,r in roles.items() if r == "money"] or ["Amount"] if "Amount" in cols else []
    amount_col = money_cols[0] if money_cols else None
    dimension_cols = [n for n,r in roles.items() if r in ["product", "payment_method", "location", "dimension", "status"]]
    date_cols = [n for n,r in roles.items() if r == "date"]

    kpis = []
    if amount_col:
        kpis += [
            {"name": "Total Revenue", "formula_type": "sum", "column": amount_col, "visual": "kpi_card", "priority": "high"},
            {"name": "Average Transaction Value", "formula_type": "average", "column": amount_col, "visual": "kpi_card", "priority": "high"},
            {"name": "Number of Transactions", "formula_type": "count_rows", "visual": "kpi_card", "priority": "high"}
        ]
    if amount_col and "Day" in cols:
        kpis.append({"name": "Daily Revenue Trend", "formula_type": "group_sum", "group_by": "Day", "value_column": amount_col, "visual": "line_chart", "priority": "high"})
    if amount_col and "Hour" in cols:
        kpis.append({"name": "Revenue by Hour", "formula_type": "group_sum", "group_by": "Hour", "value_column": amount_col, "visual": "bar_chart", "priority": "medium"})
    if amount_col and "Weekday" in cols:
        kpis.append({"name": "Revenue by Weekday", "formula_type": "group_sum", "group_by": "Weekday", "value_column": amount_col, "visual": "bar_chart", "priority": "medium"})
    for dim in dimension_cols[:4]:
        if amount_col:
            kpis.append({"name": f"Revenue by {dim}", "formula_type": "group_sum", "group_by": dim, "value_column": amount_col, "visual": "bar_chart", "priority": "high"})
        kpis.append({"name": f"Transactions by {dim}", "formula_type": "group_count", "group_by": dim, "visual": "bar_chart", "priority": "medium"})

    return {
        "kpis": kpis,
        "dashboard_layout": {
            "top": [k["name"] for k in kpis if k["visual"] == "kpi_card"],
            "middle": [k["name"] for k in kpis if k["visual"] == "line_chart"],
            "bottom": [k["name"] for k in kpis if k["visual"] == "bar_chart"],
            "filters": [c for c in ["Month", "Day"] + dimension_cols[:3] if c in cols]
        }
    }
