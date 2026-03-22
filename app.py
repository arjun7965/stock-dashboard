import streamlit as st
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

st.set_page_config(page_title="Stock Dashboard", layout="wide")
st.title("Stock Dashboard")

# --- Top bar: ticker search + settings ---
top_col1, top_col2, top_col3, top_col4, top_col5, top_col6 = st.columns([2, 1, 1, 1, 1, 1])
ticker = top_col1.text_input("Ticker", value="AAPL").upper().strip()
rv_window = top_col2.slider("RV Window (days)", 5, 60, 20)
rv_annualize = top_col3.checkbox("Annualize RV", value=True)
ma_period = top_col4.radio("Moving Average", [100, 200], horizontal=True)
show_rv = top_col5.checkbox("Show Realized Vol", value=True)
show_options = top_col6.checkbox("Show Options IV", value=True)


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


def compute_realized_vol(prices: pd.Series, window: int, annualize: bool, period: str) -> pd.Series:
    log_returns = np.log(prices / prices.shift(1))
    # Scale window by bars-per-day for intraday periods so "20d" means 20 trading days
    bars_per_day = {"1d": 78, "5d": 26}  # 6.5hrs: 78 x 5min, 26 x 15min
    effective_window = window * bars_per_day.get(period, 1)
    rv = log_returns.rolling(window=effective_window, min_periods=1).std()
    if annualize:
        factor = 252 * bars_per_day.get(period, 1)
        rv = rv * np.sqrt(factor)
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

# Fetch extended history for MA calculation on shorter periods
@st.cache_data(ttl=300)
def fetch_ma_data(ticker: str, ma_period: int, display_period: str) -> pd.Series:
    """Fetch enough daily history to compute the full moving average across the display range."""
    # Calendar days needed for the display period + MA warmup
    display_days = {"3mo": 90, "6mo": 180, "1y": 365, "5y": 1825}
    total_days = display_days.get(display_period, 365) + int(ma_period * 1.5)  # 1.5x for weekends/holidays
    tk = yf.Ticker(ticker)
    ma_hist = tk.history(period=f"{total_days}d")
    if ma_hist.empty:
        return pd.Series(dtype=float)
    ma_hist.index = ma_hist.index.tz_localize(None)
    return ma_hist["Close"].rolling(ma_period).mean()

if hist.empty:
    st.error(f"No data found for **{ticker}**. Check the symbol and try again.")
    st.stop()

# --- Fetch company name ---
@st.cache_data(ttl=3600)
def get_company_name(ticker: str) -> str:
    try:
        tk = yf.Ticker(ticker)
        info = tk.info or {}
        for key in ("shortName", "longName", "displayName"):
            name = info.get(key)
            if name and name != ticker:
                return name
    except Exception:
        pass
    # Fallback: yf.Search (available in yfinance >= 0.2.31)
    try:
        results = yf.Search(ticker, max_results=1)
        if results.quotes:
            name = results.quotes[0].get("shortname") or results.quotes[0].get("longname")
            if name:
                return name
    except Exception:
        pass
    return ticker

company_name = get_company_name(ticker)
st.markdown(f"## {company_name} ({ticker})")

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
st.subheader(f"{company_name} ({ticker}) — Price & Volume")

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

