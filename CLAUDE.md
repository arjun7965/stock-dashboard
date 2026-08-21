# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the App

```bash
pip install -r requirements.txt
streamlit run app.py
```

The app runs at `http://localhost:8501`. No API keys required — all data comes from Yahoo Finance for free.

Live deployment at https://stock-dashboard-av.streamlit.app (deployed via [share.streamlit.io](https://share.streamlit.io)).

There are no linters or build steps. Unit tests for the pure helpers live in `tests/test_*.py` and run with `pytest`.

## Architecture

This is a Streamlit app split by responsibility — a stock dashboard that charts price, volume, realized volatility, and implied volatility using free Yahoo Finance data via `yfinance`:
- `app.py` — UI, sidebar controls, and Plotly charts in display order
- `data.py` — all `yfinance` access wrapped in `@st.cache_data` fetchers (prices, options chains, company info, earnings, liquidity, ticker search)
- `calcs.py` — pure helpers (`normalize_ticker`, `fmt_compact`, `compute_realized_vol`) with no Streamlit dependency; unit-tested in `tests/`

**Data flow:** All market data is fetched through `yfinance` with Streamlit's `@st.cache_data` caching. There is no database or backend. Caching TTLs:
- 5 min (`ttl=300`): price data, options data, moving average data
- 1 hour (`ttl=3600`): company name, earnings, EPS history, forward estimates

**Key sections in app.py (top to bottom):**
- Sidebar controls: autocomplete searchbox (ticker or company name, loads only on suggestion selection), RV settings, MA toggle, feature toggles
- Shareable URLs via `st.query_params` (`?t=SYMBOL&p=PERIOD`)
- Price data fetching with intraday intervals for 1D/5D periods
- Candlestick + volume chart using Plotly subplots, with moving average overlay
- Earnings section: quarterly revenue/income with QoQ growth, EPS history (estimate vs actual), and forward estimates
- Liquidity table: bid/ask spread, dollar volume, turnover, Amihud illiquidity ratio
- Realized volatility chart (rolling window, log returns, optional annualization)
- Options implied volatility smile (calls/puts from nearest expiry), ATM IV, and IV-RV spread

**Charting patterns:** All charts use Plotly `go.Figure` or `make_subplots`. Weekend/after-hours gaps are hidden via `rangebreaks`. Volume bars are color-coded green/red by candle direction.

**Intraday handling:** 1D uses 5-min intervals (78 bars/day), 5D uses 15-min intervals (26 bars/day). The RV window scales by bars-per-day for intraday periods. Moving averages are disabled for intraday views.
