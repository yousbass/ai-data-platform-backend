from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any


VISUAL_TYPE_MAP = {
    "kpi_card": "card",
    "line_chart": "line",
    "area_chart": "area",
    "bar_chart": "bar",
    "horizontal_bar_chart": "horizontal_bar",
    "stacked_bar_chart": "bar",
    "pie_chart": "pie",
    "donut_chart": "donut",
    "table": "table",
    "ranking_table": "table",
    "pivot_table": "table",
    "histogram": "histogram",
    "scatter_plot": "scatter",
    "bubble_chart": "bubble",
    "heatmap": "heatmap",
    "treemap": "treemap",
    "funnel_chart": "funnel",
    "gauge": "gauge",
    "map_ready": "map_ready",
}


def _format_number(value: Any) -> str:
    """Format numeric values for dashboard display."""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        if abs(value) >= 1_000_000_000:
            return f"{value / 1_000_000_000:.2f}B"
        if abs(value) >= 1_000_000:
            return f"{value / 1_000_000:.2f}M"
        if abs(value) >= 1_000:
            return f"{value:,.2f}".rstrip("0").rstrip(".")
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    return str(value)


def _safe_text(value: Any) -> str:
    return escape(str(value)) if value is not None else "—"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _top_items(data: list[dict[str, Any]], x_field: str | None, y_field: str | None, limit: int = 10) -> list[dict[str, Any]]:
    if not x_field or not y_field:
        return []
    rows = []
    for row in data[:limit]:
        rows.append({
            "label": row.get(x_field, "—"),
            "value": row.get(y_field, 0),
        })
    return rows


def _normalize_visual(visual: str | None) -> str:
    if not visual:
        return "bar"
    return VISUAL_TYPE_MAP.get(visual, visual)


def _infer_y_field(formula_type: str | None, kpi: dict[str, Any], chart_meta: dict[str, Any] | None) -> str | None:
    if chart_meta and chart_meta.get("y_field"):
        return chart_meta.get("y_field")
    if formula_type in {"group_sum", "group_average", "group_median", "group_min", "group_max"}:
        return kpi.get("value_column")
    if formula_type == "group_count":
        return "Count"
    if formula_type in {"histogram"}:
        return "Count"
    if formula_type in {"heatmap"}:
        return "Value"
    return None


def _infer_x_field(kpi: dict[str, Any], chart_meta: dict[str, Any] | None) -> str | None:
    if chart_meta and chart_meta.get("x_field"):
        return chart_meta.get("x_field")
    return kpi.get("group_by") or kpi.get("x_column") or kpi.get("x")


def _make_insights(dashboard: dict[str, Any]) -> list[str]:
    insights: list[str] = []

    for chart in dashboard.get("charts", []):
        data = chart.get("data", []) or []
        x_field = chart.get("x_field")
        y_field = chart.get("y_field")
        if not data or not x_field or not y_field:
            continue

        valid_rows = [r for r in data if isinstance(r.get(y_field), (int, float))]
        if not valid_rows:
            continue
        first = max(valid_rows, key=lambda r: r.get(y_field) or 0)
        label = first.get(x_field)
        value = first.get(y_field)
        insights.append(f"{chart.get('title', 'Chart')}: the highest item is {label} with {_format_number(value)}.")

        if len(insights) >= 4:
            break

    design_plan = dashboard.get("design_plan") or {}
    for item in design_plan.get("data_story", []) or []:
        if isinstance(item, str) and item not in insights:
            insights.append(item)
        if len(insights) >= 6:
            break

    if not insights:
        insights.append("The dashboard was generated from the available cleaned and enriched dataset.")

    return insights


