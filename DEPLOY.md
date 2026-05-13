# Deployment guide — Streamlit Community Cloud

This guide gets the dashboard onto a public URL so you can put a live link on your portfolio site or résumé. Total time: ~5 minutes once the repo is on GitHub.

## What's in the repo for deployment

- **`requirements.txt`** — Streamlit Cloud doesn't run `uv`, so we list the runtime deps separately. Keep in sync with `pyproject.toml`.
- **`.streamlit/config.toml`** — theme + page config. Streamlit Cloud reads this automatically.
- **`data/sample/*.parquet`** — committed sample dataset (full 2019Q1–2023Q1 series). The dashboard falls back to this on a fresh clone, so the live deployment has working charts out of the box.
- **`app/streamlit_app.py`** — the entry point.

The FFIEC fetcher (`scripts/fetch_ffiec.py`) is *not* run on Streamlit Cloud — it requires Chromium (~110 MB) and would push the deployment over Streamlit's resource limits. The committed sample dataset covers the full timeline shown on the dashboard.

## Steps

### 1 — Push the repo to GitHub

```bash
gh repo create alm-rate-shock-2023 --public --source=. --remote=origin
git push -u origin main
```

(Or create the repo via the GitHub web UI and push manually.)

### 2 — Create a Streamlit Community Cloud account

Go to https://streamlit.io/cloud, sign in with the same GitHub account that owns the repo. Free tier is fine — the dashboard fits comfortably under Streamlit Cloud's resource limits.

### 3 — Deploy

In the Streamlit Cloud UI:

1. Click **"Create app"**.
2. **Repository:** `<your-github-handle>/alm-rate-shock-2023`
3. **Branch:** `main`
4. **Main file path:** `app/streamlit_app.py`
5. **Python version:** `3.11`
6. Click **"Deploy"**.

The first build pulls the deps from `requirements.txt` (~90 seconds), then the app comes up.

### 4 — Configure secrets (optional)

The dashboard works without a FRED API key because it falls back to the committed `data/sample/fred_macro.parquet`. If you want the deployed app to refresh live FRED data on each load, add the key under **App settings → Secrets**:

```toml
FRED_API_KEY = "your-key-here"
```

The deployed dashboard otherwise has nothing sensitive — all numbers come from public FFIEC and FRED data.

### 5 — Get the public URL

Streamlit Cloud assigns a URL of the form `https://<repo-name>-<random>.streamlit.app`. You can rename it in the app settings to something like `alm-rate-shock-jaredlimon.streamlit.app`. Put that link in your README badge and on your portfolio / résumé.

## Updating the deployed app

Every `git push` to `main` triggers an automatic re-deploy. No manual action required.

If you change `requirements.txt`, Streamlit Cloud rebuilds the environment on the next push (~90 sec). If you change only the app code or data, redeploys are near-instant.

## What if Streamlit Cloud isn't available?

The same `streamlit_app.py` works on any host that can run a Python web app:

- **Hugging Face Spaces** — free, similar workflow.
- **Render / Fly.io / Railway** — provide a `Procfile` like `web: streamlit run app/streamlit_app.py --server.port=$PORT --server.address=0.0.0.0`.
- **A VPS** — `uv pip install -e ".[dashboard]"` then run streamlit behind nginx.

The codebase has no Streamlit-Cloud-specific lock-in.
