# Stock Dashboard

A Streamlit web app for charting stock price, volume, realized volatility, and implied volatility using free data from Yahoo Finance.

## Live Demo

[https://stock-dashboard-av.streamlit.app](https://stock-dashboard-av.streamlit.app)

## Features

- **Candlestick chart** with 100d / 200d moving average toggle
- **Volume bars** color-coded by price direction (green up, red down)
- **Period selector** — 1D, 5D, 3M, 6M, 1Y, 5Y with intraday intervals for short timeframes
- **Realized volatility** — configurable rolling window (5–60 days), optional annualization
- **Implied volatility smile** — calls and puts IV from nearest expiry options chain
- **IV vs RV spread** — at-the-money IV compared against current realized vol
- **Gap-free charts** — weekends and after-hours gaps are hidden automatically

## Tech Stack

- [Streamlit](https://streamlit.io/) — web framework
- [yfinance](https://github.com/ranaroussi/yfinance) — market data (free, no API key)
- [Plotly](https://plotly.com/python/) — interactive charts

## Run Locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

Opens at `http://localhost:8501`.

## Deploy

1. Push to GitHub
2. Go to [share.streamlit.io](https://share.streamlit.io)
3. Connect the repo and deploy
