from __future__ import annotations

from typing import Any

import pandas as pd


DEFAULT_TOP_N = 20


def _clean_label(value: Any) -> str:
    if pd.isna(value):
        return "Missing"
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


def _add_card(results: dict, name: str, value: Any, value_format: str = "number") -> None:
    results["kpi_cards"].append({
        "title": name,
        "value": value,
        "format": value_format,
    })


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
    }

    for kpi in suggestions.get("kpis", []):
        f = kpi.get("formula_type")
        name = kpi.get("name", "Unnamed KPI")
        visual = kpi.get("visual") or kpi.get("chart_type") or "kpi_card"
        top_n = int(kpi.get("top_n") or kpi.get("limit") or DEFAULT_TOP_N)

        try:
            if f == "sum":
                column = kpi.get("column")
                if column in df.columns:
                    _add_card(results, name, float(_numeric_series(df, column).sum()), kpi.get("format", "number"))

            elif f == "average":
                column = kpi.get("column")
                if column in df.columns:
                    _add_card(results, name, float(_numeric_series(df, column).mean()), kpi.get("format", "number"))

            elif f == "min":
                column = kpi.get("column")
                if column in df.columns:
                    _add_card(results, name, float(_numeric_series(df, column).min()), kpi.get("format", "number"))

            elif f == "max":
                column = kpi.get("column")
                if column in df.columns:
                    _add_card(results, name, float(_numeric_series(df, column).max()), kpi.get("format", "number"))

            elif f == "count_rows":
                _add_card(results, name, int(len(df)), "number")

            elif f == "count_non_null":
                column = kpi.get("column")
                if column in df.columns:
                    _add_card(results, name, int(df[column].notna().sum()), "number")

            elif f == "count_distinct":
                column = kpi.get("column")
                if column in df.columns:
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
                    _add_chart_result(results, name, records, meta)

            elif f == "group_count":
                group_by = kpi.get("group_by")
                if group_by in df.columns:
                    records, meta = _group_count(df, group_by, visual, top_n)
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
