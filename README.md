# AI Data Platform Backend MVP

This backend pipeline converts a CSV/Excel file into a dashboard-ready output while keeping raw row-level data local.

## What it does

1. Reads CSV or Excel.
2. Profiles the dataset locally.
3. Cleans low-risk issues locally.
4. Builds safe metadata.
5. Calls OpenAI Smart AI for schema suggestions when an API key is available.
6. Falls back to the local rule-based AI when OpenAI is not configured.
7. Applies approved/supported transformations locally.
8. Builds enriched metadata.
9. Calls OpenAI Smart AI for KPI/dashboard suggestions when available.
10. Calculates KPIs locally.
11. Generates dashboard JSON and HTML preview.
12. Generates a report summary JSON.

## Privacy model

Raw data stays local. The external AI receives only safe metadata such as column names, detected types, missing percentages, unique counts, and role guesses.

## First run

```bash
pip install -r requirements.txt
python main.py input/index_2.csv
open output/10_dashboard_preview.html
```

## Enable OpenAI Smart AI

Copy the example environment file:

```bash
cp .env.example .env
```

Open `.env` and add your API key:

```bash
OPENAI_API_KEY=your_api_key_here
OPENAI_MODEL=gpt-5.2
```

Then run again:

```bash
python main.py input/index_2.csv
```

If OpenAI is unavailable, the backend will automatically use the local fallback engine.

## Important files

- `main.py` — runs the full workload.
- `services/openai_smart_ai.py` — external Smart AI connector.
- `services/smart_ai_placeholder.py` — local fallback AI.
- `services/data_profiler.py` — local profiler and column role detector.
- `services/transformation_engine.py` — applies derived columns locally.
- `services/kpi_engine.py` — calculates KPI values locally.
- `services/dashboard_engine.py` — creates dashboard JSON.
- `services/html_dashboard.py` — creates dashboard HTML preview.
