import streamlit as st
import yfinance as yf
from streamlit_searchbox import st_searchbox
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

st.set_page_config(page_title="Stock Dashboard", layout="wide")
st.title("Stock Dashboard")

CRYPTO_ALIASES = {
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
}

# Strikes within +/- this fraction of spot are kept when plotting the IV
# smile; deep ITM/OTM quotes carry unreliable IV that squashes the curve.
IV_STRIKE_BAND = 0.4


def normalize_ticker(symbol: str) -> str:
    """Map common crypto symbols to their Yahoo Finance tickers."""
    normalized = symbol.upper().strip()
    return CRYPTO_ALIASES.get(normalized, normalized)


def fmt_compact(value: float) -> str:
    """Format large numbers with K/M/B/T suffixes."""
    if pd.isna(value):
        return "N/A"
    for threshold, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= threshold:
            return f"{value / threshold:.1f}{suffix}"
    return f"{value:,.0f}"


@st.cache_data(ttl=3600)
def search_tickers(query: str) -> list[dict]:
    """Look up ticker symbols by symbol fragment or company name."""
    try:
        results = yf.Search(query, max_results=8)
        quotes = results.quotes or []
    except Exception:
        return []
    tradable_types = {"EQUITY", "ETF", "MUTUALFUND", "CRYPTOCURRENCY", "INDEX"}
    matches = []
    seen = set()
    for quote in quotes:
        symbol = quote.get("symbol")
        name = quote.get("shortname") or quote.get("longname")
        if not symbol or not name or symbol in seen:
            continue
        if quote.get("quoteType") not in tradable_types:
            continue
        seen.add(symbol)
        matches.append({
            "symbol": symbol,
            "name": " ".join(name.split()),
            "exchange": quote.get("exchDisp") or quote.get("exchange") or "",
        })
    return matches


# --- Sidebar: ticker search + settings ---
def search_symbols(query: str) -> list[tuple[str, str]]:
    """Build labeled autocomplete suggestions from Yahoo ticker matches."""
    query = query.strip()
    if len(query) < 2:
        return []
    suggestions = []
    for match in search_tickers(query):
        name = match["name"]
        if len(name) > 40:
            name = name[:37] + "…"
        label = f"{match['symbol']} · {name}"
        if match["exchange"]:
            label += f" ({match['exchange']})"
        suggestions.append((label, match["symbol"]))
    return suggestions


with st.sidebar:
    st.header("Settings")
    selection = st_searchbox(
        search_symbols,
        label="Ticker or company name",
        placeholder="AAPL, BTC, Apple …",
        help="Type a ticker (AAPL), crypto (BTC), or a company name like 'Apple'.",
        default_searchterm="AAPL",
        default_use_searchterm=True,
        edit_after_submit="option",
        clear_on_submit=True,
        key="symbol_picker",
    )
    ticker = normalize_ticker(selection or "AAPL")
    is_crypto = ticker in CRYPTO_ALIASES.values()

    st.divider()
    rv_window = st.slider("RV Window (days)", 5, 60, 20)
    rv_annualize = st.checkbox("Annualize RV", value=True)
    ma_period = st.radio("Moving Average", [100, 200], horizontal=True)

    st.divider()
    show_rv = st.checkbox("Show Realized Vol", value=True)
    show_options = st.checkbox("Show Options IV", value=True)
    show_liquidity = st.checkbox("Show Liquidity", value=False)


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
    if hist is None or hist.empty:
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


def compute_realized_vol(
    prices: pd.Series,
    window: int,
    annualize: bool,
    period: str,
    is_crypto: bool = False,
) -> pd.Series:
    log_returns = np.log(prices / prices.shift(1))
    # Scale intraday windows by each market's bars per day.
    bars_per_day = {"1d": 288, "5d": 96} if is_crypto else {"1d": 78, "5d": 26}
    effective_window = window * bars_per_day.get(period, 1)
    rv = log_returns.rolling(window=effective_window, min_periods=1).std()
    if annualize:
        trading_days = 365 if is_crypto else 252
        factor = trading_days * bars_per_day.get(period, 1)
        rv = rv * np.sqrt(factor)
    return rv


