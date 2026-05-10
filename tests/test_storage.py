"""src.data.storage 테스트. 실제 parquet I/O는 tmp_path."""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from src.data import storage


def _row(code: str, d: date, close: int) -> list:
    return [code, d, close - 500, close + 500, close - 1000, close, 1_000_000, close * 1_000_000, 1.5]


def _make(rows: list[list]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=storage.DAILY_OHLCV_COLUMNS)


def test_read_returns_empty_when_missing(tmp_path):
    df = storage.read_daily_ohlcv(tmp_path)
    assert df.empty
    assert list(df.columns) == storage.DAILY_OHLCV_COLUMNS


def test_write_then_read_roundtrip(tmp_path):
    df = _make([_row("005930", date(2025, 5, 2), 70500)])
    storage.write_daily_ohlcv(df, tmp_path)

    out = storage.read_daily_ohlcv(tmp_path)
    assert len(out) == 1
    assert out.loc[0, "code"] == "005930"
    assert out.loc[0, "close"] == 70500


def test_upsert_replaces_duplicates(tmp_path):
    storage.upsert_daily_ohlcv(_make([_row("005930", date(2025, 5, 2), 70500)]), tmp_path)
    storage.upsert_daily_ohlcv(_make([_row("005930", date(2025, 5, 2), 71000)]), tmp_path)

    out = storage.read_daily_ohlcv(tmp_path)
    assert len(out) == 1
    assert out.loc[0, "close"] == 71000


def test_upsert_appends_new_rows(tmp_path):
    storage.upsert_daily_ohlcv(_make([_row("005930", date(2025, 5, 2), 70500)]), tmp_path)
    storage.upsert_daily_ohlcv(_make([_row("000660", date(2025, 5, 2), 204000)]), tmp_path)

    out = storage.read_daily_ohlcv(tmp_path)
    assert len(out) == 2
    assert set(out["code"]) == {"005930", "000660"}


def test_upsert_empty_input_is_noop(tmp_path):
    storage.upsert_daily_ohlcv(_make([_row("005930", date(2025, 5, 2), 70500)]), tmp_path)
    storage.upsert_daily_ohlcv(pd.DataFrame(columns=storage.DAILY_OHLCV_COLUMNS), tmp_path)

    assert len(storage.read_daily_ohlcv(tmp_path)) == 1


def test_latest_loaded_date(tmp_path):
    df = _make([
        _row("005930", date(2025, 5, 1), 70500),
        _row("005930", date(2025, 5, 2), 71000),
        _row("000660", date(2025, 4, 30), 200000),
    ])
    storage.upsert_daily_ohlcv(df, tmp_path)
    assert storage.latest_loaded_date(tmp_path) == date(2025, 5, 2)


def test_latest_loaded_date_none_when_empty(tmp_path):
    assert storage.latest_loaded_date(tmp_path) is None


def test_loaded_dates(tmp_path):
    df = _make([
        _row("005930", date(2025, 5, 1), 70500),
        _row("005930", date(2025, 5, 2), 71000),
        _row("000660", date(2025, 5, 1), 200000),
    ])
    storage.upsert_daily_ohlcv(df, tmp_path)
    assert storage.loaded_dates(tmp_path) == {date(2025, 5, 1), date(2025, 5, 2)}


def test_stock_master_roundtrip(tmp_path):
    df = pd.DataFrame(
        [
            {"code": "005930", "name": "삼성전자", "market": "KOSPI", "market_cap": 500_000_000_000_000, "listed_at": None},
            {"code": "035720", "name": "카카오", "market": "KOSPI", "market_cap": 30_000_000_000_000, "listed_at": None},
        ],
        columns=storage.STOCK_MASTER_COLUMNS,
    )
    storage.write_stock_master(df, tmp_path)
    out = storage.read_stock_master(tmp_path)
    assert len(out) == 2
    assert out.loc[out["code"] == "005930", "name"].iloc[0] == "삼성전자"


# ── compute_change_rate (M5: pct_change fallback) ────────────────────────────


def test_compute_change_rate_basic():
    df = pd.DataFrame([
        {"code": "A", "date": date(2025, 5, 1), "close": 1000},
        {"code": "A", "date": date(2025, 5, 2), "close": 1100},
        {"code": "A", "date": date(2025, 5, 7), "close": 1320},
    ])
    out = storage.compute_change_rate(df)
    rates = out.sort_values("date")["change_rate"].tolist()
    # 첫날 NaN
    assert pd.isna(rates[0])
    # 1100/1000 - 1 = 10%
    assert abs(rates[1] - 10.0) < 1e-6
    # 1320/1100 - 1 = 20%
    assert abs(rates[2] - 20.0) < 1e-6


def test_compute_change_rate_separates_codes():
    """한 종목의 직전일이 다른 종목으로 새지 않는다."""
    df = pd.DataFrame([
        {"code": "A", "date": date(2025, 5, 1), "close": 1000},
        {"code": "B", "date": date(2025, 5, 1), "close": 5000},
        {"code": "A", "date": date(2025, 5, 2), "close": 1100},
        {"code": "B", "date": date(2025, 5, 2), "close": 5500},
    ])
    out = storage.compute_change_rate(df).sort_values(["code", "date"])
    assert pd.isna(out.iloc[0]["change_rate"])  # A 첫날
    assert pd.isna(out.iloc[2]["change_rate"])  # B 첫날
    # A 둘째날 +10%, B 둘째날 +10%
    assert abs(out.iloc[1]["change_rate"] - 10.0) < 1e-6
    assert abs(out.iloc[3]["change_rate"] - 10.0) < 1e-6


def test_compute_change_rate_input_not_mutated():
    df = pd.DataFrame([
        {"code": "A", "date": date(2025, 5, 1), "close": 1000, "change_rate": pd.NA},
        {"code": "A", "date": date(2025, 5, 2), "close": 1100, "change_rate": pd.NA},
    ])
    df_before = df.copy()
    storage.compute_change_rate(df)
    pd.testing.assert_frame_equal(df, df_before)


def test_compute_change_rate_empty():
    out = storage.compute_change_rate(pd.DataFrame())
    assert out.empty


def test_compute_change_rate_adds_column_if_missing():
    df = pd.DataFrame([
        {"code": "A", "date": date(2025, 5, 1), "close": 1000},
        {"code": "A", "date": date(2025, 5, 2), "close": 1100},
    ])  # change_rate 컬럼 없음
    out = storage.compute_change_rate(df)
    assert "change_rate" in out.columns
