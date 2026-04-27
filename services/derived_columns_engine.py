"""
Deterministic derived-columns engine.

Runs AFTER the AI Schema Strategy stage and BEFORE the KPI/Dashboard AI stage.
Its job is to guarantee that the dataset has the dimensions needed for the
charts a real catalog dashboard should always show, regardless of what the
schema AI happened to suggest:

- Multi-value lists (colors, categories, sizes, ...) are exploded into
  `<name>_primary` (first token, used as a real dimension) and `<name>_count`
  (token count, used as a measure).
- When two date columns exist, a date-cohort block is added: time-between
  buckets, "was-changed" flag, and freshness bucket on the most recent date.
- When a price midpoint exists, a `price_tier` ordinal bucket is added.
- Empty/junk columns (`unnamed_*` near-null) are dropped.

The engine is purely deterministic, never sees raw rows beyond its own
in-process pandas frame, and emits a log that the orchestrator persists for
audit.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


_LIST_SEPARATORS = [",", ";", "|"]  # `/` excluded — almost always URLs
_MULTI_VALUE_AVG_THRESHOLD = 1.6  # avg tokens per non-null cell
_MULTI_VALUE_MAX_THRESHOLD = 2  # at least one cell must contain ≥2 tokens
_MULTI_VALUE_MAX_TOKENS = 25  # >25 tokens per cell ⇒ probably JSON/freeform
_MULTI_VALUE_MAX_TOKEN_LEN = 60  # primary token >60 chars ⇒ not a category label
_DATE_DETECT_THRESHOLD = 0.70
_JUNK_NAME_PATTERN = re.compile(r"^unnamed[_\s]?\d+$", re.IGNORECASE)
_JUNK_NULL_THRESHOLD = 0.95  # ≥95% null AND junk-named ⇒ drop

# Column names that are NEVER real multi-value categorical lists, even when
# they happen to contain commas (JSON blobs, URLs, IDs, freeform descriptions).
_MULTI_VALUE_NAME_BLOCKLIST = (
    "id", "key", "sku", "asin", "upc", "ean", "isbn",
    "url", "urls", "source", "image", "imageurl",
    "manufacturer_part", "websiteid", "vin",
    "description", "descriptions", "feature", "features",
    "review", "reviews", "quantity", "quantities",
    "raw", "json", "html", "xml", "merchants",
)

# Substrings that prove a value is JSON / not a categorical list.
_JSON_MARKERS = ('[{"', '{"', '":"', '":[', '":{')
# Substrings that prove a value is a URL.
_URL_MARKERS = ("http://", "https://", "www.")


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and np.isnan(value):
        return ""
    return str(value).strip()


def _is_datetime_column(series: pd.Series) -> bool:
    if pd.api.types.is_datetime64_any_dtype(series):
        return True
    sample = series.dropna().astype(str).head(200)
    if len(sample) == 0:
        return False
    parsed = pd.to_datetime(sample, errors="coerce", format="mixed")
    return bool(parsed.notna().mean() >= _DATE_DETECT_THRESHOLD)


def _coerce_datetime(series: pd.Series) -> pd.Series:
    """
    Parse to datetime64[ns] and strip any timezone so all date arithmetic in
    this engine uses a single tz-naive convention. Mixing tz-aware (e.g.,
    "2017-12-12T01:00:00Z") with a tz-naive `today()` would raise.
    """
    if pd.api.types.is_datetime64_any_dtype(series):
        out = series
    else:
        out = pd.to_datetime(series, errors="coerce", format="mixed", utc=True)
    # If tz-aware, convert to UTC then drop tz so the result is pure datetime64[ns].
    try:
        if getattr(out.dt, "tz", None) is not None:
            out = out.dt.tz_convert("UTC").dt.tz_localize(None)
    except (AttributeError, TypeError):
        pass
    return out


def _looks_like_categorical_list(sample: pd.Series, separator: str) -> bool:
    """
    Validate that a column actually holds short categorical tokens, not JSON
    blobs or URLs that just happen to contain commas.
    """
    for raw in sample.head(20):
        v = str(raw).strip()
        if not v:
            continue
        if any(marker in v for marker in _JSON_MARKERS):
            return False
        if v.startswith(("[", "{")):
            return False
        if any(marker in v.lower() for marker in _URL_MARKERS):
            return False
        # Any single token longer than the cap means the values aren't labels.
        for token in v.split(separator):
            t = token.strip()
            if len(t) > _MULTI_VALUE_MAX_TOKEN_LEN:
                return False
    return True


def _detect_multi_value(series: pd.Series, column_name: str) -> Tuple[bool, Optional[str]]:
    """Return (is_multi_value, separator) using token-count voting + content checks."""
    if not pd.api.types.is_object_dtype(series) and not pd.api.types.is_string_dtype(series):
        return False, None

    lower = column_name.lower()
    if any(token in lower for token in _MULTI_VALUE_NAME_BLOCKLIST):
        return False, None

    sample = series.dropna().astype(str).head(200)
    if len(sample) == 0:
        return False, None

    best_sep, best_avg, best_max = None, 0.0, 0
    for sep in _LIST_SEPARATORS:
        token_counts = sample.apply(lambda v: len([t for t in v.split(sep) if t.strip()]))
        avg = float(token_counts.mean())
        mx = int(token_counts.max())
        if avg > best_avg:
            best_sep, best_avg, best_max = sep, avg, mx

    if not (best_avg >= _MULTI_VALUE_AVG_THRESHOLD and best_max >= _MULTI_VALUE_MAX_THRESHOLD):
        return False, None
    if best_max > _MULTI_VALUE_MAX_TOKENS:
        return False, None
    if not _looks_like_categorical_list(sample, best_sep):
        return False, None
    return True, best_sep


def _explode_multi_value_column(
    df: pd.DataFrame, col: str, separator: str
) -> Dict[str, Any]:
    """
    Add `<col>_primary` and `<col>_count` columns in place.
    Returns a log entry describing what was done.
    """
    primary_col = f"{col}_primary"
    count_col = f"{col}_count"

    if primary_col in df.columns and count_col in df.columns:
        return {"column": col, "skipped": "derived columns already exist"}

    def _split_first(v: Any) -> Optional[str]:
        s = _safe_str(v)
        if not s:
            return None
        for token in s.split(separator):
            t = token.strip()
            if t:
                # Title-case to normalize "white", "WHITE", "White" into one bucket.
                return t.title()
        return None

    def _split_count(v: Any) -> int:
        s = _safe_str(v)
        if not s:
            return 0
        return sum(1 for t in s.split(separator) if t.strip())

    if primary_col not in df.columns:
        df[primary_col] = df[col].apply(_split_first)
    if count_col not in df.columns:
        df[count_col] = df[col].apply(_split_count).astype("int64")

    primary_unique = int(df[primary_col].nunique(dropna=True))
    count_max = int(df[count_col].max())
    return {
        "column": col,
        "separator": separator,
        "primary_column": primary_col,
        "count_column": count_col,
        "primary_unique_values": primary_unique,
        "max_token_count": count_max,
    }


def _bucket_days(days: float) -> Optional[str]:
    if days is None or (isinstance(days, float) and np.isnan(days)):
        return None
    d = float(days)
    if d < 0:
        return "Negative"
    if d == 0:
        return "Same day"
    if d <= 7:
        return "1-7 days"
    if d <= 30:
        return "1-4 weeks"
    if d <= 90:
        return "1-3 months"
    if d <= 365:
        return "3-12 months"
    if d <= 365 * 3:
        return "1-3 years"
    return "3+ years"


def _bucket_recency(days: float) -> Optional[str]:
    """Categorical freshness bucket for "how stale is the most recent activity"."""
    if days is None or (isinstance(days, float) and np.isnan(days)):
        return None
    d = float(days)
    if d < 0:
        return "Future-dated"
    if d <= 30:
        return "Fresh (≤30 days)"
    if d <= 90:
        return "Recent (1-3 months)"
    if d <= 365:
        return "Aging (3-12 months)"
    if d <= 365 * 2:
        return "Stale (1-2 years)"
    return "Very stale (2+ years)"


def _add_cohort_columns(
    df: pd.DataFrame, date_columns: List[str], log: Dict[str, Any]
) -> None:
    """
    When the dataset has at least two date columns, build:
      - days_<a>_to_<b>     (numeric, signed)
      - days_<a>_to_<b>_bucket (categorical)
      - was_<b>_changed     (bool — value differs from <a>)
    For the most recent date column, also add:
      - <col>_recency_days  (days from today, numeric)
      - <col>_recency_bucket (categorical freshness)
    """
    parsed: Dict[str, pd.Series] = {}
    for col in date_columns:
        if col not in df.columns:
            continue
        dt = _coerce_datetime(df[col])
        if dt.notna().mean() >= _DATE_DETECT_THRESHOLD:
            parsed[col] = dt

    if len(parsed) < 2:
        # Still emit recency for the single date column when present.
        if len(parsed) == 1:
            col, dt = next(iter(parsed.items()))
            today = pd.Timestamp("today").normalize()
            recency = (today - dt).dt.days
            recency_col = f"{col}_recency_days"
            recency_bucket = f"{col}_recency_bucket"
            if recency_col not in df.columns:
                df[recency_col] = recency.astype("Int64")
                log["recency_days_added"].append(recency_col)
            if recency_bucket not in df.columns:
                df[recency_bucket] = recency.apply(_bucket_recency)
                log["recency_bucket_added"].append(recency_bucket)
        return

    # Cohort: pick the two most-populated dates as the "creation" and "update".
    sorted_cols = sorted(parsed.keys(), key=lambda c: parsed[c].notna().sum(), reverse=True)
    a, b = sorted_cols[0], sorted_cols[1]
    # Heuristic: if names contain "added"/"created"/"opened" vs "updated"/"closed"/"seen",
    # use that ordering for clearer column names.
    name_order = sorted(
        [a, b],
        key=lambda c: (
            0 if any(k in c.lower() for k in ["added", "created", "opened", "joined"]) else 1,
            c,
        ),
    )
    a, b = name_order[0], name_order[1]

    diff_days_col = f"days_{a}_to_{b}"
    diff_bucket_col = f"days_{a}_to_{b}_bucket"
    changed_col = f"was_{b}_changed"

    diff = (parsed[b] - parsed[a]).dt.days
    if diff_days_col not in df.columns:
        df[diff_days_col] = diff.astype("Int64")
        log["cohort_days_added"].append(diff_days_col)
    if diff_bucket_col not in df.columns:
        df[diff_bucket_col] = diff.apply(_bucket_days)
        log["cohort_bucket_added"].append(diff_bucket_col)
    if changed_col not in df.columns:
        df[changed_col] = (diff.fillna(0) > 0)
        log["cohort_changed_flag_added"].append(changed_col)

    # Recency on the more recent column (b) so the freshness chart works.
    today = pd.Timestamp("today").normalize()
    recency = (today - parsed[b]).dt.days
    recency_col = f"{b}_recency_days"
    recency_bucket = f"{b}_recency_bucket"
    if recency_col not in df.columns:
        df[recency_col] = recency.astype("Int64")
        log["recency_days_added"].append(recency_col)
    if recency_bucket not in df.columns:
        df[recency_bucket] = recency.apply(_bucket_recency)
        log["recency_bucket_added"].append(recency_bucket)


def _add_price_tier(df: pd.DataFrame, log: Dict[str, Any]) -> None:
    """When a price midpoint column exists, add a `price_tier` ordinal bucket."""
    candidate = None
    for name in ("price_midpoint", "average_price", "avg_price", "price_amount_avg"):
        if name in df.columns and pd.api.types.is_numeric_dtype(df[name]):
            candidate = name
            break

    if not candidate or "price_tier" in df.columns:
        return

    edges = [-np.inf, 25, 50, 100, 250, 500, 1000, np.inf]
    labels = [
        "Under $25",
        "$25-50",
        "$50-100",
        "$100-250",
        "$250-500",
        "$500-1000",
        "$1000+",
    ]
    bucketed = pd.cut(df[candidate], bins=edges, labels=labels, include_lowest=True)
    df["price_tier"] = bucketed.astype("string").where(bucketed.notna(), None)
    log["price_tier_added"] = {
        "source_column": candidate,
        "edges": [None if not np.isfinite(e) else e for e in edges],
        "labels": labels,
    }


def _drop_junk_columns(df: pd.DataFrame, log: Dict[str, Any]) -> None:
    """Drop columns that are clearly junk (`unnamed_NN`, ≥95% null)."""
    to_drop: List[str] = []
    for col in list(df.columns):
        if not isinstance(col, str):
            continue
        if not _JUNK_NAME_PATTERN.match(col):
            continue
        null_share = float(df[col].isna().mean()) if len(df) else 1.0
        if null_share >= _JUNK_NULL_THRESHOLD:
            to_drop.append(col)
    if to_drop:
        df.drop(columns=to_drop, inplace=True)
        log["dropped_junk_columns"] = to_drop


def apply_derived_columns(df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    Apply the deterministic derived-column pass to an enriched dataframe.

    Returns:
        new_df, log
    """
    enriched = df.copy()
    log: Dict[str, Any] = {
        "dropped_junk_columns": [],
        "multi_value_explosions": [],
        "cohort_days_added": [],
        "cohort_bucket_added": [],
        "cohort_changed_flag_added": [],
        "recency_days_added": [],
        "recency_bucket_added": [],
        "price_tier_added": None,
    }

    _drop_junk_columns(enriched, log)

    # Detect dates first — needed for cohort fields.
    date_columns: List[str] = []
    for col in enriched.columns:
        s = enriched[col]
        if pd.api.types.is_numeric_dtype(s):
            continue  # Numeric columns are not dates here, even if name looks date-like.
        if _is_datetime_column(s):
            date_columns.append(col)

    # Multi-value explosion.
    for col in list(enriched.columns):
        if col.endswith("_primary") or col.endswith("_count"):
            continue  # already a derived column
        if not (pd.api.types.is_object_dtype(enriched[col]) or pd.api.types.is_string_dtype(enriched[col])):
            continue
        is_multi, sep = _detect_multi_value(enriched[col], col)
        if not is_multi or not sep:
            continue
        entry = _explode_multi_value_column(enriched, col, sep)
        log["multi_value_explosions"].append(entry)

    _add_cohort_columns(enriched, date_columns, log)
    _add_price_tier(enriched, log)

    log["final_column_count"] = int(len(enriched.columns))
    log["new_columns"] = [c for c in enriched.columns if c not in df.columns]
    return enriched, log
