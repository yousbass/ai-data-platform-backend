"""
Insight Engine — builds the aggregated payload for the Dashboard Insight Writer
(Smart AI #4) and injects the validated insight output back into the dashboard JSON.

Privacy guarantee:
  This module is the *only* path between local row-level data and the Insight Writer
  prompt. It deliberately summarises every chart series into top-N items, bottom-N
  items, and precomputed summary_stats. It NEVER sends raw rows or row-level data
  to the model — chart types whose payloads are inherently row-level (scatter,
  bubble, heatmap point clouds, raw tables) are explicitly excluded from the payload
  and replaced with their precomputed summary_stats only.
"""
from __future__ import annotations

from typing import Any, Iterable

import math


_MAX_TOP_ITEMS = 7
_MAX_BOTTOM_ITEMS = 3

# Chart types whose .data payload is row-level (one row = one observation rather
# than one aggregated category). Sending top/bottom items from these would leak
# raw row values into the model. We summarise them with stats only.
_ROW_LEVEL_CHART_TYPES: frozenset[str] = frozenset({
    "scatter", "scatter_plot", "bubble", "bubble_chart",
    "heatmap_points", "table_raw", "raw_table",
})

# Hard limits on the validator
_MAX_INSIGHTS = 7
_MIN_INSIGHTS = 3
_MAX_RECS = 5
_MIN_RECS = 2
_MAX_STORY_PARAGRAPHS = 5
_MAX_CALLOUTS = 4
_HEADLINE_WORD_CAP = 22


# ---------------------------------------------------------------------------
# Aggregation helpers (run on already-aggregated dashboard data, never on raw rows)
# ---------------------------------------------------------------------------

def _safe_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if math.isnan(value) or math.isinf(value):
            return None
        return float(value)
    return None


def _numeric_values(rows: Iterable[dict], y_field: str) -> list[float]:
    out: list[float] = []
    for row in rows:
        v = _safe_float(row.get(y_field))
        if v is not None:
            out.append(v)
    return out


