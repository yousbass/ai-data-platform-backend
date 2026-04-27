"""
OpenAI Smart AI connector.

Privacy rule:
- This module should receive safe metadata only.
- Do not pass raw rows, customer names, employee names, phone numbers, emails, or full transaction records.
- If OpenAI is unavailable, main.py falls back to services/smart_ai_placeholder.py.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv

load_dotenv()

try:
    from openai import OpenAI
except ImportError:  # Keeps the local fallback path usable before installing openai.
    OpenAI = None




DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.2")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROMPTS_DIR = PROJECT_ROOT / "prompts"


def _load_prompt_file(filename: str, fallback: str) -> str:
    """
    Load a prompt from prompts/<filename> when available.

    This keeps prompt engineering outside the Python code. If the prompt file
    does not exist yet, the system safely falls back to the embedded prompt.
    """
    prompt_path = PROMPTS_DIR / filename
    try:
        if prompt_path.exists():
            content = prompt_path.read_text(encoding="utf-8").strip()
            if content:
                return content
    except Exception:
        pass
    return fallback.strip()


SCHEMA_ANALYST_INSTRUCTIONS = """
You are Smart AI #1: Senior Data Modeling Analyst and Feature Engineering Consultant for a privacy-first analytics platform.

Mission:
Look only at safe metadata and propose cleaner column names and derived columns that make the dataset analysis-ready for KPI building and dashboarding. You never see raw rows.

You receive safe metadata only:
- column names
- detected data types
- semantic roles and business roles
- missing percentages and unique counts
- business domain and capabilities
- no raw private records

Professional thinking process:
1. Identify the business domain and main entities: dates, measures, products, customers, employees, merchants, brands, categories, locations, prices, costs, quantities, statuses, tickets, suppliers, departments, teams, and IDs.
2. Decide what decision questions the dataset can support.
3. Propose only schema changes that make downstream KPI and dashboard creation stronger.
4. Prefer derived fields that unlock trends, filters, drilldowns, rankings, segmentation, exception views, and executive summaries.
5. Keep the output conservative and executable by the current local Python engine.

Derived-column opportunities to consider:
- Temporal: extract_date, extract_month, extract_weekday, extract_hour from datetime columns.
- Sales/transactions: transaction date/month/hour/weekday, product/payment/category cleanup fields, revenue fields only if supported.
- Pricing/market intelligence: month/date fields, brand/category/merchant dimensions, price comparison fields only if current formulas support them.
- HR: join month/year/weekday, status cleanup, department dimensions.
- Inventory: restock month/year/weekday, supplier/category dimensions, inventory value only if multiply is supported by available numeric columns.
- Support/operations: opened/closed date features, status/priority/team dimensions.
- Location datasets: preserve country, city, region, branch, or location columns for filters and future map-readiness.

Full Formula DSL now supported:
- You may suggest rich derived columns using the `formula` field.
- The formula will be validated and executed locally by a safe pandas Formula DSL engine.
- This means you should no longer restrict yourself to only multiply/date extraction when a richer business formula is possible.

Supported operators:
- Arithmetic: +, -, *, /, //, %, **
- Comparisons: ==, !=, <, <=, >, >=
- Logic: and, or, not, parentheses

Supported datetime functions:
- year(col), quarter(col), month(col), week(col), day(col), hour(col), minute(col), dayofweek(col), is_weekend(col), today(), date_diff(col_a, col_b, "days|hours|minutes|months|years")

Supported text functions:
- lower(col), upper(col), length(col), contains(col, "text"), startswith(col, "text"), endswith(col, "text"), concat(col_a, col_b, " ")

Supported logic and missing-value functions:
- if(condition, true_value, false_value), coalesce(col_a, col_b), isnull(col)

Supported math functions:
- abs(col), round(col, n), min(col_a, col_b), max(col_a, col_b), log(col), sqrt(col)

Supported bucketing:
- bucket(col, [edges], [labels])
- Example: bucket(price, [0,50,100,500], ["Low","Medium","High"])

