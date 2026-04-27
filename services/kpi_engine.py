from __future__ import annotations

from typing import Any

import pandas as pd

from services.temporal_engine import (
    ALL_GRAINS,
    aggregate_by_grain,
    build_data_by_grain,
    build_temporal_manifest,
    detect_date_columns,
    pick_primary_date_column,
)


DEFAULT_TOP_N = 20

# KPI cards built from these formula types are date-sensitive: when the user
# changes the global time scope, the headline number should recompute. Each
# entry maps the formula_type to its `temporal_type` (how the browser should
# combine the per-bucket values for an arbitrary date range).
TEMPORAL_CARD_FORMULAS: dict[str, str] = {
    "sum": "sum",
    "average": "average",
    "min": "min",
    "max": "max",
    "count_rows": "sum",
    "count_non_null": "sum",
    "count_distinct": "distinct_unsupported",  # cannot recompute distincts from buckets
}

# For a time-scope-aware card we pre-aggregate at these grains. Day buckets
# would balloon the payload for multi-year datasets and are rarely needed for
# headline KPIs (the frontend slider snaps to month). Day buckets are still
# emitted for *charts* via `data_by_grain` so the user can drill in visually.
CARD_SERIES_GRAINS: tuple[str, ...] = ("month", "year")


def _clean_label(value: Any) -> str:
    if pd.isna(value):
        return "Missing"
    # datetime-like values would otherwise stringify as "2026-04-27 00:00:00"
    # which is noisy in non-temporal grouping contexts. We still preserve the
    # date in YYYY-MM-DD form so non-primary date columns don't render junk.
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    return str(value)


def _numeric_series(df: pd.DataFrame, column: str) -> pd.Series:
    return pd.to_numeric(df[column], errors="coerce")


def _datetime_series(df: pd.DataFrame, column: str) -> pd.Series:
    return pd.to_datetime(df[column], errors="coerce", format="mixed")


def _safe_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    return df.where(pd.notna(df), None).to_dict(orient="records")


def _limit_grouped(grouped: pd.DataFrame, sort_column: str, visual: str | None, top_n: int = DEFAULT_TOP_N) -> pd.DataFrame:
    """Sort and limit grouped data based on visual type."""
    visual = visual or "bar_chart"

    if visual in {"line_chart", "area_chart"}:
        return grouped.sort_values(grouped.columns[0], ascending=True)

    grouped = grouped.sort_values(sort_column, ascending=False)
    if visual in {"bar_chart", "horizontal_bar_chart", "pie_chart", "donut_chart", "treemap", "ranking_table"}:
        return grouped.head(top_n)

    return grouped


def _add_card(
    results: dict,
    name: str,
    value: Any,
    value_format: str = "number",
    *,
    temporal_type: str | None = None,
    series_by_grain: dict[str, list[dict[str, Any]]] | None = None,
    value_column: str | None = None,
) -> None:
    """
    Append a KPI card. When `temporal_type` and `series_by_grain` are
    supplied, the frontend can recompute the card's value for any date range
    by aggregating the per-bucket series client-side.
    """
    card: dict[str, Any] = {
        "title": name,
        "value": value,
        "format": value_format,
    }
    if temporal_type is not None:
        card["temporal_type"] = temporal_type
    if series_by_grain is not None:
        # Drop empty grains to keep the payload tight.
        non_empty = {grain: rows for grain, rows in series_by_grain.items() if rows}
        if non_empty:
            card["series_by_grain"] = non_empty
            if value_column:
                card["value_column"] = value_column
    results["kpi_cards"].append(card)


def _add_chart_result(results: dict, name: str, records: list[dict[str, Any]], chart_meta: dict[str, Any]) -> None:
    results["grouped_results"][name] = records
    results.setdefault("chart_metadata", {})[name] = chart_meta