def build_dashboard_data(kpi_suggestions, kpi_results):
    chart_metadata = kpi_results.get("chart_metadata", {})
    dashboard = {
        "title": "Executive Analytics Dashboard",
        "subtitle": "Auto-generated from cleaned and enriched data",
        "cards": kpi_results.get("kpi_cards", []),
        "charts": [],
        "filters": kpi_suggestions.get("dashboard_layout", {}).get("filters", []),
        "future_opportunities": kpi_results.get("future_opportunities", kpi_suggestions.get("future_opportunities", [])),
    }

    grouped = kpi_results.get("grouped_results", {})

    for kpi in kpi_suggestions.get("kpis", []):
        name = kpi.get("name")
        if name not in grouped:
            continue

        formula_type = kpi.get("formula_type")
        visual = kpi.get("visual") or kpi.get("chart_type") or "bar_chart"
        chart_meta = chart_metadata.get(name, {})
        chart_type = _normalize_visual(chart_meta.get("chart_type") or visual)
        x_field = _infer_x_field(kpi, chart_meta)
        y_field = _infer_y_field(formula_type, kpi, chart_meta)

        dashboard["charts"].append({
            "title": name,
            "type": chart_type,
            "visual": visual,
            "formula_type": formula_type,
            "subtitle": kpi.get("reason", "Generated from the selected KPI logic."),
            "x_field": x_field,
            "y_field": y_field,
            "color_field": chart_meta.get("color_field") or kpi.get("color_column") or kpi.get("color"),
            "size_field": chart_meta.get("size_field") or kpi.get("size_column") or kpi.get("size"),
            "value_field": chart_meta.get("value_field"),
            "data": grouped[name],
            "metadata": chart_meta,
        })

    dashboard["insights"] = _make_insights(dashboard)
    return dashboard


def _render_filter_bar(filters: list[str]) -> str:
    if not filters:
        return ""

    chips = "".join(f'<span class="chip">{_safe_text(f)}</span>' for f in filters)
    return f"""
    <section class="filter-card">
      <div>
        <div class="filter-title">Available Interactive Filters</div>
        <div class="filter-subtitle">These fields can become slicers in the website version.</div>
      </div>
      <div class="chips">{chips}</div>
    </section>
    """


def _render_cards(cards: list[dict[str, Any]]) -> str:
    if not cards:
        return """
        <section class="empty-card">
          <h3>No KPI cards were generated</h3>
          <p>The current dataset may not contain clear numeric measures for summary cards.</p>
        </section>
        """

    icons = ["$", "#", "↗", "◆", "✓", "◷", "Σ", "%"]
    html = []
    for index, card in enumerate(cards):
        title = _safe_text(card.get("title", "KPI"))
        value = _format_number(card.get("value"))
        icon = icons[index % len(icons)]
        html.append(f"""
        <article class="kpi-card accent-{(index % 6) + 1}">
          <div>
            <div class="kpi-title">{title}</div>
            <div class="kpi-value">{_safe_text(value)}</div>
          </div>
          <div class="kpi-icon">{icon}</div>
        </article>
        """)
    return f'<section class="kpi-grid">{"".join(html)}</section>'


def _render_table(chart: dict[str, Any], limit: int = 12) -> str:
    data = chart.get("data", []) or []
    if not data:
        return "<p class='muted'>No table data available.</p>"

    columns = list(data[0].keys())[:6]
    header = "".join(f"<th>{_safe_text(col)}</th>" for col in columns)
    rows = []
    for row in data[:limit]:
        cells = "".join(f"<td>{_safe_text(_format_number(row.get(col)) if isinstance(row.get(col), (int, float)) else row.get(col))}</td>" for col in columns)
        rows.append(f"<tr>{cells}</tr>")

    return f"""
    <div class="table-wrap">
      <table>
        <thead><tr>{header}</tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>
    """


def _render_plotly_chart(chart: dict[str, Any], chart_id: str) -> str:
    chart_type = chart.get("type") or "bar"
    data = chart.get("data", []) or []
    x_field = chart.get("x_field")
    y_field = chart.get("y_field")
    value_field = chart.get("value_field") or y_field
    size_field = chart.get("size_field")

    if chart_type in {"table", "map_ready"}:
        return _render_table(chart)

    if not data:
        return "<p class='muted'>No chart data available.</p>"

    payload = {
        "type": chart_type,
        "data": data,
        "x": x_field,
        "y": y_field,
        "value": value_field,
        "size": size_field,
        "title": chart.get("title", "Chart"),
    }

    return f"""
    <div id="{chart_id}" class="plotly-chart"></div>
    <script>
      renderChart("{chart_id}", {_json(payload)});
    </script>
    """