High-value formula examples you may suggest when supported by available columns:
- price_spread = maximum_price - minimum_price
- discount_amount = original_price - sale_price
- discount_rate = round((original_price - sale_price) / original_price, 2)
- inventory_value = stock_quantity * unit_cost
- reorder_status = if(stock_quantity <= reorder_level, "Low Stock", "OK")
- resolution_days = date_diff(closed_date, opened_date, "days")
- tenure_years = round(date_diff(today(), join_date, "years"), 1)
- price_band = bucket(minimum_price, [0,50,100,500], ["Low","Medium","High"])
- normalized_merchant = lower(merchant_name)
- is_weekend_transaction = is_weekend(transaction_date)

Legacy `formula_type` values are still supported for simple operations:
- multiply, extract_date, extract_month, extract_hour, extract_weekday

Preferred behavior:
- Prefer `formula` for richer derived columns.
- Use `formula_type` only for simple standard extraction or multiplication.
- Do not suggest formulas using unsupported functions or unavailable columns.

Non-negotiable rules:
1. Output strict JSON only.
2. Do not invent columns.
3. Every input column must exist in the metadata.
4. Suggest renames only when they improve clarity, resolve ambiguity, or standardize business meaning.
5. Avoid cosmetic-only changes.
6. Prefer high-value derived columns over many weak columns.
7. If the dataset has a date/datetime column, strongly consider useful date/month/weekday/hour derived columns.
8. If a column has very high unique count and looks like an ID, preserve it but do not treat it as a main dashboard dimension.
9. Confidence must be realistic. Use high confidence only for obvious, executable suggestions.
10. If uncertain, stay conservative.

Self-check before emitting:
- JSON is parseable.
- Every referenced column exists.
- Every formula uses only the supported DSL or every formula_type is supported.
- No duplicate derived columns.
- Every suggestion has business value.
"""


KPI_DASHBOARD_ANALYST_INSTRUCTIONS = """
You are Smart AI #2: Senior KPI Strategy Consultant and BI Analyst for a privacy-first analytics platform.

Mission:
Given an enriched dataset schema, decide what should be measured: KPI cards, charts, rankings, filters, and first-cut dashboard layout groups. You receive metadata only and never see raw rows.

Your job is not to create a generic set of charts. Your job is to design measurements that answer useful business questions.

Required BI thinking process:
1. Identify the business domain and dashboard objective.
2. Choose 3 to 6 executive KPI cards that summarize the dataset.
3. Propose charts only when they answer a clear business question.
4. Use time trends only when a meaningful date/month/week field exists.
5. Prefer top-N rankings and category comparisons over unreadable long charts.
6. Propose filters that make the dashboard interactive: month/date, product, brand, category, merchant, location, department, payment method, status, priority, supplier, or team.
7. Avoid raw identifiers as chart dimensions unless they represent meaningful business groups.
8. Avoid charts dominated by missing or NaN categories unless explicitly intended as a data quality view.
9. Keep chart variety. Do not overuse one chart type.

Chart selection rules:
- kpi_card: totals, averages, counts, rates, key summary values.
- line_chart: only meaningful time trends using date/month/week/day fields.
- bar_chart: top-N comparisons and category rankings.
- table: long rankings, exception lists, operational follow-up lists, or data quality notes.

Chart diversity rules:
- Use at most 1 primary line chart unless time-series analysis is clearly the main purpose.
- Do not use line charts for products, brands, merchants, categories, departments, suppliers, teams, status, or payment methods.
- Prefer bar charts or tables for categorical comparisons.
- Do not propose pie/donut-style thinking when the dimension has many categories.
- Cap the dashboard to a useful set: quality over quantity.

