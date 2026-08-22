import re

import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
import numpy as np
from streamlit_searchbox import st_searchbox

from calcs import (
    CRYPTO_ALIASES,
    IV_STRIKE_BAND,
    compute_realized_vol,
    fmt_compact,
    normalize_ticker,
)
from data import (
    fetch_eps_history,
    fetch_earnings,
    fetch_estimates,
    fetch_liquidity_data,
    fetch_ma_data,
    fetch_options_iv,
    fetch_price_data,
    get_company_info,
    search_tickers,
)

st.set_page_config(page_title="Stock Dashboard", layout="wide")
st.title("Stock Dashboard")


# --- Sidebar: ticker search + settings ---
DEFAULT_TICKER = "AAPL"


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
    # Symbols surfaced as options are safe to load when picked later.
    st.session_state.setdefault("seen_symbols", set()).update(
        symbol for _, symbol in suggestions
    )
    return suggestions


with st.sidebar:
    st.header("Settings")
    selection = st_searchbox(
        search_symbols,
        label="Ticker or company name",
        placeholder="AAPL, BTC, Apple …",
        help="Type a ticker (AAPL), crypto (BTC), or a company name like 'Apple', then pick a suggestion.",
        default=DEFAULT_TICKER,
        edit_after_submit="option",
        clear_on_submit=True,
        key="symbol_picker",
    )

    seen_symbols = st.session_state.setdefault("seen_symbols", set())
    ticker = st.session_state.get("active_ticker", DEFAULT_TICKER)
    # The active ticker has already been confirmed by construction.
    seen_symbols.add(ticker)

    # Treat ?t= links (sharing or browser back/forward) as direct entries.
    url_ticker = normalize_ticker((st.query_params.get("t") or "").upper())
    if (
        url_ticker
        and re.fullmatch(r"[A-Z0-9.\-=^]{1,15}", url_ticker)
        and url_ticker != st.session_state.get("synced_ticker")
    ):
        seen_symbols.add(url_ticker)
        ticker = url_ticker

    # Only suggestion values (or crypto aliases) trigger a load; anything
    # else is treated as an in-progress search and keeps the current chart.
    if selection:
        normalized = normalize_ticker(selection)
        if normalized in seen_symbols or normalized in CRYPTO_ALIASES.values():
            ticker = normalized
            seen_symbols.add(normalized)
        else:
            st.info(
                f"No ticker “{normalized}” — pick a suggestion from the search bar."
            )

    st.session_state["active_ticker"] = ticker
    is_crypto = ticker in CRYPTO_ALIASES.values()

    st.divider()
    rv_window = st.slider("RV Window (days)", 5, 60, 20)
    rv_annualize = st.checkbox("Annualize RV", value=True)
    ma_period = st.radio("Moving Average", [100, 200], horizontal=True)

    st.divider()
    show_rv = st.checkbox("Show Realized Vol", value=True)
    show_options = st.checkbox("Show Options IV", value=True)
    show_liquidity = st.checkbox("Show Liquidity", value=False)

    st.divider()
    compare_with = st.text_input(
        "Compare tickers",
        placeholder="MSFT, GOOGL",
        help="Comma- or space-separated symbols, then press Enter.",
    )


period_options = {
    "1D": "1d",
    "5D": "5d",
    "3M": "3mo",
    "6M": "6mo",
    "1Y": "1y",
    "5Y": "5y",
}
# Browser navigation to a different ?p= value wins over stale session state;
# otherwise seed the default once.
url_period = st.query_params.get("p")
if (
    url_period
    and url_period in period_options.values()
    and url_period != st.session_state.get("synced_period")
):
    st.session_state["period"] = url_period
elif "period" not in st.session_state:
    st.session_state["period"] = (
        url_period if url_period in period_options.values() else "1y"
    )
st.session_state.setdefault("synced_period", st.session_state["period"])

period = st.session_state["period"]


def select_period(value: str) -> None:
    """Update the chart time range before Streamlit reruns the page."""
    st.session_state["period"] = value


def sync_query_params(ticker_value: str, period_value: str) -> None:
    """Keep the ?t=/&p= query params aligned with the visible chart."""
    qp = st.query_params
    if qp.get("t") != ticker_value:
        qp["t"] = ticker_value
    if qp.get("p") != period_value:
        qp["p"] = period_value
    st.session_state["synced_ticker"] = ticker_value
    st.session_state["synced_period"] = period_value


# --- Fetch data ---
if not ticker:
    st.warning("Enter a ticker symbol.")
    st.stop()