def _summary_stats(rows: list[dict], y_field: str) -> dict[str, Any]:
    values = _numeric_values(rows, y_field)
    if not values:
        return {"row_count": len(rows), "numeric_row_count": 0}

    sorted_vals = sorted(values)
    n = len(sorted_vals)
    total = sum(values)
    median = (
        sorted_vals[n // 2]
        if n % 2 == 1
        else (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2
    )

    stats: dict[str, Any] = {
        "row_count": len(rows),
        "numeric_row_count": n,
        "min": round(sorted_vals[0], 4),
        "max": round(sorted_vals[-1], 4),
        "mean": round(total / n, 4),
        "median": round(median, 4),
        "total": round(total, 4),
    }

    # First-to-last growth (useful for time series)
    first = _safe_float(rows[0].get(y_field)) if rows else None
    last = _safe_float(rows[-1].get(y_field)) if rows else None
    if first is not None and last is not None and first != 0:
        stats["growth_first_to_last_pct"] = round((last - first) / abs(first) * 100, 2)

    # Concentration metrics
    if total != 0:
        top_sorted = sorted(values, reverse=True)
        stats["top1_share_pct"] = round(top_sorted[0] / total * 100, 2)
        if n >= 3:
            stats["top3_share_pct"] = round(sum(top_sorted[:3]) / total * 100, 2)

    return stats


def _is_row_level_chart(chart: dict[str, Any]) -> bool:
    t = (chart.get("type") or chart.get("visual") or "").strip().lower()
    return t in _ROW_LEVEL_CHART_TYPES


def _summarise_chart(chart: dict[str, Any], chart_index: int) -> dict[str, Any] | None:
    rows = chart.get("data") or []
    if not rows:
        return None

    x_field = chart.get("x_field") or (list(rows[0].keys())[0] if rows else None)
    y_field = chart.get("y_field") or (list(rows[0].keys())[-1] if rows else None)
    if not x_field or not y_field:
        return None

    chart_type = chart.get("type") or chart.get("visual")
    summary: dict[str, Any] = {
        "chart_id": chart.get("id") or f"ch_{chart_index + 1:03d}",
        "title": chart.get("title", f"Chart {chart_index + 1}"),
        "chart_type": chart_type,
        "x_field": x_field,
        "y_field": y_field,
        "row_count": len(rows),
        "summary_stats": _summary_stats(rows, y_field),
    }

    # PRIVACY: row-level visualisations get stats-only — no top/bottom items because
    # those would expose individual observations to the model.
    if _is_row_level_chart(chart):
        summary["top_items"] = []
        summary["bottom_items"] = []
        summary["row_level"] = True
        summary["note"] = (
            "row-level visual: per-point items withheld for privacy; "
            "rely on summary_stats only"
        )
        return summary

    numeric_rows = [
        {"label": r.get(x_field), "value": _safe_float(r.get(y_field))}
        for r in rows
        if _safe_float(r.get(y_field)) is not None
    ]
    if not numeric_rows:
        # Still useful — return stats-only summary
        summary["top_items"] = []
        summary["bottom_items"] = []
        return summary

    sorted_desc = sorted(numeric_rows, key=lambda r: r["value"] or 0, reverse=True)
    sorted_asc = list(reversed(sorted_desc))

    top_items = [
        {"label": r["label"], "value": round(r["value"], 4)}
        for r in sorted_desc[:_MAX_TOP_ITEMS]
    ]
    bottom_items = [
        {"label": r["label"], "value": round(r["value"], 4)}
        for r in sorted_asc[:_MAX_BOTTOM_ITEMS]
        if r not in sorted_desc[:_MAX_TOP_ITEMS]
    ]

    summary["top_items"] = top_items
    summary["bottom_items"] = bottom_items
    return summary


def _summarise_kpis(kpi_cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for card in kpi_cards or []:
        out.append({
            "name": card.get("title") or card.get("name"),
            "value": card.get("value"),
            "format": card.get("format"),
            "unit": card.get("unit"),
            "comparison": card.get("comparison"),
        })
    return out


def build_insight_payload(
    dashboard_data: dict[str, Any],
    enriched_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the safe, aggregated payload sent to the Dashboard Insight Writer."""
    enriched_metadata = enriched_metadata or {}
    design_plan = dashboard_data.get("design_plan") or {}

    chart_summaries = []
    for i, chart in enumerate(dashboard_data.get("charts", []) or []):
        s = _summarise_chart(chart, i)
        if s is not None:
            chart_summaries.append(s)

    payload: dict[str, Any] = {
        "dataset_name": enriched_metadata.get("dataset_name")
            or dashboard_data.get("title", "Dataset"),
        "business_domain": enriched_metadata.get("business_domain")
            or enriched_metadata.get("domain")
            or "general",
        "audience": enriched_metadata.get("audience", "executive"),
        "row_count": enriched_metadata.get("row_count")
            or sum((c.get("row_count") or 0) for c in chart_summaries)
            or 0,
        "kpi_values": _summarise_kpis(dashboard_data.get("cards", []) or []),
        "chart_summaries": chart_summaries,
        "design_plan_excerpt": {
            "dashboard_title": design_plan.get("dashboard_title")
                or dashboard_data.get("title", ""),
            "objective": design_plan.get("dashboard_objective")
                or design_plan.get("objective", ""),
            "data_story": design_plan.get("data_story", []) or [],
            "section_titles": [
                s.get("section_name") or s.get("title", "")
                for s in (design_plan.get("sections") or [])
            ],
        },
    }
    return payload


# ---------------------------------------------------------------------------
# Validation of the model output
# ---------------------------------------------------------------------------

def _extract_numbers_from_text(text: str) -> set[str]:
    """Pull rounded numeric tokens out of a prose string for grounding checks."""
    import re

    if not isinstance(text, str):
        return set()
    tokens = re.findall(r"-?\d{1,3}(?:[,]\d{3})*(?:\.\d+)?|-?\d+(?:\.\d+)?", text)
    return {t.replace(",", "") for t in tokens}


def _grounding_universe(payload: dict[str, Any]) -> set[str]:
    """All numeric tokens the writer is allowed to use, derived from the payload."""
    numbers: set[str] = set()

    def _add(v: Any) -> None:
        f = _safe_float(v)
        if f is None:
            return
        numbers.add(f"{round(f, 2)}")
        numbers.add(f"{round(f, 1)}")
        if float(f).is_integer():
            numbers.add(f"{int(f)}")

    for card in payload.get("kpi_values", []):
        _add(card.get("value"))
        cmp = card.get("comparison") or {}
        if isinstance(cmp, dict):
            for v in cmp.values():
                _add(v)

    for chart in payload.get("chart_summaries", []):
        for item in (chart.get("top_items") or []) + (chart.get("bottom_items") or []):
            _add(item.get("value"))
        stats = chart.get("summary_stats", {}) or {}
        for v in stats.values():
            _add(v)

    # Allow trivial small integers (years, ranks, counts) without flagging
    for n in range(0, 100):
        numbers.add(str(n))
    for year in range(1990, 2050):
        numbers.add(str(year))
    return numbers


def _ungrounded_numbers(text: str, grounding: set[str]) -> list[str]:
    found = _extract_numbers_from_text(text)
    return [n for n in found if n not in grounding]


def validate_insight_output(
    output: dict[str, Any], payload: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Validate the Insight Writer output. Returns (cleaned_output, validation_log).

    Hard failures (added to log["errors"]; the caller may choose to re-prompt):
      - output is not a dict
      - fewer than _MIN_INSIGHTS valid insights
      - fewer than _MIN_RECS strategic_recommendations
      - any insight references a chart_id or kpi_name that does not exist in payload
      - any recommendation links to a non-existent insight id

    Soft warnings (added to log["warnings"]; not a re-prompt trigger):
      - headline > word cap
      - ungrounded numbers in body / headline / recs / story / callouts
      - lists truncated to caps
    """
    log: dict[str, Any] = {"errors": [], "warnings": [], "removed": []}
    if not isinstance(output, dict):
        log["errors"].append("output is not a dict")
        return {
            "headline_summary": "",
            "insights": [],
            "strategic_recommendations": [],
            "data_story": [],
            "dashboard_callouts": [],
        }, log

    valid_chart_ids = {c["chart_id"] for c in payload.get("chart_summaries", [])}
    valid_kpi_names = {
        c.get("name") for c in payload.get("kpi_values", []) if c.get("name")
    }
    grounding = _grounding_universe(payload)

    cleaned: dict[str, Any] = {
        "headline_summary": str(output.get("headline_summary", "")).strip(),
        "insights": [],
        "strategic_recommendations": [],
        "data_story": [],
        "dashboard_callouts": [],
        "coverage_notes": output.get("coverage_notes", {}),
    }

    # Headline summary
    if cleaned["headline_summary"]:
        if len(cleaned["headline_summary"].split()) > _HEADLINE_WORD_CAP:
            log["warnings"].append(
                f"headline_summary exceeded {_HEADLINE_WORD_CAP} words"
            )
        un = _ungrounded_numbers(cleaned["headline_summary"], grounding)
        if un:
            log["warnings"].append({"field": "headline_summary", "ungrounded_numbers": un[:5]})
    else:
        log["errors"].append("headline_summary is missing or empty")

    # Insights
    seen_insight_ids: set[str] = set()
    for i, insight in enumerate(output.get("insights", []) or []):
        if not isinstance(insight, dict):
            log["removed"].append({"kind": "insight", "index": i, "reason": "not a dict"})
            continue

        # Hard reference checks
        unknown_charts = [
            c for c in (insight.get("supporting_chart_ids") or [])
            if c not in valid_chart_ids
        ]
        unknown_kpis = [
            k for k in (insight.get("supporting_kpi_names") or [])
            if k not in valid_kpi_names
        ]
        if unknown_charts or unknown_kpis:
            log["errors"].append({
                "insight_index": i,
                "insight_id": insight.get("id"),
                "unknown_chart_ids": unknown_charts,
                "unknown_kpi_names": unknown_kpis,
            })
            insight["supporting_chart_ids"] = [
                c for c in (insight.get("supporting_chart_ids") or []) if c in valid_chart_ids
            ]
            insight["supporting_kpi_names"] = [
                k for k in (insight.get("supporting_kpi_names") or []) if k in valid_kpi_names
            ]

        # Number grounding across body + title
        for field in ("body", "title"):
            text = insight.get(field, "")
            un = _ungrounded_numbers(text if isinstance(text, str) else "", grounding)
            if un:
                log["warnings"].append({
                    "insight_index": i,
                    "field": field,
                    "ungrounded_numbers": un[:5],
                })

        insight.setdefault("id", f"ins_{i + 1:03d}")
        if insight["id"] in seen_insight_ids:
            insight["id"] = f"ins_{i + 1:03d}_dup"
        seen_insight_ids.add(insight["id"])
        cleaned["insights"].append(insight)

    if len(cleaned["insights"]) > _MAX_INSIGHTS:
        log["warnings"].append(f"insights truncated to {_MAX_INSIGHTS}")
        cleaned["insights"] = cleaned["insights"][:_MAX_INSIGHTS]
    if len(cleaned["insights"]) < _MIN_INSIGHTS:
        log["errors"].append(
            f"expected ≥{_MIN_INSIGHTS} insights, got {len(cleaned['insights'])}"
        )

    # Recommendations
    for i, rec in enumerate(output.get("strategic_recommendations", []) or []):
        if not isinstance(rec, dict):
            continue
        bad_links = [
            link for link in (rec.get("linked_insight_ids") or [])
            if link not in seen_insight_ids
        ]
        if bad_links:
            log["errors"].append({
                "rec_index": i,
                "rec_id": rec.get("id"),
                "unknown_insight_ids": bad_links,
            })
            rec["linked_insight_ids"] = [
                link for link in (rec.get("linked_insight_ids") or [])
                if link in seen_insight_ids
            ]
        # Number grounding on rec body
        for field in ("body", "title"):
            text = rec.get(field, "")
            un = _ungrounded_numbers(text if isinstance(text, str) else "", grounding)
            if un:
                log["warnings"].append({
                    "rec_index": i,
                    "field": field,
                    "ungrounded_numbers": un[:5],
                })
        rec.setdefault("id", f"rec_{i + 1:03d}")
        cleaned["strategic_recommendations"].append(rec)
    if len(cleaned["strategic_recommendations"]) > _MAX_RECS:
        log["warnings"].append(f"recommendations truncated to {_MAX_RECS}")
        cleaned["strategic_recommendations"] = cleaned["strategic_recommendations"][:_MAX_RECS]
    if len(cleaned["strategic_recommendations"]) < _MIN_RECS:
        log["errors"].append(
            f"expected ≥{_MIN_RECS} recommendations, got {len(cleaned['strategic_recommendations'])}"
        )

    # Data story
    story = output.get("data_story") or []
    if isinstance(story, list):
        cleaned["data_story"] = [
            str(p).strip() for p in story if isinstance(p, str) and p.strip()
        ][:_MAX_STORY_PARAGRAPHS]
        for j, paragraph in enumerate(cleaned["data_story"]):
            un = _ungrounded_numbers(paragraph, grounding)
            if un:
                log["warnings"].append({
                    "data_story_index": j,
                    "ungrounded_numbers": un[:5],
                })

    # Callouts
    for k, callout in enumerate(output.get("dashboard_callouts", []) or []):
        if not isinstance(callout, dict):
            continue
        if callout.get("chart_id") not in valid_chart_ids:
            log["errors"].append({
                "callout_index": k,
                "unknown_chart_id": callout.get("chart_id"),
            })
            continue
        text = callout.get("body") or callout.get("text", "")
        un = _ungrounded_numbers(text if isinstance(text, str) else "", grounding)
        if un:
            log["warnings"].append({
                "callout_index": k,
                "ungrounded_numbers": un[:5],
            })
        cleaned["dashboard_callouts"].append(callout)
    if len(cleaned["dashboard_callouts"]) > _MAX_CALLOUTS:
        cleaned["dashboard_callouts"] = cleaned["dashboard_callouts"][:_MAX_CALLOUTS]

    return cleaned, log


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def inject_insights_into_dashboard(
    dashboard_data: dict[str, Any], insight_output: dict[str, Any]
) -> dict[str, Any]:
    """
    Merge the Insight Writer output into the dashboard JSON consumed by the frontend.

    The frontend already knows how to render `insights` (list[str]) and the design plan,
    so we project the rich AI output into a backwards-compatible shape while also
    attaching the structured payload under `ai_insights` for richer renderers.
    """
    insights_list: list[str] = []
    if insight_output.get("headline_summary"):
        insights_list.append(insight_output["headline_summary"])
    for ins in insight_output.get("insights", []) or []:
        body = ins.get("body") or ins.get("title")
        if body:
            insights_list.append(body)

    if insights_list:
        dashboard_data["insights"] = insights_list

    # Attach structured payload for richer renderers (frontend may consume this directly)
    dashboard_data["ai_insights"] = insight_output

    # Recommendations into the report-side surface
    recs = [
        r.get("body") or r.get("title")
        for r in (insight_output.get("strategic_recommendations") or [])
        if r.get("body") or r.get("title")
    ]
    if recs:
        dashboard_data["recommendations"] = recs

    if insight_output.get("data_story"):
        # Mirror into design_plan.data_story so existing code paths surface it
        dashboard_data.setdefault("design_plan", {})
        dashboard_data["design_plan"]["data_story"] = insight_output["data_story"]

    return dashboard_data