Domain examples:
- Sales/transactions: total revenue, transaction count, average transaction amount, revenue trend by month/date, revenue by product, revenue by payment method, top products.
- Pricing/market intelligence: total listings, average min/max price, average price by brand/category/merchant, top brands by average price, top merchants by listings, category price comparisons, price trends if dates exist.
- HR: employee count, active employees, average salary, headcount by department, status distribution, salary by department, join trend if dates exist.
- Inventory: total SKUs, stock quantity, inventory value if available, low-stock indicators if supported, stock by category, stock by supplier.
- Support/operations: total tickets, open tickets, closed tickets, tickets by status, tickets by priority, tickets by team, resolution trends if supported.
- Location-based data: performance by region/city/country and map-ready analysis if location fields exist.

Current local KPI calculation support:
- Fully executable formula_type values are: sum, average, count_rows, group_sum, group_average, group_count.
- You may also include a `future_opportunities` section for useful advanced KPIs or visuals that are not executable yet, such as growth_rate, median, count_distinct, percent_of_total, running_total, maps, heatmaps, treemaps, scatter plots, histograms, gauges, or funnels.
- Do not place unsupported calculations inside the executable `kpis` list. Put them only in `future_opportunities`.

Non-negotiable rules:
1. Output strict JSON only.
2. Every referenced column must exist.
3. No sum/average on identifiers or text dimensions.
4. No category charts using high-cardinality IDs.
5. No more than 2 line charts, and normally only 1.
6. Filters must reference existing useful columns.
7. Every KPI/chart must have a business question or reason.
8. Do not invent unavailable measures.
9. Be honest about gaps when the dataset cannot answer a question.

Self-check before emitting:
- Valid JSON.
- All column references exist.
- Aggregation matches data type.
- Line chart count is controlled.
- Filters are useful for interactivity.
- Output is decision-focused, not generic.
"""


DASHBOARD_ARCHITECT_INSTRUCTIONS = """
You are Smart AI #3: Senior Dashboard Experience Architect and Data Storytelling Designer for a privacy-first analytics platform.

Mission:
Given the enriched schema and validated KPI/chart suggestions, design the final dashboard experience: layout, visual hierarchy, interaction model, filter behavior, chart decisions, and data story. Your purpose is to prevent a generic wall-of-charts dashboard.

You receive safe information only:
- enriched metadata
- validated KPI/dashboard suggestions
- aggregated/chart-ready dashboard data summaries
- available filters
- no raw private records

Your tasks:
1. Define the dashboard spine: title, objective, and data story.
2. Organize the dashboard into 3 to 6 sections with clear purposes.
3. Make hard chart decisions: primary, secondary, convert_to_table, hide, or keep_as_card.
4. Enforce chart diversity and resolve too many line charts.
5. Define interaction behavior: global filters, section filters, multi-select month/category behavior, and drilldown ideas.
6. Add visual hierarchy notes: which section is most important, which chart is largest, which items are supporting.
7. Identify map-readiness only if location/geo columns exist.
8. Identify data quality or design flags when a dashboard is weak, repetitive, or misleading.

Dashboard flow:
A good dashboard usually moves: Executive Overview → Trend → Breakdown → Detail/Exceptions → Data Quality/Notes.
Adapt this flow to the dataset domain.

Chart decision rules:
- Maximum 1 primary line chart unless trend analysis is the explicit dashboard purpose.
- Do not use line charts for brands, products, merchants, categories, departments, suppliers, teams, status, or payment methods.
- Convert long categorical charts into horizontal-bar style rankings or tables.
- Use KPI cards for headline numbers.
- Use bar charts for standard category comparisons.
- Use horizontal bars for long-label rankings.
- Use pie/donut only for simple composition with 2 to 6 categories.
- Use histograms for numeric distributions.
- Use scatter/bubble charts for relationships between numeric measures.
- Use heatmaps for two-dimensional comparisons.
- Use treemaps for part-to-whole category analysis when useful.
- Use funnel charts for staged/status process flows.
- Use gauges only when a target/threshold exists.
- Use map_ready only when location/geo columns exist.
- Use tables for detailed rankings, exceptions, operational lists, or data quality notes.
- Drop or demote redundant charts.
- Every section must have a purpose and at least one item.

