# AI Data Analyst

Streamlit app for:

- CSV / Excel dataset analysis
- PBIX dashboard extraction and comparison
- report generation with charts and recommendations
- chatbot follow-up on raw data, dashboards, and generated reports
- HITL report editing with regenerate-with-feedback workflows

## Main app

Use:

```bash
streamlit run main_app.py
```

## FastAPI + React app

Use this version for heavier dashboard workflows where Streamlit may freeze during long PBIX/Tableau processing:

```bash
uvicorn backend_main:app --reload --host 0.0.0.0 --port 8000
```

Then open:

```text
http://localhost:8000
```

This app supports background jobs for:

- CSV / Excel dataset analysis
- Power BI `.pbix` dashboard extraction
- Tableau `.twb` / `.twbx` dashboard extraction
- mixed Power BI + Tableau comparison
- chart chatbot
- report feedback regeneration

## Local setup

1. Create a virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Add secrets in a local `.env` file or `.streamlit/secrets.toml`.

Required keys:

- `GEMINI_API_KEY`
- `GROQ_API_KEY`

Optional keys:

- `API_BASE_URL` for the HITL API if you run it separately

## Streamlit deployment

This repo is prepared for Streamlit Community Cloud.

Deployment settings:

- Repository: this GitHub repo
- Branch: your deployment branch
- Main file path: `main_app.py`

In Streamlit app settings, add these secrets:

```toml
GEMINI_API_KEY = "your-gemini-api-key"
GROQ_API_KEY = "your-groq-api-key"
```

The app now bootstraps Streamlit secrets into environment variables during startup, so the existing modules that use `os.getenv(...)` continue to work on Streamlit Cloud.

## Notes

- `.env`, uploaded data files, generated reports, and local scratch folders are ignored so the public repo stays clean.
- If a PBIX file does not expose all tables cleanly, the app may still extract layout metadata and partial chart data.
