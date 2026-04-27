"""
Temporal engine — date detection, normalization, and pre-aggregation.

This module is the single source of truth for *temporal semantics* in the
pipeline. It does three things:

1. Detects which columns are real dates (using both pandas inference and a
   tolerant string-parse vote on a sample). Mirrors `cleaning_engine` so
   downstream code can rely on the same set of columns.

2. Picks a *primary date column* for the dashboard — the one column the
   global time-scope filter will be anchored to (most often the order /
   transaction / event date).

3. Pre-aggregates time-series at three grains (day / month / year) for the
   browser, so the static export can offer Day/Month/Year toggling and
   date-range filtering without ever shipping row-level data. This preserves
   the privacy contract closed in PR #1 (no scatter / heatmap / raw_table
   row-level data leaves the server).

Output shapes
-------------
- `build_temporal_manifest(...)` returns:
    {
      "primary_date_column": "Order Date",
      "min": "2024-01-01",
      "max": "2026-04-30",
      "available_grains": ["day", "month", "year"],
      "default_grain": "month",
      "null_count": 12,
      "total_rows": 5000,
      "all_date_columns": ["Order Date", "Ship Date"]
    }

- `aggregate_by_grain(...)` returns a list of buckets with stable shape:
    [{"date": "2026-04", "value": 38000.0, "count": 250}, ...]

  `date` is always the bucket *start* in ISO form:
    day   -> "YYYY-MM-DD"
    month -> "YYYY-MM"
    year  -> "YYYY"
"""

from __future__ import annotations

from typing import Any, Iterable

import pandas as pd

ALL_GRAINS = ("day", "month", "year")

# Heuristic: a column is treated as a date if at least this fraction of its
# non-null string values successfully parse as a date. Same threshold as the
# cleaning engine so detection here matches what cleaning actually converted.
PARSE_THRESHOLD = 0.85

# Heuristic: a column name containing any of these substrings is *promoted*
# as a candidate for the primary date column.
PRIMARY_NAME_HINTS = (
    "order_date", "order date",
    "transaction_date", "transaction date", "transaction",
    "invoice_date", "invoice date", "invoice",
    "purchase_date", "purchase date", "purchase",
    "event_date", "event date", "event",
    "created_at", "created", "created_date",
    "date",
)


def detect_date_columns(df: pd.DataFrame) -> list[str]:
    """
    Return the list of columns that are real dates.

    A column qualifies if:
      - its dtype is already datetime64[*], OR
      - it is an object/string column where >= PARSE_THRESHOLD of its non-null
        values parse as dates with `format="mixed"`.

    The check is non-destructive — the caller is responsible for actually
    converting the columns (see `normalize_date_columns`).
    """
    detected: list[str] = []
    for col in df.columns:
        series = df[col]
        if pd.api.types.is_datetime64_any_dtype(series):
            detected.append(col)
            continue
        if series.dtype != "object":
            continue
        non_null = series.dropna()
        if non_null.empty:
            continue
        sample = non_null.astype(str).head(200)
        parsed = pd.to_datetime(sample, errors="coerce", format="mixed")
        if parsed.notna().mean() >= PARSE_THRESHOLD:
            detected.append(col)
    return detected


def normalize_date_columns(df: pd.DataFrame, columns: Iterable[str] | None = None) -> tuple[pd.DataFrame, list[str]]:
    """
    Return a copy of `df` with the given (or auto-detected) date columns
    converted to real `datetime64[ns]` with NaT for unparseable / null values.

    The cleaning engine already does a similar conversion; this function is
    idempotent and safe to call again. Returning the list of converted
    columns lets the caller record which fields ended up temporal.
    """
    cols = list(columns) if columns is not None else detect_date_columns(df)
    if not cols:
        return df, []

    out = df.copy()
    converted: list[str] = []
    for col in cols:
        if col not in out.columns:
            continue
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            converted.append(col)
            continue
        out[col] = pd.to_datetime(out[col], errors="coerce", format="mixed")
        converted.append(col)
    return out, converted


def pick_primary_date_column(
    df: pd.DataFrame,
    date_columns: Iterable[str],
    chart_x_fields: Iterable[str] | None = None,
) -> str | None:
    """
    Decide which date column should anchor the dashboard's global time scope.

    Priority order:
      1. The date column most frequently used as a chart's x-axis (it is the
         one the user already sees over time).
      2. The date column whose name matches a strong hint (`order_date`,
         `invoice_date`, ...).
      3. The date column with the widest non-null coverage.
      4. None, if there are no date columns.
    """
    cols = [c for c in date_columns if c in df.columns]
    if not cols:
        return None

    if chart_x_fields:
        usage: dict[str, int] = {c: 0 for c in cols}
        for x in chart_x_fields:
            if x in usage:
                usage[x] += 1
        best_used = max(usage.items(), key=lambda kv: kv[1])
        if best_used[1] > 0:
            return best_used[0]

    for col in cols:
        name = col.lower()
        if any(hint in name for hint in PRIMARY_NAME_HINTS):
            return col

    return max(cols, key=lambda c: int(df[c].notna().sum()))


