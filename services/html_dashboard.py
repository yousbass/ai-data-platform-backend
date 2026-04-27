from pathlib import Path
import json


def write_html_dashboard(dashboard_data: dict, path: Path):
    cards = "".join([f"<div class='card'><h3>{c['title']}</h3><p>{c['value']:,.2f}</p></div>" if isinstance(c.get('value'), float) else f"<div class='card'><h3>{c['title']}</h3><p>{c['value']}</p></div>" for c in dashboard_data.get('cards', [])])
    charts_html = ""
    for i, ch in enumerate(dashboard_data.get('charts', [])):
        charts_html += f"<section><h2>{ch['title']}</h2><canvas id='chart{i}'></canvas></section>"
    script_data = json.dumps(dashboard_data, default=str)
    html = f"""<!DOCTYPE html>
<html><head><meta charset='utf-8'><title>{dashboard_data.get('title','Dashboard')}</title>
<script src='https://cdn.jsdelivr.net/npm/chart.js'></script>
<style>
body {{ font-family: Arial, sans-serif; margin: 32px; background: #f6f7fb; }}
h1 {{ margin-bottom: 20px; }} .cards {{ display:grid; grid-template-columns: repeat(auto-fit,minmax(220px,1fr)); gap:16px; }}
.card {{ background:white; padding:20px; border-radius:14px; box-shadow:0 4px 14px rgba(0,0,0,.06); }}
.card h3 {{ margin:0 0 12px; color:#555; }} .card p {{ margin:0; font-size:26px; font-weight:bold; }}
section {{ background:white; margin-top:24px; padding:20px; border-radius:14px; box-shadow:0 4px 14px rgba(0,0,0,.06); }}
canvas {{ max-height:360px; }}
</style></head><body>
<h1>{dashboard_data.get('title','Dashboard')}</h1>
<div class='cards'>{cards}</div>
{charts_html}
<script>
const dashboard = {script_data};
dashboard.charts.forEach((ch, i) => {{
  const labels = ch.data.map(r => r[ch.x_field]);
  const values = ch.data.map(r => r[ch.y_field]);
  new Chart(document.getElementById('chart'+i), {{ type: ch.type === 'line' ? 'line' : 'bar', data: {{ labels, datasets: [{{ label: ch.y_field, data: values }}] }}, options: {{ responsive:true, plugins: {{ legend: {{ display:false }} }} }} }});
}});
</script></body></html>"""
    path.write_text(html, encoding="utf-8")
