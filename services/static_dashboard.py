"""Render the React dashboard as a single self-contained HTML file.

This module replaces the legacy ``services/html_dashboard.py`` (which produced
a basic Chart.js page with three blue bar charts). It reads a prebuilt React
bundle shipped under ``services/dashboard_assets/dashboard_template.html`` and
stamps the pipeline's dashboard JSON into it, producing a portable ``.html``
report that opens in any browser without a server, network, or Node runtime.

The template is built from the companion frontend repository
(``artifacts/ai-dashboard`` in the Replit project) via::

    pnpm --filter @workspace/ai-dashboard run build:static

and the resulting ``dist-static/static.html`` is copied into
``services/dashboard_assets/dashboard_template.html``. See
``services/dashboard_assets/README.md`` for the refresh workflow.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_TEMPLATE_PATH = Path(__file__).parent / "dashboard_assets" / "dashboard_template.html"

# Matches the placeholder block emitted by static.html in the dashboard repo:
#   window.__DASHBOARD_DATA__ = /*__DASHBOARD_DATA_PLACEHOLDER__*/ null /*__END_PLACEHOLDER__*/;
# Vite's singlefile build preserves inline <script> content verbatim, so the
# markers survive unchanged into the prebuilt template.
_PLACEHOLDER_RE = re.compile(
    r"/\*__DASHBOARD_DATA_PLACEHOLDER__\*/.*?/\*__END_PLACEHOLDER__\*/",
    re.DOTALL,
)


def _safe_json(payload: Any) -> str:
    """JSON-encode ``payload`` for safe embedding in an HTML ``<script>``.

    Escapes the characters that can break or be misinterpreted inside an
    inline script:
      - ``<`` → ``\\u003c`` — prevents ``</script>`` and ``<!--`` from
        terminating the script block.
      - ``>`` → ``\\u003e`` — defence-in-depth in case the surrounding HTML
        ever ends up wrapped in a comment (``-->``).
      - ``&`` → ``\\u0026`` — prevents accidental HTML-entity interpretation.
      - U+2028 / U+2029 — JavaScript treats these as line terminators, which
        breaks string literals.

    ``default=str`` makes ``datetime``, ``Decimal`` and similar non-JSON-native
    values serialisable without forcing callers to pre-convert them.
    """
    raw = json.dumps(payload, default=str, ensure_ascii=False)
    return (
        raw.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def render_static_dashboard(dashboard_data: dict, output_path: str | Path) -> Path:
    """Write a single self-contained HTML dashboard to ``output_path``.

    The output file embeds the React bundle, all CSS, and the dashboard JSON
    payload. It is fully usable when opened directly via ``file://`` — no
    server, no network call, no Node runtime is required at view time.

    Returns the path that was written.
    """
    if not _TEMPLATE_PATH.exists():
        raise FileNotFoundError(
            f"React dashboard template not found at {_TEMPLATE_PATH}. "
            "Rebuild it from the frontend repo with "
            "`pnpm --filter @workspace/ai-dashboard run build:static` and copy "
            "`dist-static/static.html` into `services/dashboard_assets/dashboard_template.html`."
        )

    template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    encoded = _safe_json(dashboard_data)
    replaced, count = _PLACEHOLDER_RE.subn(encoded, template, count=1)
    if count != 1:
        raise RuntimeError(
            "Could not locate the __DASHBOARD_DATA_PLACEHOLDER__ block in the "
            "dashboard template. The template may be corrupted or built with "
            "an incompatible Vite configuration."
        )

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(replaced, encoding="utf-8")
    return output_path
