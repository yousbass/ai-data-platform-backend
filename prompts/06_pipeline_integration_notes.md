# Pipeline Integration Notes

## How `main.py` should call these five prompts

```
Stage 0  Load dataset → compute safe metadata (no raw rows)

Stage 1  Call Data Modeling Analyst (Prompt 1)
         → Validate JSON, parse formulas through DSL parser, run on local pandas
         → Save: 04_schema_suggestions_validated.json, 05_enriched_data.csv

Stage 2  Recompute metadata on enriched dataset → call KPI Strategy Consultant (Prompt 2)
         → Validate every column reference, dtype/aggregation compatibility
         → Save: 07_kpi_dashboard_suggestions_validated.json

Stage 3  Compute aggregated data_summary → call Dashboard Experience Architect (Prompt 3)
         → Validate every ref_id, enforce line-chart cap, resolve quality_flags
         → Save: 08_dashboard_design_plan.json

Stage 4  Build aggregated insight_payload from kpi_results + chart summaries
         → Call Dashboard Insight Writer (Prompt 4)
         → Validate every number appears in input, every chart_id resolves
         → Save: 09b_dashboard_insights.json
         → Inject headline_summary, insights, strategic_recommendations, data_story,
           and dashboard_callouts back into the dashboard JSON before rendering

Stage 5  Build full report_summary (with insight payload) → call Business Report Writer (Prompt 5)
         → Save: 12_ai_business_report.md
```

## Privacy guarantee

**No prompt ever sees raw rows.** Each Smart AI stage receives only:
- Stage 1 — schema metadata (column names, dtypes, semantic roles, missing %, unique_count)
- Stage 2 — same enriched schema
- Stage 3 — schema + validated suggestions + aggregated `data_summary` (top-N, ranges, share-of-total)
- Stage 4 — KPI values + chart summaries (top/bottom items + min/max/mean/median/total/growth/share)
- Stage 5 — the full aggregated report summary including the insight writer's output

Raw rows stay on the local machine and are processed by pandas/duckdb only.

## Validator responsibilities (the safety net behind every prompt)

**For Prompt 1 output:**
- JSON schema validation
- Formula parser: tokenize, check whitelist functions/operators, check column refs
- Dry-run each formula on a 100-row sample to catch type errors
- Reject and re-prompt if validation fails (with the specific error)

**For Prompt 2 output:**
- All `column` refs exist in enriched schema
- `(dtype, aggregation)` matrix check (no `sum` on strings, no `avg` on identifiers)
- Pie chart category-count check
- Line chart count cap

**For Prompt 3 output:**
- Every `ref_id` resolves to a validated KPI/chart/ranking
- Post-decision line-chart count ≤ 2 (or trend-domain exception)
- Cross-filter edges share a dimension
- Map readiness only true if geo column exists

**For Prompt 4 output (Insight Writer):**
- Strict JSON shape
- Every `supporting_chart_ids` entry exists in input
- Every `supporting_kpi_names` entry exists in input
- Every linked_insight_id in recommendations exists in `insights[]`
- Number-grounding pass: extract numbers from each insight body and check they appear in input `summary_stats` or `kpi_values` (with rounding tolerance)
- Length caps: ≤7 insights, ≤5 recommendations, ≤5 paragraphs, ≤22-word headline

**For Prompt 5 output:**
- Markdown sanity (sections present)
- Numeric values appear in input (regex-match a sample)
- Privacy note present in appendix

## Re-prompting strategy

When validation fails, send back a **structured error message** to the same model:

```
Your previous response failed validation:
- Error 1: {specific issue}
- Error 2: {specific issue}

Re-emit valid JSON addressing only these issues. Keep all valid items unchanged.
```

This is much cheaper and more reliable than restarting from scratch.

## Cost / latency tips

- Prompts 1, 2, 3, 4 are JSON → fast model tier
- Prompt 5 is the polished report → strong model tier
- Cache the schema metadata between Stage 1 and Stage 2 — only changes are the new derived columns
- Don't pass the full design plan to the report writer if it exceeds context — pass a compact view (sections + titles + decisions only)
- For Prompt 4, send only the top-N items per chart (5–10 max) and precomputed summary_stats — never the full series
