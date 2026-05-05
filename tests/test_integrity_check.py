"""src.data.integrity_check 테스트."""
from __future__ import annotations

from datetime import date

import pandas as pd

from src.data import integrity_check as ic


def _row(code: str, d: date, close: int) -> dict:
    return {
        "code": code,
        "date": d,
        "open": close - 100,
        "high": close + 100,
        "low": close - 200,
        "close": close,
        "volume": 1_000_000,
        "trading_value": close * 1_000_000,
        "change_rate": pd.NA,
    }


def _make(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["close"] = df["close"].astype("Int64")
    return df


# ─────────────────────────────────────────
# check_recent_coverage
# ─────────────────────────────────────────


def test_coverage_passes_when_within_threshold():
    df = _make(
        [_row(f"00000{i}", date(2025, 5, 1), 1000) for i in range(100)]
        + [_row(f"00000{i}", date(2025, 5, 2), 1000) for i in range(98)]
    )
    ok, msg = ic.check_recent_coverage(df, 0.95)
    assert ok is True
    assert "98" in msg


def test_coverage_fails_below_threshold():
    df = _make(
        [_row(f"00000{i}", date(2025, 5, 1), 1000) for i in range(100)]
        + [_row(f"00000{i}", date(2025, 5, 2), 1000) for i in range(80)]
    )
    ok, _ = ic.check_recent_coverage(df, 0.95)
    assert ok is False


def test_coverage_single_date_returns_ok():
    df = _make([_row("005930", date(2025, 5, 1), 70000)])
    ok, msg = ic.check_recent_coverage(df, 0.95)
    assert ok is True
    assert "비교할" in msg


def test_coverage_empty_df():
    ok, _ = ic.check_recent_coverage(pd.DataFrame(), 0.95)
    assert ok is False


# ─────────────────────────────────────────
# find_price_outliers
# ─────────────────────────────────────────


def test_no_outliers_when_within_threshold():
    df = _make(
        [
            _row("005930", date(2025, 5, 1), 70000),
            _row("005930", date(2025, 5, 2), 71000),  # +1.4%
            _row("005930", date(2025, 5, 5), 72000),  # +1.4%
        ]
    )
    out = ic.find_price_outliers(df, 0.5)
    assert out.empty


def test_detects_50pct_jump():
    df = _make(
        [
            _row("005930", date(2025, 5, 1), 10000),
            _row("005930", date(2025, 5, 2), 16000),  # +60%
        ]
    )
    out = ic.find_price_outliers(df, 0.5)
    assert len(out) == 1
    assert out.iloc[0]["code"] == "005930"
    assert out.iloc[0]["date"] == date(2025, 5, 2)


def test_outliers_per_code_independent():
    df = _make(
        [
            _row("005930", date(2025, 5, 1), 10000),
            _row("005930", date(2025, 5, 2), 10100),  # +1%
            _row("000660", date(2025, 5, 1), 10000),
            _row("000660", date(2025, 5, 2), 16000),  # +60%
        ]
    )
    out = ic.find_price_outliers(df, 0.5)
    assert len(out) == 1
    assert out.iloc[0]["code"] == "000660"


# ─────────────────────────────────────────
# find_weekend_rows
# ─────────────────────────────────────────


def test_no_weekend_rows_normally():
    df = _make([_row("005930", date(2025, 5, 2), 70000)])  # 금
    assert ic.find_weekend_rows(df).empty


def test_detects_saturday_row():
    df = _make([_row("005930", date(2025, 5, 3), 70000)])  # 토
    out = ic.find_weekend_rows(df)
    assert len(out) == 1


def test_detects_sunday_row():
    df = _make([_row("005930", date(2025, 5, 4), 70000)])  # 일
    out = ic.find_weekend_rows(df)
    assert len(out) == 1


def test_weekend_check_empty_df():
    assert ic.find_weekend_rows(pd.DataFrame()).empty
