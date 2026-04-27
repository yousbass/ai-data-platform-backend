# Pipeline Integration Notes

## How `main.py` should call these four prompts

```
Stage 0  Load dataset → compute safe metadata (no raw rows)
Stage 1  Call Data Modeling Analyst (Prompt 1)
         → Validate JSON, parse formulas through DSL parser, run on local pandas
         → Save: 05_modeling_plan.json, 06_enriched_dataset.parquet
Stage 2  Recompute metadata on enriched dataset → call KPI Strategy Consultant (Prompt 2)
         → Validate every column reference, dtype/aggregation compatibility
         → Save: 07_kpi_plan.json
Stage 3  Compute aggregated data_summary → call Dashboard Experience Architect (Prompt 3)
         → Validate every ref_id, enforce line-chart cap, resolve quality_flags
         → Save: 08_dashboard_design_plan.json
Stage 4  Execute KPIs/charts on local pandas → produce kpi_results, chart_summaries
         → Call Business Report Writer (Prompt 4)
         → Save: 09_business_report.md
```

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

**For Prompt 4 output:**
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

- Prompt 1, 2, 3 are JSON → use a fast model (Haiku-tier or Sonnet)
- Prompt 4 is the polished report → use a stronger model (Opus or Sonnet)
- Cache the schema metadata between Stage 1 and Stage 2 — only changes are the new derived columns
- Don't pass the full dataset_design_plan to the report writer if it exceeds context — pass a compact view (sections + titles + decisions only)