def _render_chart_card(chart: dict[str, Any], index: int) -> str:
    chart_type = chart.get("type") or "bar"
    title = _safe_text(chart.get("title", "Chart"))
    subtitle = _safe_text(chart.get("subtitle", ""))
    chart_id = f"chart_{index}"
    body = _render_plotly_chart(chart, chart_id)

    return f"""
    <article class="chart-card chart-{_safe_text(chart_type)}">
      <div class="card-header">
        <div>
          <h2>{title}</h2>
          <p>{subtitle}</p>
        </div>
        <span class="chart-badge">{_safe_text(chart_type)}</span>
      </div>
      {body}
    </article>
    """


def _render_charts(charts: list[dict[str, Any]]) -> str:
    if not charts:
        return """
        <section class="empty-card">
          <h3>No charts were generated</h3>
          <p>The data did not contain enough valid dimensions and measures to build grouped charts.</p>
        </section>
        """
    return f'<section class="chart-grid">{"".join(_render_chart_card(chart, idx) for idx, chart in enumerate(charts))}</section>'


def _render_insights(insights: list[str]) -> str:
    items = "".join(f"<li>{_safe_text(item)}</li>" for item in insights)
    return f"""
    <section class="insight-card">
      <h2>Key Insights</h2>
      <ul>{items}</ul>
    </section>
    """


def _render_future_opportunities(items: list[dict[str, Any]]) -> str:
    if not items:
        return ""
    cards = []
    for item in items[:6]:
        cards.append(f"""
        <div class="future-item">
          <div class="future-type">{_safe_text(item.get('type', 'future'))}</div>
          <h3>{_safe_text(item.get('name', 'Future opportunity'))}</h3>
          <p>{_safe_text(item.get('why_useful', item.get('implementation_note', '')))}</p>
        </div>
        """)
    return f"""
    <section class="future-card">
      <h2>Future Visualization Opportunities</h2>
      <p class="muted">These are useful advanced visuals or KPIs that can be enabled as the frontend engine expands.</p>
      <div class="future-grid">{''.join(cards)}</div>
    </section>
    """


def _render_design_plan(dashboard: dict[str, Any]) -> str:
    plan = dashboard.get("design_plan") or {}
    sections = plan.get("sections") or []
    interaction = plan.get("interaction_plan") or {}
    notes = plan.get("design_notes") or []

    section_html = ""
    if sections:
        rendered = []
        for section in sections[:6]:
            name = section.get("section_name") or section.get("title") or "Dashboard Section"
            purpose = section.get("purpose") or section.get("section_type") or ""
            rendered.append(f"""
            <div class="plan-section">
              <h3>{_safe_text(name)}</h3>
              <p>{_safe_text(purpose)}</p>
            </div>
            """)
        section_html = f'<div class="plan-grid">{"".join(rendered)}</div>'

    interaction_text = interaction.get("filter_behavior") or interaction.get("default_view") or ""
    notes_html = "".join(f"<li>{_safe_text(note)}</li>" for note in notes[:4])

    if not section_html and not interaction_text and not notes_html:
        return ""

    return f"""
    <section class="design-card">
      <h2>Dashboard Design Plan</h2>
      {section_html}
      {f'<p class="muted"><b>Interaction:</b> {_safe_text(interaction_text)}</p>' if interaction_text else ''}
      {f'<ul>{notes_html}</ul>' if notes_html else ''}
    </section>
    """


