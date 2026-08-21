"""Pure calculation helpers shared across the dashboard UI."""

import numpy as np
import pandas as pd

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
    # Require the full window so short histories yield NaN instead of noise.
    rv = log_returns.rolling(window=effective_window, min_periods=effective_window).std()
    if annualize:
        trading_days = 365 if is_crypto else 252
        factor = trading_days * bars_per_day.get(period, 1)
        rv = rv * np.sqrt(factor)
    return rv
