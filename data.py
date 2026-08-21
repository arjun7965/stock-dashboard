"""Yahoo Finance data access with Streamlit caching."""

import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np


def drop_tz(df: pd.DataFrame) -> pd.DataFrame:
    """Return df with any timezone-aware DatetimeIndex made naive."""
    idx = df.index
    if isinstance(idx, pd.DatetimeIndex) and idx.tz is not None:
        df.index = idx.tz_localize(None)
    return df


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
    hist = hist.dropna(subset=["Close"])
    if hist.empty:
        return pd.DataFrame()
    return drop_tz(hist)


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
    ma_hist = drop_tz(ma_hist)
    return ma_hist["Close"].rolling(ma_period).mean()


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


@st.cache_data(ttl=300)
def fetch_liquidity_data(ticker: str):
    tk = yf.Ticker(ticker)
    info = tk.info or {}
    hist_30d = tk.history(period="1mo")
    if hist_30d is None or hist_30d.empty:
        return None

    hist_30d = drop_tz(hist_30d)
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
