"""
Suggestion validation layer.

Purpose:
- External Smart AI can suggest schema changes and KPIs.
- The local engine must validate those suggestions before execution.
- This keeps calculations safe, explainable, and limited to supported operations.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

import pandas as pd
from services.formula_dsl import FormulaDSLError, validate_formula


SUPPORTED_SCHEMA_FORMULAS = {
    "multiply",
    "extract_date",
    "extract_month",
    "extract_hour",
    "extract_weekday",
}

SUPPORTED_KPI_FORMULAS = {
    "sum",
    "average",
    "min",
    "max",
    "count_rows",
    "count_non_null",
    "count_distinct",
    "group_sum",
    "group_average",
    "group_median",
    "group_min",
    "group_max",
    "group_count",
    "histogram",
    "scatter",
    "heatmap",
}

SUPPORTED_VISUALS = {
    "kpi_card",
    "line_chart",
    "area_chart",
    "bar_chart",
    "horizontal_bar_chart",
    "stacked_bar_chart",
    "pie_chart",
    "donut_chart",
    "table",
    "ranking_table",
    "pivot_table",
    "histogram",
    "scatter_plot",
    "bubble_chart",
    "heatmap",
    "treemap",
    "funnel_chart",
    "gauge",
    "map_ready",
}

CATEGORY_VISUALS = {
    "bar_chart",
    "horizontal_bar_chart",
    "stacked_bar_chart",
    "pie_chart",
    "donut_chart",
    "table",
    "ranking_table",
    "pivot_table",
    "treemap",
}

NUMERIC_CARD_FORMULAS = {"sum", "average", "min", "max"}
GROUP_NUMERIC_FORMULAS = {"group_sum", "group_average", "group_median", "group_min", "group_max"}


def _is_safe_column_name(name: Any) -> bool:
    """Allow normal business column names, but reject empty or suspicious names."""
    if not isinstance(name, str):
        return False
    cleaned = name.strip()
    if not cleaned or len(cleaned) > 80:
        return False
    # Reject names that look like code or formulas.
    if any(token in cleaned for token in [";", "--", "/*", "*/", "=", "<script", "import ", "eval("]):
        return False
    return bool(re.search(r"[A-Za-z0-9]", cleaned))


def _is_numeric_column(df: pd.DataFrame, column: str, threshold: float = 0.80) -> bool:
    if column not in df.columns:
        return False
    numeric = pd.to_numeric(df[column], errors="coerce")
    return bool(numeric.notna().mean() >= threshold)


def _is_datetime_column(df: pd.DataFrame, column: str, threshold: float = 0.70) -> bool:
    if column not in df.columns:
        return False
    if pd.api.types.is_datetime64_any_dtype(df[column]):
        return True
    sample = df[column].dropna().head(200)
    if len(sample) == 0:
        return False
    dates = pd.to_datetime(sample, errors="coerce", format="mixed")
    return bool(dates.notna().mean() >= threshold)


def _is_high_cardinality(df: pd.DataFrame, column: str, threshold: int = 50) -> bool:
    if column not in df.columns:
        return False
    return bool(df[column].nunique(dropna=True) > threshold)


def _column_exists(df: pd.DataFrame, column: Any) -> bool:
    return isinstance(column, str) and column in df.columns


# ---------------------------------------------------------------------------
# Identifier-column detection
# ---------------------------------------------------------------------------
# A `group_count` over a unique-identifier column (e.g. `product_record_id`,
# `asin`, `sku`, `uuid`) renders as an unreadable bar chart with hundreds of
# raw IDs on the x-axis. We detect these and convert them to a `count_distinct`
# headline card instead, which is what the AI almost always meant.
_IDENTIFIER_NAME_PATTERN = re.compile(
    r"(^|_)(id|record_id|asin|sku|upc|ean|isbn|guid|uuid|websiteid|vin|hash)$"
    r"|(_id$)"
    r"|(_record_id$)",
    re.IGNORECASE,
)


def _looks_like_identifier(df: pd.DataFrame, column: str) -> bool:
    """
    Heuristic: column is a unique-row identifier and should NOT be used as a
    chart dimension. True when EITHER the name matches an id-like pattern OR
    the column has very high uniqueness ratio on a non-trivial dataset.
    """
    if column not in df.columns:
        return False
    name = column.lower() if isinstance(column, str) else ""
    if name and _IDENTIFIER_NAME_PATTERN.search(name):
        return True
    n = len(df)
    if n < 20:
        return False
    nunique = int(df[column].nunique(dropna=True))
    return (nunique / n) >= 0.8 and nunique > 50


def validate_schema_suggestions(df: pd.DataFrame, suggestions: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Validate schema suggestions before local transformation.

    Returns:
        validated_suggestions, validation_log
    """
    validated = {"rename_columns": [], "derived_columns": []}
    log = {
        "schema_validation": {
            "accepted_renames": [],
            "rejected_renames": [],
            "accepted_derived_columns": [],
            "rejected_derived_columns": [],
        }
    }

    existing_columns = set(df.columns)
    planned_names = set(df.columns)

    for item in suggestions.get("rename_columns", []):
        old_name = item.get("old_name")
        new_name = item.get("new_name")

        if not _column_exists(df, old_name):
            log["schema_validation"]["rejected_renames"].append({"suggestion": item, "reason": "old_name does not exist."})
            continue
        if not _is_safe_column_name(new_name):
            log["schema_validation"]["rejected_renames"].append({"suggestion": item, "reason": "new_name is empty, too long, or unsafe."})
            continue
        if new_name in existing_columns and new_name != old_name:
            log["schema_validation"]["rejected_renames"].append({"suggestion": item, "reason": "new_name already exists."})
            continue

        validated["rename_columns"].append(item)
        planned_names.discard(old_name)
        planned_names.add(new_name)
        log["schema_validation"]["accepted_renames"].append(item)

    # Map original names to their approved renamed names so derived columns can reference either.
    rename_map = {item["old_name"]: item["new_name"] for item in validated["rename_columns"]}

    reverse_rename_map = {v: k for k, v in rename_map.items()}

    for item in suggestions.get("derived_columns", []):
        new_column = item.get("new_column") or item.get("name")
        formula = item.get("formula")
        formula_type = item.get("formula_type")
        inputs = item.get("input_columns") or item.get("source_columns") or []

        if not _is_safe_column_name(new_column):
            log["schema_validation"]["rejected_derived_columns"].append({"suggestion": item, "reason": "new_column is empty, too long, or unsafe."})
            continue
        if new_column in planned_names:
            log["schema_validation"]["rejected_derived_columns"].append({"suggestion": item, "reason": "new_column already exists or is already planned."})
            continue
        if not isinstance(inputs, list):
            log["schema_validation"]["rejected_derived_columns"].append({"suggestion": item, "reason": "input_columns/source_columns must be a list."})
            continue

        if formula:
            # Formula DSL validation happens on a temporary dataframe with approved renames applied,
            # because the transformation engine executes formulas after renaming.
            temp_df = df.rename(columns=rename_map)
            try:
                validate_formula(temp_df, formula)
            except FormulaDSLError as e:
                log["schema_validation"]["rejected_derived_columns"].append({"suggestion": item, "reason": f"Invalid formula DSL: {e}"})
                continue
            except Exception as e:
                log["schema_validation"]["rejected_derived_columns"].append({"suggestion": item, "reason": f"Formula validation failed: {e}"})
                continue

            validated_item = dict(item)
            validated_item["new_column"] = new_column
            validated_item["formula"] = formula
            if inputs:
                validated_item["source_columns"] = inputs
            validated["derived_columns"].append(validated_item)
            planned_names.add(new_column)
            log["schema_validation"]["accepted_derived_columns"].append(validated_item)
            continue

        if formula_type not in SUPPORTED_SCHEMA_FORMULAS:
            log["schema_validation"]["rejected_derived_columns"].append({"suggestion": item, "reason": f"Unsupported formula_type: {formula_type}."})
            continue

        # Validate against the original dataframe. If AI uses renamed columns, map back to old columns when needed.
        original_inputs = [reverse_rename_map.get(col, col) for col in inputs]

        if formula_type == "multiply":
            if len(inputs) != 2:
                log["schema_validation"]["rejected_derived_columns"].append({"suggestion": item, "reason": "multiply requires exactly two input columns."})
                continue
            if not all(_column_exists(df, col) for col in original_inputs):
                log["schema_validation"]["rejected_derived_columns"].append({"suggestion": item, "reason": "One or more input columns do not exist."})
                continue
            if not all(_is_numeric_column(df, col) for col in original_inputs):
                log["schema_validation"]["rejected_derived_columns"].append({"suggestion": item, "reason": "multiply requires two numeric columns."})
                continue

        elif formula_type in {"extract_date", "extract_month", "extract_hour", "extract_weekday"}:
            if len(inputs) != 1:
                log["schema_validation"]["rejected_derived_columns"].append({"suggestion": item, "reason": f"{formula_type} requires exactly one input column."})
                continue
            if not _column_exists(df, original_inputs[0]):
                log["schema_validation"]["rejected_derived_columns"].append({"suggestion": item, "reason": "Input column does not exist."})
                continue
            if not _is_datetime_column(df, original_inputs[0]):
                log["schema_validation"]["rejected_derived_columns"].append({"suggestion": item, "reason": f"{formula_type} requires a date/datetime column."})
                continue

        validated_item = dict(item)
        validated_item["new_column"] = new_column
        # Keep the AI-visible final names. The transformation engine runs after renaming, so final names are correct.
        validated["derived_columns"].append(validated_item)
        planned_names.add(new_column)
        log["schema_validation"]["accepted_derived_columns"].append(validated_item)

    if "_source" in suggestions:
        validated["_source"] = suggestions["_source"]

    return validated, log


