"""Unit tests for the pure calculation helpers in calcs.py."""

import numpy as np
import pandas as pd
import pytest

from calcs import compute_realized_vol, fmt_compact, normalize_ticker


def daily_series(values) -> pd.Series:
    return pd.Series(np.asarray(values, dtype=float), index=pd.bdate_range("2024-01-01", periods=len(values)))


class TestNormalizeTicker:
    def test_crypto_alias_btc(self):
        assert normalize_ticker("BTC") == "BTC-USD"

    def test_crypto_alias_eth_is_case_insensitive(self):
        assert normalize_ticker("eth") == "ETH-USD"

    def test_strips_whitespace(self):
        assert normalize_ticker("  aapl  ") == "AAPL"

    def test_unknown_symbol_passes_through_uppercased(self):
        assert normalize_ticker("msft") == "MSFT"


class TestFmtCompact:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (float("nan"), "N/A"),
            (pd.NA, "N/A"),
            (None, "N/A"),
            (0, "0"),
            (999, "999"),
            (1_500, "1.5K"),
            (2_500_000, "2.5M"),
            (3_100_000_000, "3.1B"),
            (1_200_000_000_000, "1.2T"),
            (-1_500_000, "-1.5M"),
        ],
    )
    def test_formats(self, value, expected):
        assert fmt_compact(value) == expected


class TestComputeRealizedVol:
    def test_flat_prices_yield_zero_vol(self):
        rv = compute_realized_vol(daily_series([100.0] * 40), window=20, annualize=False, period="1y")
        assert rv.dropna().eq(0).all()

    def test_requires_full_window(self):
        # 10 prices give 9 returns; a 20d window must produce no estimates.
        rv = compute_realized_vol(daily_series(np.linspace(100, 110, 10)), window=20, annualize=False, period="1y")
        assert rv.isna().all()

    def test_head_is_nan_until_window_fills(self):
        rng = np.random.default_rng(42)
        prices = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 60))))
        rv = compute_realized_vol(prices, window=20, annualize=False, period="1y")
        assert rv.iloc[:20].isna().all()
        assert rv.iloc[20:].notna().all()

    def test_annualization_factor_daily_equity(self):
        rng = np.random.default_rng(7)
        prices = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.02, 120))))
        plain = compute_realized_vol(prices, window=30, annualize=False, period="1y")
        annualized = compute_realized_vol(prices, window=30, annualize=True, period="1y")
        assert np.allclose((annualized / plain).dropna(), np.sqrt(252))

    def test_intraday_period_without_enough_bars_is_all_nan(self):
        # Regression: a 5d RV window on a 1D chart used to emit noisy values.
        rv = compute_realized_vol(
            daily_series(np.linspace(50, 60, 100)), window=5, annualize=True, period="1d"
        )
        assert rv.isna().all()

    def test_crypto_uses_365_day_factor(self):
        bars = 96 * 60  # enough for a 5d crypto window of 480 bars
        rng = np.random.default_rng(9)
        prices = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, bars))))
        plain = compute_realized_vol(prices, window=5, annualize=False, period="5d", is_crypto=True)
        annualized = compute_realized_vol(prices, window=5, annualize=True, period="5d", is_crypto=True)
        assert np.allclose((annualized / plain).dropna(), np.sqrt(365 * 96))