# Moving average (use extended history so MA covers full display range)
if period not in ("1d", "5d"):
    ma_series = fetch_ma_data(ticker, ma_period, period)
    # Trim to the display period
    ma_display = ma_series.reindex(hist.index)
    if ma_display.notna().any():
        fig_price.add_trace(
            go.Scatter(
                x=ma_display.index,
                y=ma_display,
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
    rangebreaks.append(dict(bounds=[16, 9.5], pattern="hour"))  # hide non-trading hours (4pm-9:30am ET)

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

# --- Earnings (last 4 quarters) ---
@st.cache_data(ttl=3600)
def fetch_earnings(ticker: str) -> pd.DataFrame:
    try:
        tk = yf.Ticker(ticker)
        inc = tk.quarterly_income_stmt
        if inc is None or inc.empty:
            return pd.DataFrame()
        # Grab 5 quarters so we can compute QoQ for the latest 4
        all_cols = inc.columns[:5]
        display_cols = inc.columns[:4]
        rows = {}
        for key in ("Total Revenue", "Net Income"):
            if key in inc.index:
                rows[key] = inc.loc[key, all_cols]
        if not rows:
            return pd.DataFrame()
        raw = pd.DataFrame(rows, index=all_cols)

        # Build display table with QoQ growth
        result = []
        for i, col in enumerate(display_cols):
            row = {"Quarter": col.strftime("%b %Y")}
            for metric in ("Total Revenue", "Net Income"):
                if metric not in raw.columns:
                    continue
                val = raw.loc[col, metric]
                row[metric] = f"${val / 1e9:.2f}B" if pd.notna(val) else "N/A"
                # QoQ: compare to previous quarter (next index since sorted most recent first)
                prev_idx = i + 1
                if prev_idx < len(all_cols):
                    prev_val = raw.loc[all_cols[prev_idx], metric]
                    if pd.notna(val) and pd.notna(prev_val) and prev_val != 0:
                        growth = (val - prev_val) / abs(prev_val) * 100
                        arrow = "🟢 ▲" if growth >= 0 else "🔴 ▼"
                        row[f"{metric} QoQ"] = f"{arrow} {growth:+.1f}%"
                    else:
                        row[f"{metric} QoQ"] = "N/A"
                else:
                    row[f"{metric} QoQ"] = "N/A"
            result.append(row)

        df = pd.DataFrame(result).set_index("Quarter")
        df.index.name = "Quarter"
        return df
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=3600)
def fetch_eps_history(ticker: str) -> pd.DataFrame:
    try:
        tk = yf.Ticker(ticker)
        hist = tk.earnings_history
        if hist is None or hist.empty:
            return pd.DataFrame()
        df = hist[["epsEstimate", "epsActual", "epsDifference", "surprisePercent"]].copy()
        df.index = df.index.strftime("%b %Y")
        df.index.name = "Quarter"

        def fmt_result(row):
            diff = row["epsDifference"]
            pct = row["surprisePercent"]
            if pd.isna(diff):
                return "N/A"
            arrow = "🟢 ▲" if diff >= 0 else "🔴 ▼"
            pct_str = f"{pct * 100:+.1f}%" if pd.notna(pct) else ""
            return f"{arrow} ${diff:+.2f} ({pct_str})"

        df["Result"] = df.apply(fmt_result, axis=1)
        df["EPS Estimate"] = df["epsEstimate"].apply(lambda x: f"${x:.2f}" if pd.notna(x) else "N/A")
        df["EPS Actual"] = df["epsActual"].apply(lambda x: f"${x:.2f}" if pd.notna(x) else "N/A")
        return df[["EPS Estimate", "EPS Actual", "Result"]]
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=3600)
def fetch_estimates(ticker: str):
    try:
        tk = yf.Ticker(ticker)
        eps_est = tk.earnings_estimate
        rev_est = tk.revenue_estimate
        return eps_est, rev_est
    except Exception:
        return None, None


earnings_df = fetch_earnings(ticker)
eps_hist_df = fetch_eps_history(ticker)

if not earnings_df.empty or not eps_hist_df.empty:
    st.subheader(f"{company_name} ({ticker}) — Earnings")

    if not earnings_df.empty:
        st.caption("**Revenue & Income (Last 4 Quarters)**")
        st.dataframe(earnings_df, use_container_width=True)

    if not eps_hist_df.empty:
        st.caption("**EPS: Analyst Estimate vs Actual**")
        st.dataframe(eps_hist_df, use_container_width=True)

    # Forward estimates
    eps_est, rev_est = fetch_estimates(ticker)
    if eps_est is not None and not eps_est.empty:
        st.caption("**Forward Estimates**")
        est_col1, est_col2 = st.columns(2)
        with est_col1:
            st.markdown("**EPS Estimates**")
            eps_display = eps_est[["avg", "low", "high", "numberOfAnalysts"]].copy()
            eps_display.columns = ["Avg", "Low", "High", "# Analysts"]
            eps_display.index = ["Current Qtr", "Next Qtr", "Current Year", "Next Year"]
            for col in ("Avg", "Low", "High"):
                eps_display[col] = eps_display[col].apply(lambda x: f"${x:.2f}" if pd.notna(x) else "N/A")
            eps_display["# Analysts"] = eps_display["# Analysts"].astype(int)
            st.dataframe(eps_display, use_container_width=True)
        if rev_est is not None and not rev_est.empty:
            with est_col2:
                st.markdown("**Revenue Estimates**")
                rev_display = rev_est[["avg", "low", "high", "numberOfAnalysts"]].copy()
                rev_display.columns = ["Avg", "Low", "High", "# Analysts"]
                rev_display.index = ["Current Qtr", "Next Qtr", "Current Year", "Next Year"]
                for col in ("Avg", "Low", "High"):
                    rev_display[col] = rev_display[col].apply(lambda x: f"${x / 1e9:.2f}B" if pd.notna(x) else "N/A")
                rev_display["# Analysts"] = rev_display["# Analysts"].astype(int)
                st.dataframe(rev_display, use_container_width=True)

# --- Realized Volatility ---
rv = compute_realized_vol(hist["Close"], rv_window, rv_annualize, period)

if show_rv:
    st.subheader(f"{company_name} ({ticker}) — Realized Volatility ({rv_window}d{'  annualized' if rv_annualize else ''})")

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
    st.subheader(f"{company_name} ({ticker}) — Implied Volatility (Nearest Expiry)")

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
