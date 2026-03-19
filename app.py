import streamlit as st
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

st.set_page_config(page_title="Stock Dashboard", layout="wide")
st.title("Stock Dashboard")

# --- Sidebar controls ---
with st.sidebar:
    st.header("Settings")
    ticker = st.text_input("Ticker", value="AAPL").upper().strip()
    st.markdown("---")

    rv_window = st.slider("Realized Vol Window (days)", 5, 60, 20)
    rv_annualize = st.checkbox("Annualize Realized Vol", value=True)

    st.markdown("---")
    ma_period = st.radio("Moving Average", [100, 200], horizontal=True)
    show_options = st.checkbox("Show Options IV", value=True)


@st.cache_data(ttl=300)
def fetch_price_data(ticker: str, period: str) -> pd.DataFrame:
    tk = yf.Ticker(ticker)
    # Use appropriate interval for short periods
    if period == "1d":
        hist = tk.history(period="1d", interval="5m")
    elif period == "5d":
        hist = tk.history(period="5d", interval="15m")
    else:
        hist = tk.history(period=period)
    if hist.empty:
        return pd.DataFrame()
    hist.index = hist.index.tz_localize(None)
    return hist


@st.cache_data(ttl=300)
def fetch_options_iv(ticker: str):
    """Fetch IV from the nearest expiry options chain."""
    tk = yf.Ticker(ticker)
    try:
        expirations = tk.options
    except Exception:
        return None, None, None

    if not expirations:
        return None, None, None

    # Use the nearest expiry
    chain = tk.option_chain(expirations[0])
    calls = chain.calls[["strike", "impliedVolatility", "volume", "lastPrice"]].copy()
    puts = chain.puts[["strike", "impliedVolatility", "volume", "lastPrice"]].copy()
    calls["type"] = "Call"
    puts["type"] = "Put"
    return calls, puts, expirations[0]


def compute_realized_vol(prices: pd.Series, window: int, annualize: bool) -> pd.Series:
    log_returns = np.log(prices / prices.shift(1))
    rv = log_returns.rolling(window=window).std()
    if annualize:
        rv = rv * np.sqrt(252)
    return rv


# --- Period toggle buttons ---
period_options = {"1D": "1d", "5D": "5d", "3M": "3mo", "6M": "6mo", "1Y": "1y", "5Y": "5y"}
cols = st.columns(len(period_options) + 4)  # extra cols for spacing
for i, (label, val) in enumerate(period_options.items()):
    if cols[i].button(label, use_container_width=True):
        st.session_state["period"] = val

if "period" not in st.session_state:
    st.session_state["period"] = "1y"

period = st.session_state["period"]

# --- Fetch data ---
if not ticker:
    st.warning("Enter a ticker symbol.")
    st.stop()

hist = fetch_price_data(ticker, period)

if hist.empty:
    st.error(f"No data found for **{ticker}**. Check the symbol and try again.")
    st.stop()

# --- Company info header ---
info_col1, info_col2, info_col3, info_col4 = st.columns(4)
latest = hist.iloc[-1]
prev = hist.iloc[-2] if len(hist) > 1 else latest
change = latest["Close"] - prev["Close"]
change_pct = (change / prev["Close"]) * 100

info_col1.metric("Close", f"${latest['Close']:.2f}", f"{change:+.2f} ({change_pct:+.2f}%)")
info_col2.metric("Volume", f"{latest['Volume']:,.0f}")
info_col3.metric("Day High", f"${latest['High']:.2f}")
info_col4.metric("Day Low", f"${latest['Low']:.2f}")

# --- Price + Volume chart ---
st.subheader(f"{ticker} — Price & Volume")

fig_price = make_subplots(
    rows=2, cols=1,
    shared_xaxes=True,
    vertical_spacing=0.03,
    row_heights=[0.7, 0.3],
    subplot_titles=("Price", "Volume"),
)

# Candlestick
fig_price.add_trace(
    go.Candlestick(
        x=hist.index,
        open=hist["Open"],
        high=hist["High"],
        low=hist["Low"],
        close=hist["Close"],
        name="Price",
    ),
    row=1, col=1,
)

# Moving average
if len(hist) >= ma_period:
    fig_price.add_trace(
        go.Scatter(
            x=hist.index,
            y=hist["Close"].rolling(ma_period).mean(),
            name=f"{ma_period}d MA",
            line=dict(color="orange", width=1),
        ),
        row=1, col=1,
    )