def _grain_floor(series: pd.Series, grain: str) -> pd.Series:
    """Floor a datetime series to the start of its day / month / year bucket."""
    if grain == "day":
        return series.dt.floor("D")
    if grain == "month":
        return series.dt.to_period("M").dt.to_timestamp()
    if grain == "year":
        return series.dt.to_period("Y").dt.to_timestamp()
    raise ValueError(f"Unsupported grain: {grain!r}")


def _format_bucket(ts: pd.Timestamp, grain: str) -> str:
    if pd.isna(ts):
        return ""
    if grain == "day":
        return ts.strftime("%Y-%m-%d")
    if grain == "month":
        return ts.strftime("%Y-%m")
    if grain == "year":
        return ts.strftime("%Y")
    raise ValueError(f"Unsupported grain: {grain!r}")


def _coerce_jsonable(value: Any) -> Any:
    """pandas may give us numpy scalars — JSON-serialize cleanly."""
    if value is None or pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def aggregate_by_grain(
    df: pd.DataFrame,
    date_column: str,
    value_column: str | None,
    aggregation: str,
    grain: str,
) -> list[dict[str, Any]]:
    """
    Pre-aggregate a (date_column, value_column) pair at the given grain.

    Always returns a list of `{date, value, count}` records sorted by date
    ascending. Rows with NaT in `date_column` are dropped (they cannot be
    placed on a time axis). Aggregations supported: sum, average, median,
    min, max, count.

    `value_column=None` is allowed only when `aggregation == "count"`.
    """
    if grain not in ALL_GRAINS:
        raise ValueError(f"Unsupported grain: {grain!r}")
    if date_column not in df.columns:
        return []

    dates = pd.to_datetime(df[date_column], errors="coerce", format="mixed")
    mask = dates.notna()
    dates = dates[mask]
    if dates.empty:
        return []

    bucket = _grain_floor(dates, grain)

    if aggregation == "count" or value_column is None:
        grouped = bucket.groupby(bucket).size().sort_index()
        return [
            {"date": _format_bucket(idx, grain), "value": int(count), "count": int(count)}
            for idx, count in grouped.items()
        ]

    if value_column not in df.columns:
        return []

    values = pd.to_numeric(df[value_column], errors="coerce")[mask]
    frame = pd.DataFrame({"_bucket": bucket.values, "_value": values.values})
    frame = frame.dropna(subset=["_value"])
    if frame.empty:
        return []

    grouper = frame.groupby("_bucket", sort=True)
    if aggregation == "sum":
        agg = grouper["_value"].sum()
    elif aggregation in {"avg", "average", "mean"}:
        agg = grouper["_value"].mean()
    elif aggregation == "median":
        agg = grouper["_value"].median()
    elif aggregation == "min":
        agg = grouper["_value"].min()
    elif aggregation == "max":
        agg = grouper["_value"].max()
    else:
        raise ValueError(f"Unsupported aggregation: {aggregation!r}")

    counts = grouper.size()

    out: list[dict[str, Any]] = []
    for ts, value in agg.items():
        out.append({
            "date": _format_bucket(ts, grain),
            "value": _coerce_jsonable(value),
            "count": int(counts.loc[ts]),
        })
    return out


def build_data_by_grain(
    df: pd.DataFrame,
    date_column: str,
    value_column: str | None,
    aggregation: str,
) -> dict[str, list[dict[str, Any]]]:
    """Convenience: build all three grain buckets in one pass."""
    return {grain: aggregate_by_grain(df, date_column, value_column, aggregation, grain) for grain in ALL_GRAINS}


def build_temporal_manifest(
    df: pd.DataFrame,
    primary_date_column: str | None,
    all_date_columns: Iterable[str] | None = None,
) -> dict[str, Any] | None:
    """
    Build the top-level `temporal` block consumed by the frontend
    `TimeScopeContext`. Returns None when there is no usable date column.
    """
    if not primary_date_column or primary_date_column not in df.columns:
        return None

    series = pd.to_datetime(df[primary_date_column], errors="coerce", format="mixed")
    valid = series.dropna()
    if valid.empty:
        return None

    span_days = (valid.max() - valid.min()).days
    # Default grain heuristic: a tight range (a single year or less) defaults
    # to month; a multi-year range still defaults to month for readability;
    # only span < 60 days starts on day.
    if span_days < 60:
        default_grain = "day"
    elif span_days <= 365 * 5:
        default_grain = "month"
    else:
        default_grain = "year"

    return {
        "primary_date_column": primary_date_column,
        "min": valid.min().strftime("%Y-%m-%d"),
        "max": valid.max().strftime("%Y-%m-%d"),
        "available_grains": list(ALL_GRAINS),
        "default_grain": default_grain,
        "null_count": int(series.isna().sum()),
        "total_rows": int(len(series)),
        "all_date_columns": list(all_date_columns) if all_date_columns else [primary_date_column],
    }
