# AI Data Platform Backend

Privacy-first analytics pipeline that turns a CSV/Excel into a dashboard-ready output by chaining four Smart AI stages and a Report Writer — all behind a strict aggregation boundary so **the model never sees raw rows**.

## Pipeline (5 AI stages)

```
Stage 0   Read file → profile → clean → safe metadata                       (local)
Stage 1   Smart AI #1 — Data Modeling Analyst         → derived columns      (prompts/01)
Stage 2   Smart AI #2 — KPI & Dashboard Strategy      → KPIs + chart roster  (prompts/02)
Stage 3   Smart AI #3 — Dashboard Experience Architect → layout, sections    (prompts/03)
Stage 4   Smart AI #4 — Dashboard Insight Writer       → headline, insights, (prompts/04)
                                                         recommendations,
                                                         data story, callouts
Stage 5   Report Writer — Executive Markdown report                          (prompts/05)
```

Each AI stage receives **only safe aggregated input**:

| Stage | What the model sees |
|------:|---------------------|
| 1 | column names, dtypes, semantic roles, missing %, unique counts |
| 2 | enriched schema (same shape, post-derivation) |
| 3 | schema + validated KPIs + aggregated chart-ready data |
| 4 | KPI values + per-chart top-N items + precomputed `summary_stats` (built by `services/insight_engine.py`) |
| 5 | the full report summary including the Insight Writer output |

Raw rows stay on the local machine and are processed only by pandas / DuckDB.

## Outputs

For every run a numbered set of artefacts is written to `output/<dataset>/`:

```
01_data_profile.json
02_cleaned_data.csv
02_cleaning_log.json
03_safe_metadata_*.json
04_schema_suggestions_validated.json
05_enriched_data.csv
05_transformation_log.json
06_safe_metadata_for_kpi_ai.json
07_kpi_dashboard_suggestions_validated.json
08_dashboard_design_plan.json
09_dashboard_data_with_design.json
09a_insight_payload.json                ← payload sent to Insight Writer
09b_dashboard_insights.json             ← Insight Writer output (validated)
09b_insight_validation_log.json
09c_dashboard_data_with_insights.json   ← final dashboard JSON for the frontend
10_dashboard_preview.html
11_report_summary.json
12_ai_business_report.md
```

## Quick start

```bash
pip install -r requirements.txt
python main.py input/index_2.csv
open output/<dataset>/10_dashboard_preview.html
```

## Enable OpenAI Smart AI

```bash
cp .env.example .env
# edit .env and set:
#   OPENAI_API_KEY=...
#   OPENAI_MODEL=gpt-5.2
python main.py input/index_2.csv
```

If OpenAI is unavailable, every Smart AI stage falls back automatically to either the local rule-based engine (`services/smart_ai_placeholder.py`) or, for the Insight Writer, a mechanical insight builder in `services/dashboard_engine.py::_make_insights`.

## Insight Writer in detail (Stage 4)

The new stage between dashboard generation and the report writer.

- **`services/insight_engine.py`**
  - `build_insight_payload(dashboard_data, enriched_metadata)` — projects each chart series into top-7 / bottom-3 items plus precomputed `summary_stats` (min, max, mean, median, total, growth_first_to_last_pct, top1_share_pct, top3_share_pct). This is the only path between local row data and the model.
  - `validate_insight_output(output, payload)` — strict-JSON shape, every `supporting_chart_ids` and `linked_insight_ids` must resolve, soft number-grounding pass, length caps (≤7 insights, ≤5 recs, ≤5 paragraphs).
  - `inject_insights_into_dashboard(dashboard_data, insight_output)` — merges the headline, insights, recommendations, data story and callouts back into the dashboard JSON.
- **`services/openai_smart_ai.py::generate_dashboard_insights(payload)`** — calls the Insight Writer prompt (`prompts/04_dashboard_insight_writer_prompt.md`).

The frontend reads `dashboard_data["insights"]`, `dashboard_data["recommendations"]`, and `dashboard_data["ai_insights"]` (the full structured payload) without any code changes — backwards compatible with the previous schema.

## Visual whitelist (Stage 2)

`prompts/02_kpi_strategy_consultant_prompt.md` and the in-code template now agree on the same broad whitelist: `kpi_card`, `line_chart`, `area_chart`, `bar_chart`, `horizontal_bar_chart`, `column_chart`, `pie_chart`, `donut_chart`, `stacked_bar_chart`, `scatter_plot`, `bubble_chart`, `histogram`, `box_plot`, `heatmap`, `treemap`, `funnel_chart`, `gauge`, `table`, `ranking_table`, `map_choropleth`, `map_points`. The prior bug that capped suggestions to `kpi_card | line_chart | bar_chart | table` is fixed.

## Important files

| Area | Path |
|---|---|
| Prompts | `prompts/01..05_*.md` (+ integration notes in `prompts/06_pipeline_integration_notes.md`) |
| Pipeline orchestrator | `main.py` |
| Smart AI connector | `services/openai_smart_ai.py` |
| Local fallback AI | `services/smart_ai_placeholder.py` |
| Profiler | `services/data_profiler.py` |
| Schema validator | `services/suggestion_validator.py` |
| KPI engine | `services/kpi_engine.py` |
| Dashboard engine | `services/dashboard_engine.py` |
| Insight engine (NEW) | `services/insight_engine.py` |
| Report engine | `services/report_engine.py` |
| HTML preview | `services/html_dashboard.py` |