def validate_kpi_suggestions(df: pd.DataFrame, suggestions: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Validate KPI and dashboard suggestions before local KPI calculation.

    Returns:
        validated_suggestions, validation_log
    """
    validated_kpis: List[Dict[str, Any]] = []
    accepted_names = set()
    log = {
        "kpi_validation": {
            "accepted_kpis": [],
            "rejected_kpis": [],
            "accepted_filters": [],
            "rejected_filters": [],
        }
    }

    for kpi in suggestions.get("kpis", []):
        name = kpi.get("name")
        formula_type = kpi.get("formula_type")
        visual = kpi.get("visual") or kpi.get("chart_type") or "kpi_card"

        if not _is_safe_column_name(name):
            log["kpi_validation"]["rejected_kpis"].append({"suggestion": kpi, "reason": "KPI name is empty, too long, or unsafe."})
            continue
        if name in accepted_names:
            log["kpi_validation"]["rejected_kpis"].append({"suggestion": kpi, "reason": "Duplicate KPI name."})
            continue
        if formula_type not in SUPPORTED_KPI_FORMULAS:
            log["kpi_validation"]["rejected_kpis"].append({"suggestion": kpi, "reason": f"Unsupported formula_type: {formula_type}."})
            continue
        if visual not in SUPPORTED_VISUALS:
            log["kpi_validation"]["rejected_kpis"].append({"suggestion": kpi, "reason": f"Unsupported visual: {visual}."})
            continue

        if formula_type in NUMERIC_CARD_FORMULAS:
            column = kpi.get("column")
            if not _column_exists(df, column):
                log["kpi_validation"]["rejected_kpis"].append({"suggestion": kpi, "reason": "Required column does not exist."})
                continue
            if not _is_numeric_column(df, column):
                log["kpi_validation"]["rejected_kpis"].append({"suggestion": kpi, "reason": f"{formula_type} requires a numeric column."})
                continue
            if visual != "kpi_card":
                kpi = dict(kpi)
                kpi["visual"] = "kpi_card"

        elif formula_type == "count_rows":
            if visual != "kpi_card":
                kpi = dict(kpi)
                kpi["visual"] = "kpi_card"

        elif formula_type in {"count_non_null", "count_distinct"}:
            column = kpi.get("column")
            if not _column_exists(df, column):
                log["kpi_validation"]["rejected_kpis"].append({"suggestion": kpi, "reason": "Required column does not exist."})
                continue
            if visual != "kpi_card":
                kpi = dict(kpi)
                kpi["visual"] = "kpi_card"

        elif formula_type in GROUP_NUMERIC_FORMULAS:
            group_by = kpi.get("group_by")
            value_column = kpi.get("value_column")
            if not _column_exists(df, group_by):
                log["kpi_validation"]["rejected_kpis"].append({"suggestion": kpi, "reason": "group_by column does not exist."})
                continue
            if not _column_exists(df, value_column):
                log["kpi_validation"]["rejected_kpis"].append({"suggestion": kpi, "reason": "value_column does not exist."})
                continue
            if not _is_numeric_column(df, value_column):
                log["kpi_validation"]["rejected_kpis"].append({"suggestion": kpi, "reason": f"{formula_type} requires a numeric value_column."})
                continue
            # Bug B (extended) — heatmap with one dimension: grouped numeric
            # KPIs only have a single `group_by` dimension, so a heatmap is
            # always malformed (it needs x AND y). Downgrade to bar_chart.
            # True 2D heatmaps must use formula_type=heatmap with explicit
            # x_column and y_column.
            if visual == "heatmap":
                kpi = dict(kpi)
                kpi["visual"] = "bar_chart"
                kpi["validation_note"] = (
                    f"Downgraded heatmap → bar_chart: {formula_type} produces a single dimension. "
                    "For a true 2D heatmap, use formula_type=heatmap with x_column and y_column."
                )
                visual = "bar_chart"
            if visual not in CATEGORY_VISUALS | {"line_chart", "area_chart"}:
                kpi = dict(kpi)
                kpi["visual"] = "bar_chart"
            if kpi.get("visual") in {"pie_chart", "donut_chart"} and _is_high_cardinality(df, group_by, threshold=6):
                kpi = dict(kpi)
                kpi["visual"] = "horizontal_bar_chart"
                kpi["validation_note"] = "Converted pie/donut to horizontal_bar_chart because the dimension has more than 6 categories."

        elif formula_type == "group_count":
            group_by = kpi.get("group_by")
            if not _column_exists(df, group_by):
                log["kpi_validation"]["rejected_kpis"].append({"suggestion": kpi, "reason": "group_by column does not exist."})
                continue
            # Bug A — identifier x-axis: a `group_count` over a unique row-id
            # column would render as hundreds of raw IDs. Convert to a
            # `count_distinct` headline card, which is what the AI usually
            # meant ("Unique X" / "Distinct X").
            if _looks_like_identifier(df, group_by):
                kpi = dict(kpi)
                kpi["formula_type"] = "count_distinct"
                kpi["column"] = group_by
                kpi.pop("group_by", None)
                kpi["visual"] = "kpi_card"
                kpi["validation_note"] = (
                    f"Converted group_count to count_distinct: `{group_by}` looks like a unique "
                    f"identifier (id-like name or ≥80% unique). Bar chart would have been "
                    f"unreadable; a headline `Distinct {group_by}` card is what the user wants."
                )
                validated_kpis.append(kpi)
                accepted_names.add(name)
                log["kpi_validation"]["accepted_kpis"].append(kpi)
                continue
            # Bug C — single-bucket dimension: a group_count over a column
            # with only 1 distinct value renders as a useless 1-bar chart
            # (e.g. all rows fall into "Very stale (2+ years)").
            nunique = int(df[group_by].nunique(dropna=True))
            if nunique < 2:
                log["kpi_validation"]["rejected_kpis"].append({
                    "suggestion": kpi,
                    "reason": (
                        f"group_by `{group_by}` has only {nunique} distinct value(s) — "
                        f"chart would be a single bar with no comparison."
                    ),
                })
                continue
            # Bug B — heatmap with one dimension: `group_count + visual=heatmap`
            # is malformed (heatmaps need x AND y). Downgrade to bar_chart so
            # the chart still renders meaningfully. True 2D heatmaps must use
            # `formula_type=heatmap` with explicit x_column and y_column.
            if visual == "heatmap":
                kpi = dict(kpi)
                kpi["visual"] = "bar_chart"
                kpi["validation_note"] = (
                    "Downgraded heatmap → bar_chart: group_count produces a single dimension. "
                    "For a true 2D heatmap, use formula_type=heatmap with x_column and y_column."
                )
                visual = "bar_chart"
            if visual not in CATEGORY_VISUALS | {"heatmap"}:
                kpi = dict(kpi)
                kpi["visual"] = "bar_chart"
            if kpi.get("visual") in {"pie_chart", "donut_chart"} and _is_high_cardinality(df, group_by, threshold=6):
                kpi = dict(kpi)
                kpi["visual"] = "horizontal_bar_chart"
                kpi["validation_note"] = "Converted pie/donut to horizontal_bar_chart because the dimension has more than 6 categories."

        elif formula_type == "histogram":
            column = kpi.get("column")
            if not _column_exists(df, column):
                log["kpi_validation"]["rejected_kpis"].append({"suggestion": kpi, "reason": "Histogram column does not exist."})
                continue
            if not _is_numeric_column(df, column):
                log["kpi_validation"]["rejected_kpis"].append({"suggestion": kpi, "reason": "histogram requires a numeric column."})
                continue
            kpi = dict(kpi)
            kpi["visual"] = "histogram"

        elif formula_type == "scatter":
            x_col = kpi.get("x_column") or kpi.get("x")
            y_col = kpi.get("y_column") or kpi.get("y")
            if not _column_exists(df, x_col) or not _column_exists(df, y_col):
                log["kpi_validation"]["rejected_kpis"].append({"suggestion": kpi, "reason": "scatter requires existing x and y columns."})
                continue
            if not _is_numeric_column(df, x_col) or not _is_numeric_column(df, y_col):
                log["kpi_validation"]["rejected_kpis"].append({"suggestion": kpi, "reason": "scatter requires numeric x and y columns."})
                continue
            kpi = dict(kpi)
            kpi["visual"] = "scatter_plot"

        elif formula_type == "heatmap":
            x_col = kpi.get("x_column") or kpi.get("x")
            y_col = kpi.get("y_column") or kpi.get("y")
            value_column = kpi.get("value_column")
            if not _column_exists(df, x_col) or not _column_exists(df, y_col):
                log["kpi_validation"]["rejected_kpis"].append({"suggestion": kpi, "reason": "heatmap requires existing x and y columns."})
                continue
            if value_column and not _column_exists(df, value_column):
                log["kpi_validation"]["rejected_kpis"].append({"suggestion": kpi, "reason": "heatmap value_column does not exist."})
                continue
            if value_column and not _is_numeric_column(df, value_column):
                log["kpi_validation"]["rejected_kpis"].append({"suggestion": kpi, "reason": "heatmap value_column must be numeric when provided."})
                continue
            kpi = dict(kpi)
            kpi["visual"] = "heatmap"

        validated_kpis.append(kpi)
        accepted_names.add(name)
        log["kpi_validation"]["accepted_kpis"].append(kpi)

    layout = suggestions.get("dashboard_layout", {}) or {}

    def keep_existing_kpi_names(names: Any) -> List[str]:
        if not isinstance(names, list):
            return []
        return [name for name in names if isinstance(name, str) and name in accepted_names]

    filters = []
    for item in layout.get("filters", []):
        if _column_exists(df, item):
            filters.append(item)
            log["kpi_validation"]["accepted_filters"].append(item)
        else:
            log["kpi_validation"]["rejected_filters"].append({"filter": item, "reason": "Filter column does not exist."})

    validated = {
        "kpis": validated_kpis,
        "dashboard_layout": {
            "top": keep_existing_kpi_names(layout.get("top", [])),
            "middle": keep_existing_kpi_names(layout.get("middle", [])),
            "bottom": keep_existing_kpi_names(layout.get("bottom", [])),
            "filters": sorted(set(filters)),
        },
        "future_opportunities": suggestions.get("future_opportunities", []),
    }

    if "_source" in suggestions:
        validated["_source"] = suggestions["_source"]

    return validated, log