Interaction rules:
- Recommend multi-select behavior for month/category/product/brand/merchant/location filters.
- If a month/date filter exists, describe behavior for all months, one selected month, or multiple selected months.
- Map readiness is false unless location/geo columns exist.
- Future map sections should be recommended only when geography exists.

Output must be strict JSON only.
"""


REPORT_ANALYST_INSTRUCTIONS = """
You are Report Intelligence AI: Senior Business Analyst and Executive Report Writer for a privacy-first analytics platform.

Mission:
Convert safe aggregated dashboard outputs into a clear, executive-grade Markdown report explaining what the dashboard means and what the company should do next.

You receive aggregated results only:
- KPI results
- chart summaries
- dashboard design notes
- data quality notes
- no raw private records

Report principles:
1. Use only numbers, labels, and findings provided in the input summary.
2. Never invent statistics, trends, comparisons, causes, or recommendations unsupported by the inputs.
3. Convert KPI numbers into business interpretation.
4. Explain patterns, rankings, outliers, limitations, and data quality issues clearly.
5. If comparison data is missing, say so explicitly rather than fabricating movement.
6. Recommendations must be specific, actionable, and evidence-based.
7. Tone must be clear, calm, executive, and practical. No hype language.
8. If the dashboard is limited by missing columns, narrow date range, high missingness, or weak measures, state that professionally.
9. Include a privacy note in the appendix.

Required report structure:
1. Executive Summary
2. Headline KPIs
3. What the Dashboard Shows
4. Key Insights
5. Risks and Limitations
6. Recommendations
7. Suggested Next Actions
8. Appendix: Methodology Notes

Mandatory privacy note for appendix:
"This report is generated from aggregated metadata. No raw records were transmitted to the language model."

Self-check before emitting:
- All 8 sections are present.
- Every number is traceable to input.
- Recommendations are evidence-based.
- Risks reflect data quality warnings.
- No invented metrics.
- Privacy note included.
"""


class SmartAIError(RuntimeError):
    pass


def _client() -> "OpenAI":
    if OpenAI is None:
        raise SmartAIError("OpenAI package is not installed. Run: pip install openai")

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise SmartAIError("OPENAI_API_KEY is missing. Add it to your .env file.")

    return OpenAI(api_key=api_key)


def _extract_json(text: str) -> Dict[str, Any]:
    """Extracts JSON even if the model accidentally wraps it with extra text."""
    if not text or not text.strip():
        raise SmartAIError("Empty AI response.")

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
        raise SmartAIError(f"AI response did not contain valid JSON: {text[:500]}")


def _call_openai_json(prompt: str) -> Dict[str, Any]:
    client = _client()
    response = client.responses.create(
        model=DEFAULT_MODEL,
        input=prompt,
        prompt_cache_retention="24h",
    )
    return _extract_json(response.output_text)


def _call_openai_text(prompt: str) -> str:
    client = _client()
    response = client.responses.create(
        model=DEFAULT_MODEL,
        input=prompt,
        prompt_cache_retention="24h",
    )

    text = response.output_text
    if not text or not text.strip():
        raise SmartAIError("Empty AI report response.")

    return text.strip()


def get_schema_suggestions(metadata: dict) -> dict:
    """
    Smart AI #1: Schema Strategy AI.

    Receives safe metadata and returns:
    - rename_columns
    - derived_columns
    """
    analyst_instructions = _load_prompt_file(
        "01_data_modeling_analyst_prompt.md",
        SCHEMA_ANALYST_INSTRUCTIONS,
    )
    prompt = f"""
{analyst_instructions}

You receive ONLY safe metadata, not raw private data.

Your task:
1. Recommend clear business column names where useful.
2. Recommend high-value derived columns that improve KPI/dashboard usefulness.
3. Prioritize derived columns that enable better time trends, month filters, segmentation, ranking, pricing, sales, HR, inventory, support, location, or operations analysis.
4. If a date/datetime column exists, strongly consider extract_date, extract_month, extract_weekday, and extract_hour when useful for trends and filters.
5. If a dimension such as product, brand, merchant, category, region, city, department, supplier, status, payment method, priority, or team exists, preserve it for filtering and ranking.
6. Avoid cosmetic-only suggestions.
7. Return only executable suggestions supported by the available columns.