def _group_numeric(df: pd.DataFrame, group_by: str, value_column: str, agg: str, visual: str | None, top_n: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    temp = df[[group_by, value_column]].copy()
    temp[group_by] = temp[group_by].map(_clean_label)
    temp[value_column] = pd.to_numeric(temp[value_column], errors="coerce")

    if agg == "sum":
        grouped = temp.groupby(group_by, dropna=False)[value_column].sum().reset_index()
    elif agg == "average":
        grouped = temp.groupby(group_by, dropna=False)[value_column].mean().reset_index()
    elif agg == "median":
        grouped = temp.groupby(group_by, dropna=False)[value_column].median().reset_index()
    elif agg == "min":
        grouped = temp.groupby(group_by, dropna=False)[value_column].min().reset_index()
    elif agg == "max":
        grouped = temp.groupby(group_by, dropna=False)[value_column].max().reset_index()
    else:
        raise ValueError(f"Unsupported numeric group aggregation: {agg}")

    grouped = _limit_grouped(grouped, value_column, visual, top_n)
    return _safe_records(grouped), {
        "chart_type": visual or "bar_chart",
        "x_field": group_by,
        "y_field": value_column,
        "aggregation": agg,
        "top_n": top_n,
    }


def _group_count(df: pd.DataFrame, group_by: str, visual: str | None, top_n: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    temp = df[[group_by]].copy()
    temp[group_by] = temp[group_by].map(_clean_label)
    grouped = temp.groupby(group_by, dropna=False).size().reset_index(name="Count")
    grouped = _limit_grouped(grouped, "Count", visual, top_n)
    return _safe_records(grouped), {
        "chart_type": visual or "bar_chart",
        "x_field": group_by,
        "y_field": "Count",
        "aggregation": "count",
        "top_n": top_n,
    }


def _histogram(df: pd.DataFrame, column: str, bins: int = 10) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    series = _numeric_series(df, column).dropna()
    if series.empty:
        return [], {"chart_type": "histogram", "x_field": "bin", "y_field": "Count", "source_column": column}

    cats = pd.cut(series, bins=bins)
    grouped = cats.value_counts().sort_index().reset_index()
    grouped.columns = ["bin", "Count"]
    grouped["bin"] = grouped["bin"].astype(str)
    return _safe_records(grouped), {
        "chart_type": "histogram",
        "x_field": "bin",
        "y_field": "Count",
        "source_column": column,
        "bins": bins,
    }


def _scatter(df: pd.DataFrame, x_col: str, y_col: str, color_col: str | None = None, size_col: str | None = None, limit: int = 500) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cols = [x_col, y_col]
    if color_col and color_col in df.columns:
        cols.append(color_col)
    if size_col and size_col in df.columns:
        cols.append(size_col)

    temp = df[cols].copy().head(limit)
    temp[x_col] = pd.to_numeric(temp[x_col], errors="coerce")
    temp[y_col] = pd.to_numeric(temp[y_col], errors="coerce")
    if size_col and size_col in temp.columns:
        temp[size_col] = pd.to_numeric(temp[size_col], errors="coerce")

    temp = temp.dropna(subset=[x_col, y_col])
    return _safe_records(temp), {
        "chart_type": "scatter_plot",
        "x_field": x_col,
        "y_field": y_col,
        "color_field": color_col,
        "size_field": size_col,
        "limit": limit,
    }


def _heatmap(df: pd.DataFrame, x_col: str, y_col: str, value_col: str | None = None, agg: str = "count", top_n: int = 20) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if value_col and value_col in df.columns and agg in {"sum", "average"}:
        temp = df[[x_col, y_col, value_col]].copy()
        temp[x_col] = temp[x_col].map(_clean_label)
        temp[y_col] = temp[y_col].map(_clean_label)
        temp[value_col] = pd.to_numeric(temp[value_col], errors="coerce")
        if agg == "sum":
            grouped = temp.groupby([x_col, y_col], dropna=False)[value_col].sum().reset_index(name="Value")
        else:
            grouped = temp.groupby([x_col, y_col], dropna=False)[value_col].mean().reset_index(name="Value")
    else:
        temp = df[[x_col, y_col]].copy()
        temp[x_col] = temp[x_col].map(_clean_label)
        temp[y_col] = temp[y_col].map(_clean_label)
        grouped = temp.groupby([x_col, y_col], dropna=False).size().reset_index(name="Value")

    # Keep heatmaps readable by limiting to top x/y labels by frequency.
    top_x = grouped.groupby(x_col)["Value"].sum().sort_values(ascending=False).head(top_n).index
    top_y = grouped.groupby(y_col)["Value"].sum().sort_values(ascending=False).head(top_n).index
    grouped = grouped[grouped[x_col].isin(top_x) & grouped[y_col].isin(top_y)]

    return _safe_records(grouped), {
        "chart_type": "heatmap",
        "x_field": x_col,
        "y_field": y_col,
        "value_field": "Value",
        "aggregation": agg,
        "top_n": top_n,
    }


def calculate_kpis(df: pd.DataFrame, suggestions: dict):
    """Calculate AI-suggested KPIs and chart-ready summaries locally on the private dataset."""
    results = {
        "kpi_cards": [],
        "grouped_results": {},
        "chart_metadata": {},
        "future_opportunities": suggestions.get("future_opportunities", []),
        "data_by_grain": {},
        "temporal_manifest": None,
    }

    # ---- Temporal preflight ----------------------------------------------
    # Detect date columns *now* so every KPI loop iteration can decide
    # whether it is operating on the dashboard's anchor date column. The
    # actual primary-column choice is informed by the AI's chart x-axis
    # picks, so we collect those first.
    chart_x_fields: list[str] = []
    for kpi in suggestions.get("kpis", []):
        gb = kpi.get("group_by")
        if isinstance(gb, str):
            chart_x_fields.append(gb)
    date_columns = detect_date_columns(df)
    primary_date_column = pick_primary_date_column(df, date_columns, chart_x_fields)
    temporal_manifest = build_temporal_manifest(df, primary_date_column, date_columns)
    results["temporal_manifest"] = temporal_manifest

    for kpi in suggestions.get("kpis", []):
        f = kpi.get("formula_type")
        name = kpi.get("name", "Unnamed KPI")
        visual = kpi.get("visual") or kpi.get("chart_type") or "kpi_card"
        top_n = int(kpi.get("top_n") or kpi.get("limit") or DEFAULT_TOP_N)

        # Helper closure: build per-grain card series for time-sensitive KPIs.
        # Returns (temporal_type, series_by_grain) or (None, None).
        def _card_temporal(formula: str, value_col: str | None) -> tuple[str | None, dict | None]:
            ttype = TEMPORAL_CARD_FORMULAS.get(formula)
            if not ttype or ttype == "distinct_unsupported":
                return None, None
            if not primary_date_column:
                return None, None
            # `count_rows` has no value column — bucket by row count.
            agg = "count" if formula == "count_rows" else (
                "sum" if formula in {"sum", "count_non_null"} else
                "average" if formula == "average" else
                formula  # min / max
            )
            col = None if formula == "count_rows" else value_col
            if formula != "count_rows" and (not col or col not in df.columns):
                return None, None
            series = {
                grain: aggregate_by_grain(df, primary_date_column, col, agg, grain)
                for grain in CARD_SERIES_GRAINS
            }
            if not any(series.values()):
                return None, None
            return ttype, series

        try:
            if f == "sum":
                column = kpi.get("column")
                if column in df.columns:
                    ttype, series = _card_temporal(f, column)
                    _add_card(
                        results, name, float(_numeric_series(df, column).sum()),
                        kpi.get("format", "number"),
                        temporal_type=ttype, series_by_grain=series, value_column=column,
                    )

            elif f == "average":
                column = kpi.get("column")
                if column in df.columns:
                    ttype, series = _card_temporal(f, column)
                    _add_card(
                        results, name, float(_numeric_series(df, column).mean()),
                        kpi.get("format", "number"),
                        temporal_type=ttype, series_by_grain=series, value_column=column,
                    )

            elif f == "min":
                column = kpi.get("column")
                if column in df.columns:
                    ttype, series = _card_temporal(f, column)
                    _add_card(
                        results, name, float(_numeric_series(df, column).min()),
                        kpi.get("format", "number"),
                        temporal_type=ttype, series_by_grain=series, value_column=column,
                    )

            elif f == "max":
                column = kpi.get("column")
                if column in df.columns:
                    ttype, series = _card_temporal(f, column)
                    _add_card(
                        results, name, float(_numeric_series(df, column).max()),
                        kpi.get("format", "number"),
                        temporal_type=ttype, series_by_grain=series, value_column=column,
                    )

            elif f == "count_rows":
                ttype, series = _card_temporal(f, None)
                _add_card(
                    results, name, int(len(df)), "number",
                    temporal_type=ttype, series_by_grain=series,
                )

            elif f == "count_non_null":
                column = kpi.get("column")
                if column in df.columns:
                    ttype, series = _card_temporal(f, column)
                    _add_card(
                        results, name, int(df[column].notna().sum()), "number",
                        temporal_type=ttype, series_by_grain=series, value_column=column,
                    )

            elif f == "count_distinct":
                column = kpi.get("column")
                if column in df.columns:
                    # Distinct counts cannot be combined from per-bucket pre-aggregations
                    # (you would over-count repeats), so we deliberately do NOT expose a
                    # series_by_grain here. The card stays as a global figure.
                    _add_card(results, name, int(df[column].nunique(dropna=True)), "number")

            elif f in ["group_sum", "group_average", "group_median", "group_min", "group_max"]:
                group_by = kpi.get("group_by")
                value_column = kpi.get("value_column")
                agg_map = {
                    "group_sum": "sum",
                    "group_average": "average",
                    "group_median": "median",
                    "group_min": "min",
                    "group_max": "max",
                }
                if group_by in df.columns and value_column in df.columns:
                    records, meta = _group_numeric(df, group_by, value_column, agg_map[f], visual, top_n)
                    # Time-series chart: anchor on the primary date column. Emit
                    # data_by_grain so the frontend can flip between Day/Month/Year.
                    if primary_date_column and group_by == primary_date_column:
                        results["data_by_grain"][name] = build_data_by_grain(
                            df, primary_date_column, value_column, agg_map[f],
                        )
                        meta["is_temporal"] = True
                    _add_chart_result(results, name, records, meta)

            elif f == "group_count":
                group_by = kpi.get("group_by")
                if group_by in df.columns:
                    records, meta = _group_count(df, group_by, visual, top_n)
                    if primary_date_column and group_by == primary_date_column:
                        results["data_by_grain"][name] = build_data_by_grain(
                            df, primary_date_column, None, "count",
                        )
                        meta["is_temporal"] = True
                    _add_chart_result(results, name, records, meta)

            elif f == "histogram":
                column = kpi.get("column")
                if column in df.columns:
                    records, meta = _histogram(df, column, int(kpi.get("bins") or 10))
                    _add_chart_result(results, name, records, meta)

            elif f == "scatter":
                x_col = kpi.get("x_column") or kpi.get("x")
                y_col = kpi.get("y_column") or kpi.get("y")
                color_col = kpi.get("color_column") or kpi.get("color")
                size_col = kpi.get("size_column") or kpi.get("size")
                if x_col in df.columns and y_col in df.columns:
                    records, meta = _scatter(df, x_col, y_col, color_col, size_col)
                    _add_chart_result(results, name, records, meta)

            elif f == "heatmap":
                x_col = kpi.get("x_column") or kpi.get("x")
                y_col = kpi.get("y_column") or kpi.get("y")
                value_col = kpi.get("value_column")
                agg = kpi.get("aggregation") or "count"
                if x_col in df.columns and y_col in df.columns:
                    records, meta = _heatmap(df, x_col, y_col, value_col, agg, top_n)
                    _add_chart_result(results, name, records, meta)

            else:
                results.setdefault("unsupported", []).append({
                    "kpi": name,
                    "formula_type": f,
                    "reason": "Unsupported formula_type in current local KPI engine.",
                })

        except Exception as e:
            results.setdefault("errors", []).append({"kpi": name, "error": str(e)})

    return results