period_options = {
    "1D": "1d",
    "5D": "5d",
    "3M": "3mo",
    "6M": "6mo",
    "1Y": "1y",
    "5Y": "5y",
}
if "period" not in st.session_state:
    st.session_state["period"] = "1y"

period = st.session_state["period"]


def select_period(value: str) -> None:
    """Update the chart time range before Streamlit reruns the page."""
    st.session_state["period"] = value


# --- Fetch data ---
if not ticker:
    st.warning("Enter a ticker symbol.")
    st.stop()

with st.spinner(f"Fetching {ticker} data..."):
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
    if ma_hist is None or ma_hist.empty:
        return pd.Series(dtype=float)
    ma_hist.index = ma_hist.index.tz_localize(None)
    return ma_hist["Close"].rolling(ma_period).mean()

if hist.empty:
    st.error(f"No data found for **{ticker}**. Check the symbol and try again.")
    st.stop()

# --- Fetch company metadata ---
@st.cache_data(ttl=3600)
def get_company_info(ticker: str) -> dict:
    """Best-effort company display name and market cap."""
    try:
        tk = yf.Ticker(ticker)
        info = tk.info or {}
        for key in ("shortName", "longName", "displayName"):
            name = info.get(key)
            if name and name != ticker:
                return {"name": name, "market_cap": info.get("marketCap") or 0}
    except Exception:
        pass
    # Fallback: yf.Search (available in yfinance >= 0.2.31)
    try:
        results = yf.Search(ticker, max_results=1)
        if results.quotes:
            quote = results.quotes[0]
            name = quote.get("shortname") or quote.get("longname")
            if name:
                return {"name": name, "market_cap": quote.get("marketCap") or 0}
    except Exception:
        pass
    return {"name": ticker, "market_cap": 0}

company_info = get_company_info(ticker)
company_name = company_info["name"]
st.markdown(f"## {company_name} ({ticker})")

# --- Company info header ---
info_col1, info_col2, info_col3, info_col4 = st.columns(4)
latest = hist.iloc[-1]
prev = hist.iloc[-2] if len(hist) > 1 else latest
change = latest["Close"] - prev["Close"]
change_pct = (change / prev["Close"]) * 100
market_cap = company_info["market_cap"]

info_col1.metric("Close", f"${latest['Close']:.2f}", f"{change:+.2f} ({change_pct:+.2f}%)")
info_col2.metric("Volume", fmt_compact(latest["Volume"]))
info_col3.metric("Day Range", f"${latest['Low']:.2f} – {latest['High']:.2f}")
info_col4.metric("Market Cap", fmt_compact(market_cap) if market_cap else "N/A")


# --- Liquidity ---
@st.cache_data(ttl=300)
def fetch_liquidity_data(ticker: str):
    tk = yf.Ticker(ticker)
    info = tk.info or {}
    hist_30d = tk.history(period="1mo")
    if hist_30d is None or hist_30d.empty:
        return None

    hist_30d.index = hist_30d.index.tz_localize(None)
    vol = hist_30d["Volume"]
    price = hist_30d["Close"]
    dollar_vol = vol * price

    bid = info.get("bid", 0) or 0
    ask = info.get("ask", 0) or 0
    mid = (bid + ask) / 2 if bid and ask else 0
    spread = ask - bid if bid and ask else 0
    spread_pct = (spread / mid * 100) if mid else 0

    shares = info.get("sharesOutstanding", 0) or 0
    turnover = (vol / shares * 100).mean() if shares else 0

    # Amihud illiquidity ratio
    returns = np.log(price / price.shift(1)).dropna()
    dv = dollar_vol.iloc[1:]
    amihud = (returns.abs() / dv).mean() * 1e6 if (dv > 0).all() else 0

    # Intraday range
    intraday_range = ((hist_30d["High"] - hist_30d["Low"]) / hist_30d["Close"] * 100).mean()

    return {
        "bid": bid, "ask": ask, "spread": spread, "spread_pct": spread_pct,
        "avg_vol_10d": info.get("averageVolume10days", 0) or 0,
        "avg_vol_3mo": info.get("averageVolume", 0) or 0,
        "avg_dollar_vol": dollar_vol.mean(),
        "turnover": turnover,
        "amihud": amihud,
        "intraday_range": intraday_range,
        "market_cap": info.get("marketCap", 0) or 0,
        "shares": shares,
    }


