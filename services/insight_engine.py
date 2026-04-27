"""
Insight Engine — builds the aggregated payload for the Dashboard Insight Writer
(Smart AI #4) and injects the validated insight output back into the dashboard JSON.

Privacy guarantee:
  This module is the *only* path between local row-level data and the Insight Writer
  prompt. It deliberately summarises every chart series into top-N items, bottom-N
  items, and precomputed summary_stats. It never sends raw rows to the model.
"""
from __future__ import annotations

from typing import Any, Iterable

import math


_MAX_TOP_ITEMS = 7
_MAX_BOTTOM_ITEMS = 3


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


def _summarise_chart(chart: dict[str, Any], chart_index: int) -> dict[str, Any] | None:
    rows = chart.get("data") or []
    if not rows:
        return None

    x_field = chart.get("x_field") or (list(rows[0].keys())[0] if rows else None)
    y_field = chart.get("y_field") or (list(rows[0].keys())[-1] if rows else None)
    if not x_field or not y_field:
        return None

    numeric_rows = [
        {"label": r.get(x_field), "value": _safe_float(r.get(y_field))}
        for r in rows
        if _safe_float(r.get(y_field)) is not None
    ]
    if not numeric_rows:
        return None

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

    return {
        "chart_id": chart.get("id") or f"ch_{chart_index + 1:03d}",
        "title": chart.get("title", f"Chart {chart_index + 1}"),
        "chart_type": chart.get("type") or chart.get("visual"),
        "x_field": x_field,
        "y_field": y_field,
        "row_count": len(rows),
        "top_items": top_items,
        "bottom_items": bottom_items,
        "summary_stats": _summary_stats(rows, y_field),
    }


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
    for card in payload.get("kpi_values", []):
        v = card.get("value")
        if isinstance(v, (int, float)):
            numbers.add(f"{round(v, 2)}")
            numbers.add(f"{int(v)}" if float(v).is_integer() else f"{round(v, 1)}")
        cmp = card.get("comparison") or {}
        for k, val in cmp.items():
            if isinstance(val, (int, float)):
                numbers.add(f"{round(val, 2)}")
                numbers.add(f"{round(val, 1)}")

    for chart in payload.get("chart_summaries", []):
        for item in chart.get("top_items", []) + chart.get("bottom_items", []):
            v = item.get("value")
            if isinstance(v, (int, float)):
                numbers.add(f"{round(v, 2)}")
                numbers.add(f"{int(v)}" if float(v).is_integer() else f"{round(v, 1)}")
        stats = chart.get("summary_stats", {}) or {}
        for v in stats.values():
            if isinstance(v, (int, float)):
                numbers.add(f"{round(v, 2)}")
                numbers.add(f"{round(v, 1)}")
                numbers.add(f"{int(v)}" if float(v).is_integer() else f"{round(v, 1)}")

    # Allow trivial small integers (years, ranks, counts) without flagging
    for n in range(0, 50):
        numbers.add(str(n))
    for year in range(1990, 2050):
        numbers.add(str(year))
    return numbers


def validate_insight_output(
    output: dict[str, Any], payload: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate the Insight Writer output. Returns (cleaned_output, validation_log)."""
    log: dict[str, Any] = {"errors": [], "warnings": [], "removed": []}
    if not isinstance(output, dict):
        return {"headline_summary": "", "insights": [], "strategic_recommendations": [],
                "data_story": [], "dashboard_callouts": []}, {
            "errors": ["output is not a dict"], "warnings": [], "removed": []
        }

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

    # Headline summary: word cap
    if len(cleaned["headline_summary"].split()) > 30:
        log["warnings"].append("headline_summary exceeded 22 words (soft cap)")

    # Insights
    seen_insight_ids: set[str] = set()
    for i, insight in enumerate(output.get("insights", []) or []):
        if not isinstance(insight, dict):
            log["removed"].append({"kind": "insight", "index": i, "reason": "not a dict"})
            continue
        bad_charts = [
            c for c in (insight.get("supporting_chart_ids") or [])
            if c not in valid_chart_ids
        ]
        if bad_charts:
            log["warnings"].append({"insight": insight.get("id"), "unknown_chart_ids": bad_charts})
            insight["supporting_chart_ids"] = [
                c for c in (insight.get("supporting_chart_ids") or []) if c in valid_chart_ids
            ]
        bad_kpis = [
            k for k in (insight.get("supporting_kpi_names") or [])
            if k not in valid_kpi_names
        ]
        if bad_kpis:
            log["warnings"].append({"insight": insight.get("id"), "unknown_kpis": bad_kpis})
            insight["supporting_kpi_names"] = [
                k for k in (insight.get("supporting_kpi_names") or []) if k in valid_kpi_names
            ]

        # Number grounding (soft)
        body_numbers = _extract_numbers_from_text(insight.get("body", ""))
        ungrounded = [n for n in body_numbers if n not in grounding and n not in {f"{round(float(g), 1)}" for g in grounding if g.replace('.', '', 1).replace('-', '', 1).isdigit()}]
        if ungrounded:
            log["warnings"].append({
                "insight": insight.get("id"),
                "ungrounded_numbers": ungrounded[:5],
            })

        insight.setdefault("id", f"ins_{i + 1:03d}")
        if insight["id"] in seen_insight_ids:
            insight["id"] = f"ins_{i + 1:03d}_dup"
        seen_insight_ids.add(insight["id"])
        cleaned["insights"].append(insight)

    # Cap insights
    if len(cleaned["insights"]) > 7:
        log["warnings"].append("insights truncated to 7")
        cleaned["insights"] = cleaned["insights"][:7]

    # Recommendations: must link to existing insights
    for i, rec in enumerate(output.get("strategic_recommendations", []) or []):
        if not isinstance(rec, dict):
            continue
        bad_links = [
            link for link in (rec.get("linked_insight_ids") or [])
            if link not in seen_insight_ids
        ]
        if bad_links:
            log["warnings"].append({"rec": rec.get("id"), "unknown_insight_ids": bad_links})
            rec["linked_insight_ids"] = [
                link for link in (rec.get("linked_insight_ids") or [])
                if link in seen_insight_ids
            ]
        rec.setdefault("id", f"rec_{i + 1:03d}")
        cleaned["strategic_recommendations"].append(rec)
    if len(cleaned["strategic_recommendations"]) > 5:
        log["warnings"].append("recommendations truncated to 5")
        cleaned["strategic_recommendations"] = cleaned["strategic_recommendations"][:5]

    # Data story
    story = output.get("data_story") or []
    if isinstance(story, list):
        cleaned["data_story"] = [str(p).strip() for p in story if isinstance(p, str) and p.strip()][:5]

    # Callouts
    for callout in output.get("dashboard_callouts", []) or []:
        if isinstance(callout, dict) and callout.get("chart_id") in valid_chart_ids:
            cleaned["dashboard_callouts"].append(callout)
    if len(cleaned["dashboard_callouts"]) > 4:
        cleaned["dashboard_callouts"] = cleaned["dashboard_callouts"][:4]

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