# Volume bars colored by direction
colors = [
    "#26a69a" if hist["Close"].iloc[i] >= hist["Open"].iloc[i] else "#ef5350"
    for i in range(len(hist))
]
fig_price.add_trace(
    go.Bar(x=hist.index, y=hist["Volume"], name="Volume", marker_color=colors),
    row=2, col=1,
)

# Hide non-trading gaps (weekends + after hours for intraday)
rangebreaks = [dict(bounds=["sat", "mon"])]  # hide weekends
if period in ("1d", "5d"):
    rangebreaks.append(dict(bounds=[20, 4], pattern="hour"))  # hide after-hours (8pm-4am ET)

fig_price.update_layout(
    height=600,
    xaxis_rangeslider_visible=False,
    showlegend=True,
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
)
fig_price.update_xaxes(rangebreaks=rangebreaks, row=1, col=1)
fig_price.update_xaxes(rangebreaks=rangebreaks, row=2, col=1)
fig_price.update_yaxes(title_text="Price ($)", row=1, col=1)
fig_price.update_yaxes(title_text="Volume", row=2, col=1)

st.plotly_chart(fig_price, use_container_width=True)

# --- Realized Volatility chart ---
st.subheader(f"{ticker} — Realized Volatility ({rv_window}d{'  annualized' if rv_annualize else ''})")

rv = compute_realized_vol(hist["Close"], rv_window, rv_annualize)

fig_rv = go.Figure()
fig_rv.add_trace(
    go.Scatter(
        x=hist.index,
        y=rv * 100,
        name=f"{rv_window}d RV",
        line=dict(color="#7e57c2", width=2),
        fill="tozeroy",
        fillcolor="rgba(126,87,194,0.15)",
    )
)
fig_rv.update_layout(
    height=350,
    yaxis_title="Realized Vol (%)",
    xaxis_title="Date",
    xaxis=dict(rangebreaks=rangebreaks),
)
st.plotly_chart(fig_rv, use_container_width=True)

# --- Options IV ---
if show_options:
    st.subheader(f"{ticker} — Implied Volatility (Nearest Expiry)")

    calls, puts, expiry = fetch_options_iv(ticker)

    if calls is not None and not calls.empty:
        st.caption(f"Options expiry: **{expiry}**")

        fig_iv = go.Figure()

        # Filter out zero/NaN IV
        calls_clean = calls[calls["impliedVolatility"] > 0]
        puts_clean = puts[puts["impliedVolatility"] > 0]

        fig_iv.add_trace(
            go.Scatter(
                x=calls_clean["strike"],
                y=calls_clean["impliedVolatility"] * 100,
                name="Calls IV",
                mode="lines+markers",
                line=dict(color="#26a69a"),
            )
        )
        fig_iv.add_trace(
            go.Scatter(
                x=puts_clean["strike"],
                y=puts_clean["impliedVolatility"] * 100,
                name="Puts IV",
                mode="lines+markers",
                line=dict(color="#ef5350"),
            )
        )

        # Mark current price
        fig_iv.add_vline(
            x=latest["Close"],
            line_dash="dash",
            line_color="gray",
            annotation_text=f"Spot: ${latest['Close']:.2f}",
        )

        fig_iv.update_layout(
            height=400,
            xaxis_title="Strike Price ($)",
            yaxis_title="Implied Volatility (%)",
        )
        st.plotly_chart(fig_iv, use_container_width=True)

        # IV summary stats
        atm_calls = calls_clean.iloc[
            (calls_clean["strike"] - latest["Close"]).abs().argsort()[:3]
        ]
        avg_atm_iv = atm_calls["impliedVolatility"].mean() * 100

        iv_col1, iv_col2, iv_col3 = st.columns(3)
        iv_col1.metric("ATM IV (approx)", f"{avg_atm_iv:.1f}%")
        iv_col2.metric(
            "Current RV",
            f"{rv.iloc[-1] * 100:.1f}%" if not np.isnan(rv.iloc[-1]) else "N/A",
        )
        if not np.isnan(rv.iloc[-1]) and avg_atm_iv > 0:
            vol_spread = avg_atm_iv - (rv.iloc[-1] * 100)
            iv_col3.metric("IV - RV Spread", f"{vol_spread:+.1f}%")
    else:
        st.info("No options data available for this ticker.")