# --- Price + Volume chart ---
st.subheader("Price & Volume")
st.caption("Time range")
period_cols = st.columns(len(period_options) + 4)  # extra cols keep the control compact
for i, (label, value) in enumerate(period_options.items()):
    period_cols[i].button(
        label,
        key=f"period_{value}",
        type="primary" if period == value else "secondary",
        width="stretch",
        on_click=select_period,
        args=(value,),
    )

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
        increasing_line_color="#26a69a",
        decreasing_line_color="#ef5350",
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

# Hide non-trading gaps for equities; cryptocurrencies trade continuously.
rangebreaks = [] if is_crypto else [dict(bounds=["sat", "mon"])]
if not is_crypto and period in ("1d", "5d"):
    rangebreaks.append(dict(bounds=[16, 9.5], pattern="hour"))  # hide non-trading hours (4pm-9:30am ET)

fig_price.update_layout(
    height=600,
    xaxis_rangeslider_visible=False,
    showlegend=True,
    hovermode="x unified",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
)
fig_price.update_xaxes(rangebreaks=rangebreaks, row=1, col=1)
fig_price.update_xaxes(rangebreaks=rangebreaks, row=2, col=1)
fig_price.update_yaxes(title_text="Price ($)", tickprefix="$", row=1, col=1)
fig_price.update_yaxes(title_text="Volume", row=2, col=1)

st.plotly_chart(fig_price, width="stretch")

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


earnings_df = pd.DataFrame() if is_crypto else fetch_earnings(ticker)
eps_hist_df = pd.DataFrame() if is_crypto else fetch_eps_history(ticker)

if not earnings_df.empty or not eps_hist_df.empty:
    st.subheader("Earnings")

    if not earnings_df.empty:
        st.caption("**Revenue & Income (Last 4 Quarters)**")
        st.dataframe(earnings_df, width="stretch")

    if not eps_hist_df.empty:
        st.caption("**EPS: Analyst Estimate vs Actual**")
        st.dataframe(eps_hist_df, width="stretch")

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
            st.dataframe(eps_display, width="stretch")
        if rev_est is not None and not rev_est.empty:
            with est_col2:
                st.markdown("**Revenue Estimates**")
                rev_display = rev_est[["avg", "low", "high", "numberOfAnalysts"]].copy()
                rev_display.columns = ["Avg", "Low", "High", "# Analysts"]
                rev_display.index = ["Current Qtr", "Next Qtr", "Current Year", "Next Year"]
                for col in ("Avg", "Low", "High"):
                    rev_display[col] = rev_display[col].apply(lambda x: f"${x / 1e9:.2f}B" if pd.notna(x) else "N/A")
                rev_display["# Analysts"] = rev_display["# Analysts"].astype(int)
                st.dataframe(rev_display, width="stretch")

# --- Liquidity table (below earnings) ---
if show_liquidity and is_crypto:
    st.info("Equity liquidity metrics are not available for spot cryptocurrencies.")