with st.spinner(f"Fetching {ticker} data..."):
    hist = fetch_price_data(ticker, period)

if hist.empty:
    st.error(f"No data found for **{ticker}**. Check the symbol and try again.")
    st.stop()

company_info = get_company_info(ticker)
company_name = company_info["name"]
st.markdown(f"## {company_name} ({ticker})")

# --- Company info header ---
info_col1, info_col2, info_col3, info_col4 = st.columns(4)
latest = hist.iloc[-1]
prev = hist.iloc[-2] if len(hist) > 1 else latest
prev_close = prev["Close"]
change = latest["Close"] - prev_close
change_pct = (change / prev_close * 100) if (prev_close and not pd.isna(prev_close)) else 0.0
market_cap = company_info["market_cap"]

info_col1.metric("Close", f"${latest['Close']:.2f}", f"{change:+.2f} ({change_pct:+.2f}%)")
info_col2.metric("Volume", fmt_compact(latest["Volume"]))
info_col3.metric("Day Range", f"${latest['Low']:.2f} – {latest['High']:.2f}")
info_col4.metric("Market Cap", fmt_compact(market_cap) if market_cap else "N/A")

wk_low = company_info.get("week_52_low")
wk_high = company_info.get("week_52_high")
pe_ratio = company_info.get("pe_ratio")
beta = company_info.get("beta")
dividend_rate = company_info.get("dividend_rate")

stat_col1, stat_col2, stat_col3, stat_col4 = st.columns(4)
if wk_low and wk_high:
    range_span = wk_high - wk_low
    position = (latest["Close"] - wk_low) / range_span * 100 if range_span > 0 else 50
    stat_col1.metric("52W Range", f"${wk_low:.2f} – {wk_high:.2f}", f"{position:+.0f}% of range")
else:
    stat_col1.metric("52W Range", "N/A")
stat_col2.metric("P/E Ratio", f"{pe_ratio:.1f}" if pe_ratio is not None else "N/A")
stat_col3.metric("Beta", f"{beta:.2f}" if beta is not None else "N/A")
div_yield = (dividend_rate / latest["Close"] * 100) if dividend_rate and latest["Close"] else None
stat_col4.metric("Div Yield", f"{div_yield:.2f}%" if div_yield else "N/A")


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

st.download_button(
    "Download price history (CSV)",
    data=hist[["Open", "High", "Low", "Close", "Volume"]].to_csv(index_label="Date"),
    file_name=f"{ticker}_{period}_history.csv",
    mime="text/csv",
)

# --- Compare mode ---
compare_symbols = []
for part in re.split(r"[,\s]+", compare_with.strip()):
    if part:
        symbol = normalize_ticker(part)
        if symbol != ticker and symbol not in compare_symbols:
            compare_symbols.append(symbol)
compare_symbols = compare_symbols[:3]

if compare_symbols:
    candidates = [ticker] + compare_symbols
    st.subheader(f"Relative Performance vs {ticker}")
    palette = ["#26a69a", "#7e57c2", "#ef5350", "#f9a825"]
    fig_cmp = go.Figure()
    plotted = 0
    for i, symbol in enumerate(candidates):
        cmp_hist = hist if symbol == ticker else fetch_price_data(symbol, period)
        if cmp_hist is None or cmp_hist.empty:
            continue
        closes = cmp_hist["Close"]
        base_close = closes.iloc[0]
        if not base_close or pd.isna(base_close):
            continue
        fig_cmp.add_trace(
            go.Scatter(
                x=closes.index,
                y=(closes / base_close - 1) * 100,
                name=symbol,
                line=dict(color=palette[i % len(palette)], width=2),
            )
        )
        plotted += 1

    if plotted <= 1:
        st.info("No matching data found for the comparison tickers.")
    else:
        fig_cmp.add_hline(y=0, line_dash="dot", line_color="gray")
        # Mixed crypto/equity calendars have incompatible gaps.
        all_equity = all(s not in CRYPTO_ALIASES.values() for s in candidates)
        fig_cmp.update_layout(
            height=350,
            hovermode="x unified",
            yaxis_title="Change since start (%)",
            xaxis=dict(rangebreaks=rangebreaks if all_equity else []),
        )
        st.plotly_chart(fig_cmp, width="stretch")

# --- Earnings (last 4 quarters) ---
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

    # With the full-window requirement, intraday periods (and very short
    # histories) produce no valid points at all.
    if rv.dropna().empty:
        st.info(
            f"Not enough history in this range for a {rv_window}-day window "
            "— pick a longer time range."
        )
    else:
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

sync_query_params(ticker, period)
