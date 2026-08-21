# Repository Guidelines

## Project Structure & Module Organization

This repository is a Streamlit application split by responsibility. `app.py` holds the UI, sidebar controls, and Plotly charts in top-to-bottom display order. `data.py` wraps all Yahoo Finance access behind `@st.cache_data` fetchers, and `calcs.py` contains pure calculation helpers (`normalize_ticker`, `fmt_compact`, `compute_realized_vol`) with no Streamlit dependency, so they can be unit-tested directly. `requirements.txt` pins minimum runtime dependency versions, while `README.md` documents features and deployment. `.devcontainer/devcontainer.json` defines the Python 3.11 development container and forwards Streamlit's port 8501.

Keep fetch logic in `data.py`, reusable calculations in `calcs.py`, and rendering in `app.py`. Add unit tests for pure helpers under `tests/test_*.py`.

## Build, Test, and Development Commands

Create and activate a virtual environment before installing dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

The last command serves the dashboard at `http://localhost:8501`. There is no compilation or packaging step. Use `python -m py_compile app.py` for a quick syntax check.

## Coding Style & Naming Conventions

Follow standard Python conventions: four-space indentation, `snake_case` for functions and variables, and descriptive names such as `fetch_price_data`. Add type hints to calculation and data-fetching helpers where practical. Keep Streamlit labels concise and use existing Plotly patterns for chart layout and colors. Cache remote `yfinance` calls with `@st.cache_data`; choose a TTL appropriate to how quickly the data changes. No formatter or linter is configured, so keep imports and line lengths readable and avoid broad drive-by formatting.

## Testing Guidelines

No automated test framework or coverage threshold exists. Before submitting, run the syntax check and manually exercise the affected controls with a common ticker (for example, `AAPL`) and an invalid or data-sparse symbol. Verify empty-data handling, chart rendering, and both intraday and daily periods. If adding tests, place them in `tests/`, name files `test_*.py`, and add the chosen runner to `requirements.txt`.

## Commit & Pull Request Guidelines

Recent commits use short, imperative subjects such as `Add liquidity section` and `Fix potential AttributeError`. Follow that style and keep each commit focused. Pull requests should explain the user-visible change, summarize validation performed, and link any relevant issue. Include screenshots for chart, layout, or control changes, and call out changes to caching or market-data assumptions.

## Security & Data Reliability

The app requires no API keys; do not commit secrets or local environment files. Treat Yahoo Finance responses as unreliable external input: guard against exceptions, `None`, empty frames, and missing columns before rendering.