elif show_liquidity:
    liq = fetch_liquidity_data(ticker)
    if liq:
        st.subheader("Liquidity")
        avg_vol = liq["avg_vol_3mo"]
        sp = liq["spread_pct"]
        if avg_vol > 1_000_000 and sp < 0.1:
            assessment = "🟢 HIGH — tight spreads, strong volume"
        elif avg_vol > 500_000 and sp < 0.3:
            assessment = "🟡 MODERATE — reasonable spreads and volume"
        elif avg_vol > 100_000:
            assessment = "🟠 LOW-MODERATE — watch for slippage"
        else:
            assessment = "🔴 LOW — significant market impact risk"

        liq_table = pd.DataFrame({
            "Metric": [
                "Bid / Ask",
                "Spread",
                "Market Cap",
                "Shares Outstanding",
                "Avg Volume (10d)",
                "Avg Volume (3mo)",
                "Avg Dollar Volume",
                "Daily Turnover",
                "Avg Intraday Range",
                "Amihud Illiquidity (×10⁶)",
                "Assessment",
            ],
            "Value": [
                f"${liq['bid']:.2f} / ${liq['ask']:.2f}",
                f"${liq['spread']:.4f} ({liq['spread_pct']:.2f}%)",
                f"${liq['market_cap'] / 1e9:.2f}B" if liq["market_cap"] >= 1e9 else f"${liq['market_cap'] / 1e6:.0f}M",
                f"{liq['shares'] / 1e6:.1f}M" if liq["shares"] else "N/A",
                f"{liq['avg_vol_10d']:,.0f}",
                f"{avg_vol:,.0f}",
                f"${liq['avg_dollar_vol'] / 1e6:,.1f}M",
                f"{liq['turnover']:.2f}%",
                f"{liq['intraday_range']:.2f}%",
                f"{liq['amihud']:.4f}",
                assessment,
            ],
        }).set_index("Metric")
        st.dataframe(liq_table, width="stretch")
        st.markdown(
            '<sup>ℹ️ <b>Amihud Illiquidity Ratio</b> measures price impact per dollar traded. Lower = more liquid.</sup>',
            unsafe_allow_html=True,
        )

# --- Realized Volatility ---
rv = compute_realized_vol(
    hist["Close"],
    rv_window,
    rv_annualize,
    period,
    is_crypto=is_crypto,
)

if show_rv:
    st.subheader(f"Realized Volatility — {rv_window}d{' (annualized)' if rv_annualize else ''}")

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
    st.plotly_chart(fig_rv, width="stretch")

def render_iv_section(ticker: str, spot: float, rv: pd.Series) -> None:
    """Render the IV smile chart plus ATM IV vs RV summary stats."""
    st.subheader("Implied Volatility — Nearest Expiry")

    calls, puts, expiry = fetch_options_iv(ticker)

    if calls is None or calls.empty:
        st.info("No options data available for this ticker.")
        return

    st.caption(f"Options expiry: **{expiry}**")

    # Filter out zero/NaN IV plus unreliable deep ITM/OTM quotes that
    # spike the y-axis and squash the visible smile.
    tradable_band = (spot * (1 - IV_STRIKE_BAND), spot * (1 + IV_STRIKE_BAND))
    calls_clean = calls[
        calls["impliedVolatility"].between(0.0001, 5)
        & calls["strike"].between(*tradable_band)
    ]
    puts_clean = puts[
        puts["impliedVolatility"].between(0.0001, 5)
        & puts["strike"].between(*tradable_band)
    ]

    if calls_clean.empty and puts_clean.empty:
        st.info("No reliable options data available for this ticker.")
        return

    fig_iv = go.Figure()

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
        x=spot,
        line_dash="dash",
        line_color="gray",
        annotation_text=f"Spot: ${spot:.2f}",
    )

    fig_iv.update_layout(
        height=400,
        xaxis_title="Strike Price ($)",
        yaxis_title="Implied Volatility (%)",
    )
    st.plotly_chart(fig_iv, width="stretch")

    # IV summary stats
    avg_atm_iv = float("nan")
    if not calls_clean.empty:
        atm_calls = calls_clean.iloc[
            (calls_clean["strike"] - spot).abs().argsort()[:3]
        ]
        avg_atm_iv = atm_calls["impliedVolatility"].mean() * 100

    iv_col1, iv_col2, iv_col3 = st.columns(3)
    iv_col1.metric(
        "ATM IV (approx)",
        f"{avg_atm_iv:.1f}%" if not np.isnan(avg_atm_iv) else "N/A",
    )
    iv_col2.metric(
        "Current RV",
        f"{rv.iloc[-1] * 100:.1f}%" if not np.isnan(rv.iloc[-1]) else "N/A",
    )
    if not np.isnan(avg_atm_iv) and not np.isnan(rv.iloc[-1]):
        vol_spread = avg_atm_iv - (rv.iloc[-1] * 100)
        iv_col3.metric("IV - RV Spread", f"{vol_spread:+.1f}%")


# --- Options IV ---
if show_options and is_crypto:
    st.info("Listed options IV is not available for spot cryptocurrencies.")
elif show_options:
    render_iv_section(ticker, latest["Close"], rv)
