# Dashboard assets

This folder ships the prebuilt React dashboard bundle that
`services/static_dashboard.py` stamps the pipeline's JSON into.

## What's here

- `dashboard_template.html` — a single self-contained HTML file produced by
  the React frontend (`artifacts/ai-dashboard` in the Replit companion repo).
  It contains the entire React bundle, Tailwind CSS, Recharts and a
  placeholder block:

  ```html
  <script>
    window.__DASHBOARD_DATA__ = /*__DASHBOARD_DATA_PLACEHOLDER__*/ null /*__END_PLACEHOLDER__*/;
  </script>
  ```

  At pipeline time, `render_static_dashboard()` substitutes the JSON payload
  for the `null` literal between those markers and writes the result to
  `output/<file>/10_dashboard_preview.html`.

## Why ship a prebuilt bundle

End-users run `python main.py file.csv` and expect a polished dashboard
without installing Node, pnpm or any frontend tooling. Committing the built
bundle (~960 KB) keeps the runtime story `python` only.

## How to refresh the template

Run the build in the companion frontend repo and copy the result here:

```bash
# In the React project (artifacts/ai-dashboard)
pnpm install
pnpm run build:static

# Copy the freshly built single-file bundle into this folder
cp dist-static/static.html services/dashboard_assets/dashboard_template.html
```

Then commit the updated `dashboard_template.html`. Both placeholder markers
must survive the rebuild — `services/static_dashboard.py` will raise if the
substitution target is missing.

## Why hash routing

The bundle uses `wouter`'s `useHashLocation` hook so the in-app navigation
links resolve to `#/insights` and `#/report`. This keeps the single HTML
file fully usable when opened from a local filesystem (no SPA fallback
server is required).