def write_html_dashboard(dashboard: dict[str, Any], output_path: str | Path) -> None:
    """Write a polished standalone HTML dashboard preview."""
    output_path = Path(output_path)
    title = _safe_text(dashboard.get("title", "Executive Analytics Dashboard"))
    subtitle = _safe_text(dashboard.get("subtitle", "Auto-generated dashboard"))

    html = f"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
  <style>
    :root {{
      --bg: #f6f7fb;
      --card: #ffffff;
      --text: #101828;
      --muted: #667085;
      --line: #e4e7ec;
      --primary: #f58a07;
      --primary-light: #fff3e3;
      --green: #2fb67c;
      --blue: #3f6fd9;
      --purple: #7c3aed;
      --red: #e5484d;
      --shadow: 0 12px 28px rgba(16, 24, 40, 0.08);
      --radius: 18px;
    }}

    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
    }}

    .topbar {{ background: white; border-bottom: 1px solid var(--line); padding: 28px 8vw 22px; }}
    .topbar h1 {{ margin: 0; font-size: 32px; letter-spacing: -0.03em; }}
    .topbar p {{ margin: 8px 0 0; color: var(--muted); font-size: 15px; }}
    main {{ width: min(1440px, 92vw); margin: 30px auto 60px; }}

    .filter-card, .empty-card, .insight-card, .future-card, .design-card, .chart-card, .kpi-card {{
      background: var(--card); border: 1px solid var(--line); border-radius: var(--radius); box-shadow: var(--shadow);
    }}

    .filter-card {{ padding: 22px 26px; margin-bottom: 24px; display: flex; align-items: center; gap: 22px; flex-wrap: wrap; }}
    .filter-title {{ color: var(--text); font-weight: 800; letter-spacing: -0.02em; }}
    .filter-subtitle {{ color: var(--muted); font-size: 13px; margin-top: 4px; }}
    .chips {{ display: flex; gap: 10px; flex-wrap: wrap; }}
    .chip {{ display: inline-flex; align-items: center; padding: 9px 14px; border-radius: 999px; background: var(--primary); color: white; font-weight: 700; font-size: 13px; }}

    .kpi-grid {{ display: grid; grid-template-columns: repeat(4, minmax(180px, 1fr)); gap: 18px; margin-bottom: 24px; }}
    .kpi-card {{ padding: 24px; display: flex; justify-content: space-between; align-items: center; min-height: 122px; border-top: 5px solid var(--primary); }}
    .kpi-card.accent-2 {{ border-top-color: var(--blue); }}
    .kpi-card.accent-3 {{ border-top-color: var(--green); }}
    .kpi-card.accent-4 {{ border-top-color: var(--purple); }}
    .kpi-card.accent-5 {{ border-top-color: var(--red); }}
    .kpi-card.accent-6 {{ border-top-color: #0ea5e9; }}
    .kpi-title {{ color: var(--muted); font-weight: 800; font-size: 14px; }}
    .kpi-value {{ font-size: 31px; font-weight: 900; margin-top: 10px; letter-spacing: -0.04em; }}
    .kpi-icon {{ width: 52px; height: 52px; border-radius: 15px; display: grid; place-items: center; background: var(--primary-light); color: var(--primary); font-size: 22px; font-weight: 900; }}

    .chart-grid {{ display: grid; grid-template-columns: repeat(2, minmax(320px, 1fr)); gap: 24px; }}
    .chart-card {{ padding: 24px; min-height: 430px; }}
    .card-header {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 14px; }}
    .card-header h2 {{ margin: 0; font-size: 20px; letter-spacing: -0.03em; }}
    .card-header p {{ margin: 7px 0 0; color: var(--muted); line-height: 1.4; font-size: 13px; }}
    .chart-badge {{ background: var(--primary-light); color: var(--primary); border-radius: 999px; padding: 7px 11px; font-weight: 800; font-size: 11px; text-transform: uppercase; white-space: nowrap; }}
    .plotly-chart {{ width: 100%; height: 310px; }}

    .table-wrap {{ overflow-x: auto; max-height: 320px; overflow-y: auto; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th {{ text-align: left; color: var(--muted); border-bottom: 1px solid var(--line); padding: 10px; background: #f9fafb; }}
    td {{ border-bottom: 1px solid #f0f2f5; padding: 10px; }}

    .insight-card, .future-card, .design-card {{ margin-top: 24px; padding: 26px; }}
    .insight-card h2, .future-card h2, .design-card h2 {{ margin: 0 0 14px; }}
    .insight-card li, .design-card li {{ margin: 8px 0; color: var(--muted); line-height: 1.5; }}
    .future-grid, .plan-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin-top: 14px; }}
    .future-item, .plan-section {{ border: 1px solid var(--line); border-radius: 14px; padding: 16px; background: #fbfcff; }}
    .future-item h3, .plan-section h3 {{ margin: 4px 0 8px; font-size: 15px; }}
    .future-item p, .plan-section p {{ margin: 0; color: var(--muted); font-size: 13px; line-height: 1.45; }}
    .future-type {{ color: var(--primary); font-weight: 900; font-size: 11px; text-transform: uppercase; }}

    .empty-card {{ padding: 28px; margin-bottom: 24px; }}
    .muted {{ color: var(--muted); }}
    footer {{ margin-top: 34px; color: var(--muted); text-align: center; font-size: 13px; }}

    @media (max-width: 1100px) {{ .kpi-grid, .chart-grid, .future-grid, .plan-grid {{ grid-template-columns: 1fr 1fr; }} }}
    @media (max-width: 720px) {{ .kpi-grid, .chart-grid, .future-grid, .plan-grid {{ grid-template-columns: 1fr; }} .topbar {{ padding: 22px 5vw; }} main {{ width: 94vw; }} }}
  </style>
</head>
<body>
  <header class="topbar">
    <h1>{title}</h1>
    <p>{subtitle}</p>
  </header>
  <main>
    {_render_filter_bar(dashboard.get('filters', []))}
    {_render_cards(dashboard.get('cards', []))}
    {_render_design_plan(dashboard)}
    {_render_charts(dashboard.get('charts', []))}
    {_render_insights(dashboard.get('insights', []))}
    {_render_future_opportunities(dashboard.get('future_opportunities', []))}
    <footer>Generated by the AI Data Platform backend. Data was processed locally and summarized for dashboard output.</footer>
  </main>

  <script>
    function values(rows, field) {{
      if (!field) return [];
      return rows.map(r => r[field]);
    }}

    function renderChart(id, payload) {{
      const rows = payload.data || [];
      const type = payload.type || 'bar';
      const x = values(rows, payload.x);
      const y = values(rows, payload.y);
      const v = values(rows, payload.value);
      const layout = {{
        margin: {{l: 50, r: 20, t: 15, b: 70}},
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(0,0,0,0)',
        font: {{family: 'Inter, Arial, sans-serif', size: 12}},
        xaxis: {{automargin: true}},
        yaxis: {{automargin: true}},
        showlegend: true,
      }};
      const config = {{responsive: true, displayModeBar: false}};
      let traces = [];

      if (type === 'line') {{
        traces = [{{type: 'scatter', mode: 'lines+markers', x, y, line: {{shape: 'spline', width: 3}} }}];
      }} else if (type === 'area') {{
        traces = [{{type: 'scatter', mode: 'lines', x, y, fill: 'tozeroy', line: {{shape: 'spline', width: 3}} }}];
      }} else if (type === 'horizontal_bar') {{
        traces = [{{type: 'bar', x: y, y: x, orientation: 'h'}}];
        layout.margin.l = 120;
      }} else if (type === 'pie' || type === 'donut') {{
        traces = [{{type: 'pie', labels: x, values: y, hole: type === 'donut' ? 0.55 : 0}}];
      }} else if (type === 'histogram') {{
        traces = [{{type: 'bar', x, y}}];
      }} else if (type === 'scatter' || type === 'bubble') {{
        const marker = {{size: type === 'bubble' && payload.size ? values(rows, payload.size) : 9, opacity: 0.75}};
        traces = [{{type: 'scatter', mode: 'markers', x, y, marker}}];
      }} else if (type === 'heatmap') {{
        const xs = [...new Set(x)];
        const ys = [...new Set(values(rows, payload.y))];
        const matrix = ys.map(yy => xs.map(xx => {{
          const found = rows.find(r => r[payload.x] === xx && r[payload.y] === yy);
          return found ? found[payload.value] : null;
        }}));
        traces = [{{type: 'heatmap', x: xs, y: ys, z: matrix, colorscale: 'YlOrRd'}}];
      }} else if (type === 'treemap') {{
        traces = [{{type: 'treemap', labels: x, parents: x.map(() => ''), values: y}}];
      }} else if (type === 'funnel') {{
        traces = [{{type: 'funnel', y: x, x: y}}];
      }} else if (type === 'gauge') {{
        traces = [{{type: 'indicator', mode: 'gauge+number', value: y[0] || 0}}];
      }} else {{
        traces = [{{type: 'bar', x, y}}];
      }}

      Plotly.newPlot(id, traces, layout, config);
    }}
  </script>
</body>
</html>
"""

    output_path.write_text(html, encoding="utf-8")