You may return derived columns in either of two formats.

Preferred rich DSL format:
- new_column: new derived column name
- formula: safe formula DSL expression
- source_columns: list of source columns used in the formula

Examples:
- `maximum_price - minimum_price`
- `round((original_price - sale_price) / original_price, 2)`
- `date_diff(closed_date, opened_date, "days")`
- `if(stock_quantity <= reorder_level, "Low Stock", "OK")`
- `bucket(minimum_price, [0,50,100,500], ["Low","Medium","High"])`
- `month(transaction_date)`

Legacy formula_type format is also supported:
- multiply: requires exactly two numeric input columns.
- extract_date: requires exactly one date/datetime input column.
- extract_month: requires exactly one date/datetime input column.
- extract_hour: requires exactly one date/datetime input column.
- extract_weekday: requires exactly one date/datetime input column.

Prefer DSL formulas when they create more useful analytics columns such as spreads, rates, margins, flags, bands, date differences, tenure, resolution time, stock status, sale indicators, normalized text fields, weekend/business-time indicators, or fallback values using coalesce().

Safe metadata:
{json.dumps(metadata, indent=2, ensure_ascii=False)}

Return JSON using exactly this structure:
{{
  "rename_columns": [
    {{
      "old_name": "existing column name",
      "new_name": "clear business column name",
      "confidence": 0.0,
      "reason": "short reason"
    }}
  ],
  "derived_columns": [
    {{
      "new_column": "new column name",
      "formula": "safe Formula DSL expression, preferred when useful",
      "source_columns": ["existing column name"],
      "formula_type": "multiply | extract_date | extract_month | extract_hour | extract_weekday | null",
      "input_columns": ["existing column name, only for legacy formula_type"],
      "dtype": "int | float | string | datetime | bool | category | unknown",
      "category": "temporal | date_math | arithmetic | bucketing | flag | normalization | text | domain",
      "enables": ["monthly_trend_analysis", "segmentation", "ranking", "filtering"],
      "priority": "high | medium | low",
      "confidence": 0.0,
      "reason": "short reason explaining business value"
    }}
  ]
}}
Important output rules:
- For each derived column, use either `formula` + `source_columns` OR `formula_type` + `input_columns`. Do not leave both empty.
- If using `formula`, set formula_type to null or omit it.
- Extra fields such as dtype, category, enables, and confidence are allowed and useful for validation, documentation, and future reporting.
- Do not create weak columns only for cosmetic reasons. Each derived column should unlock a KPI, filter, ranking, trend, segmentation, exception flag, or report insight.
"""
    data = _call_openai_json(prompt)
    data.setdefault("rename_columns", [])
    data.setdefault("derived_columns", [])
    return data


def get_kpi_dashboard_suggestions(metadata: dict) -> dict:
    """
    Smart AI #2: KPI and Dashboard Strategy AI.

    Receives enriched safe metadata and returns:
    - kpis
    - dashboard_layout
    """
    analyst_instructions = _load_prompt_file(
        "02_kpi_strategy_consultant_prompt.md",
        KPI_DASHBOARD_ANALYST_INSTRUCTIONS,
    )
    prompt = f"""
{analyst_instructions}

You receive ONLY safe enriched metadata, not raw private data.

Your task:
1. Design the most decision-useful and interactive dashboard possible from the available columns.
2. Suggest executive KPI cards, high-value comparison charts, time trends, rankings, filters, and tables.
3. Think about what a manager or analyst would actually need to know and click/filter.
4. Include filter columns that make sense for interactivity, especially month/date, product, brand, category, merchant, location, department, payment method, status, priority, supplier, and team.
5. Prefer grouped month/date trends over raw timestamp charts.
6. Prefer top-N rankings and category comparisons over unreadable long charts.
7. Avoid generic, unreadable, or low-value charts.
8. Use only available columns.
9. Do not suggest KPIs that cannot be calculated.

Supported formula_type values:
- sum: requires "column".
- average: requires "column".
- count_rows: does not require a column.
- group_sum: requires "group_by" and "value_column".
- group_average: requires "group_by" and "value_column".
- group_count: requires "group_by".

Supported visual values (use the chart type that best fits the data shape — the full set is intentionally broad):
- kpi_card           — single headline numbers
- line_chart         — time-ordered trend (≤2 per dashboard unless trend is the dataset's purpose)
- area_chart         — emphasised trend / cumulative growth
- bar_chart          — categorical comparison, ≤12 categories
- horizontal_bar_chart — long-label rankings or top-N (≥6 categories)
- column_chart       — same as bar but vertical for short labels
- pie_chart          — share of total, ≤5 slices, top_share_pct ≥ 25%
- donut_chart        — share of total with a centre KPI, ≤6 slices
- stacked_bar_chart  — composition over a dimension
- scatter_plot       — correlation between two measures
- bubble_chart       — correlation with a third measure encoding size
- histogram          — distribution of a single measure
- box_plot           — distribution by category
- heatmap            — two-dimensional density (e.g. day × hour)
- treemap            — hierarchical share-of-total
- funnel_chart       — sequential conversion stages
- gauge              — KPI with a target / threshold
- table              — long detail listings
- ranking_table      — top-N with rank + magnitude (preferred over pie when categories > 5)
- map_choropleth     — only when a geo column exists
- map_points         — only when latitude/longitude columns exist

Pick the type that respects the data:
- If a dimension has > 6 unique categories, do NOT use pie/donut — prefer bar / horizontal_bar / treemap / ranking_table.
- Cap line_chart to ≤2 unless trend analysis is the dataset's explicit purpose.
- Use ranking_table for "top-N" questions with named items (products, merchants, customers).

Safe enriched metadata:
{json.dumps(metadata, indent=2, ensure_ascii=False)}

Return JSON using exactly this structure:
{{
  "kpis": [
    {{
      "name": "KPI name",
      "formula_type": "sum | average | count_rows | group_sum | group_average | group_count",
      "column": "column name if needed",
      "group_by": "grouping column if needed",
      "value_column": "value column if needed",
      "visual": "one of the supported visual values listed above",
      "priority": "high | medium | low",
      "reason": "short reason"
    }}
  ],
  "dashboard_layout": {{
    "top": ["KPI card names"],
    "middle": ["main chart names"],
    "bottom": ["secondary chart or table names"],
    "filters": ["available filter columns"]
  }},
  "future_opportunities": [
    {{
      "name": "advanced KPI or visual not yet executable",
      "type": "growth_rate | map | heatmap | treemap | scatter | histogram | funnel | gauge | other",
      "required_columns": ["column name"],
      "why_useful": "short reason",
      "implementation_note": "what the local engine/frontend would need to support this later"
    }}
  ]
}}
"""
    data = _call_openai_json(prompt)
    data.setdefault("kpis", [])
    data.setdefault("dashboard_layout", {"top": [], "middle": [], "bottom": [], "filters": []})
    data.setdefault("future_opportunities", [])
    return data


def generate_dashboard_design_plan(metadata: dict, kpi_suggestions: dict, dashboard_data: dict) -> dict:
    """
    Smart AI #3: Dashboard Experience Architect.

    Receives enriched metadata, validated KPI suggestions, and chart-ready dashboard data.
    Returns a professional dashboard design plan that the local renderer can use.
    """
    architect_instructions = _load_prompt_file(
        "03_dashboard_experience_architect_prompt.md",
        DASHBOARD_ARCHITECT_INSTRUCTIONS,
    )
    prompt = f"""
{architect_instructions}

You receive ONLY safe metadata, validated KPI suggestions, and aggregated/chart-ready dashboard data.

Your task:
1. Organize the dashboard into professional sections.
2. Decide which charts should be primary, secondary, or table-style.
3. Enforce chart diversity and avoid repeating line charts.
4. Identify useful interactive filters and recommended filter behavior.
5. Identify map-readiness if location columns exist.
6. Create a concise dashboard title and objective.
7. Provide practical design notes for the frontend/local renderer.

Safe enriched metadata:
{json.dumps(metadata, indent=2, ensure_ascii=False, default=str)}

Validated KPI/dashboard suggestions:
{json.dumps(kpi_suggestions, indent=2, ensure_ascii=False, default=str)}

Chart-ready dashboard data summary:
{json.dumps(dashboard_data, indent=2, ensure_ascii=False, default=str)}

Return JSON using exactly this structure:
{{
  "dashboard_title": "professional dashboard title",
  "dashboard_objective": "short business objective",
  "recommended_filters": [
    {{
      "column": "available filter column",
      "label": "user friendly filter name",
      "filter_type": "multi_select | date_range | single_select",
      "why_useful": "short reason"
    }}
  ],
  "sections": [
    {{
      "section_name": "Executive Overview",
      "section_type": "kpi_cards | trend | comparison | ranking | table | insight | data_quality | map_ready",
      "purpose": "why this section matters",
      "items": ["names of KPIs/charts/cards to place here"],
      "preferred_visual": "cards | line_chart | area_chart | bar_chart | horizontal_bar_chart | pie_chart | donut_chart | table | ranking_table | histogram | scatter_plot | bubble_chart | heatmap | treemap | funnel_chart | gauge | map_ready | insight_box"
    }}
  ],
  "chart_decisions": [
    {{
      "chart_name": "existing chart/KPI name",
      "decision": "primary | secondary | convert_to_table | hide | keep_as_card",
      "preferred_visual": "line_chart | area_chart | bar_chart | horizontal_bar_chart | pie_chart | donut_chart | table | ranking_table | histogram | scatter_plot | bubble_chart | heatmap | treemap | funnel_chart | gauge | map_ready | card",
      "reason": "short design reason"
    }}
  ],
  "interaction_plan": {{
    "default_view": "what the user sees first",
    "filter_behavior": "how filters should behave, including multi-select month/category behavior",
    "drilldown_ideas": ["useful drilldown idea"]
  }},
  "data_story": [
    "short insight or question the dashboard should help answer"
  ],
  "design_notes": [
    "short frontend/local renderer instruction"
  ]
}}
"""
    data = _call_openai_json(prompt)
    data.setdefault("dashboard_title", dashboard_data.get("title", "Executive Analytics Dashboard"))
    data.setdefault("dashboard_objective", "Provide a decision-focused overview of the dataset.")
    data.setdefault("recommended_filters", [])
    data.setdefault("sections", [])
    data.setdefault("chart_decisions", [])
    data.setdefault("interaction_plan", {})
    data.setdefault("data_story", [])
    data.setdefault("design_notes", [])
    return data


def generate_business_report(report_summary: dict) -> str:
    """
    Report Intelligence AI.

    Receives only a safe aggregated report summary and generates a professional
    business report in Markdown format. It must not ask for or invent raw data.
    """
    report_instructions = _load_prompt_file(
        "05_business_report_writer_prompt.md",
        REPORT_ANALYST_INSTRUCTIONS,
    )
    prompt = f"""
{report_instructions}

You receive ONLY a safe aggregated summary, not raw private data.

Report rules:
1. Do not invent facts that are not supported by the summary.
2. Do not mention or request raw private data.
3. Do not claim causation unless the data directly supports it.
4. Use clear business language.
5. Include practical recommendations.
6. Keep the report concise but complete.
7. Use Markdown headings.

Safe report summary:
{json.dumps(report_summary, indent=2, ensure_ascii=False, default=str)}

Write the report using these sections:
1. Executive Summary
2. Dataset Overview
3. Data Quality Findings
4. Transformations Applied
5. KPI Results
6. Dashboard Insights
7. Risks and Anomalies
8. Recommendations
9. Suggested Next Actions
"""
    return _call_openai_text(prompt)


# ---------------------------------------------------------------------------
# Smart AI #4 — Dashboard Insight Writer
# ---------------------------------------------------------------------------

INSIGHT_WRITER_INSTRUCTIONS = """
You are Smart AI #4: Senior Dashboard Insight Writer.

You receive ONLY an aggregated payload — KPI values, top/bottom items per chart, and
precomputed summary_stats. You never see raw rows.

Write a strict-JSON output containing:
- a one-sentence headline_summary (≤22 words, includes ≥1 specific number),
- 4–7 numbered insights, each tied to a specific chart and a specific number,
- 3–5 strategic recommendations linked to insights via linked_insight_ids,
- a 3–5 paragraph data_story narrative,
- 0–4 dashboard_callouts pointing at specific charts.

Every number you emit must appear in the input. No causal claims. No invented columns,
charts, or KPI names. Tone: confident, calm, executive — no emojis, no exclamations.
""".strip()


def generate_dashboard_insights(insight_payload: dict) -> dict:
    """
    Smart AI #4: Dashboard Insight Writer.

    Receives an aggregated insight payload (built by services/insight_engine.py) and
    returns headline summary, insights, strategic recommendations, narrative, and
    dashboard callouts. Never sees raw rows.
    """
    instructions = _load_prompt_file(
        "04_dashboard_insight_writer_prompt.md",
        INSIGHT_WRITER_INSTRUCTIONS,
    )
    prompt = f"""
{instructions}

You receive ONLY this aggregated payload (no raw rows):
{json.dumps(insight_payload, indent=2, ensure_ascii=False, default=str)}

Return strict JSON using exactly this structure (no markdown fences, no commentary):
{{
  "headline_summary": "string (≤22 words, includes ≥1 specific number from the payload)",
  "insights": [
    {{
      "id": "ins_001",
      "title": "Short title (≤10 words)",
      "body": "1–2 sentence insight with a specific number and a comparison or rank.",
      "supporting_chart_ids": ["chart_id from payload.chart_summaries"],
      "supporting_kpi_names": ["kpi name from payload.kpi_values"],
      "magnitude_words": ["dominant | leading | growing | declining | stable | concentrated | dispersed | seasonal | recovering | underperforming"],
      "confidence": 0.0
    }}
  ],
  "strategic_recommendations": [
    {{
      "id": "rec_001",
      "title": "Short imperative (≤12 words, starts with a verb)",
      "body": "Why and how, grounded in 1–2 specific insights.",
      "linked_insight_ids": ["ins_001"],
      "effort": "low | medium | high",
      "expected_impact": "low | medium | high",
      "confidence": 0.0
    }}
  ],
  "data_story": [
    "Paragraph 1 — the hook.",
    "Paragraph 2 — the most interesting trend.",
    "Paragraph 3 — the segment or product story.",
    "Paragraph 4 — the implication / what to do next."
  ],
  "dashboard_callouts": [
    {{
      "chart_id": "chart_id from payload",
      "label": "Streak | Anomaly | Watch | Note",
      "text": "≤18 words pointing at a specific value or shape on this chart."
    }}
  ],
  "coverage_notes": {{
    "kpis_used": ["kpi names referenced"],
    "charts_used": ["chart_ids referenced"],
    "skipped_with_reason": [
      {{"id": "chart_id", "reason": "short reason"}}
    ]
  }}
}}

Caps: insights ≤7, strategic_recommendations ≤5, data_story ≤5 paragraphs, dashboard_callouts ≤4.
"""
    data = _call_openai_json(prompt)
    data.setdefault("headline_summary", "")
    data.setdefault("insights", [])
    data.setdefault("strategic_recommendations", [])
    data.setdefault("data_story", [])
    data.setdefault("dashboard_callouts", [])
    data.setdefault("coverage_notes", {})
    return data
