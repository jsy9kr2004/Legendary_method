"""src.data.index_storage round-trip 테스트."""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from src.data.index_storage import (
    INDEX_DAILY_COLUMNS,
    index_daily_path,
    latest_loaded_index_date,
    read_index_daily,
    upsert_index_daily,
    write_index_daily,
)


def test_read_empty_returns_schema(tmp_path):
    df = read_index_daily(tmp_path, "0001")
    assert df.empty
    assert list(df.columns) == INDEX_DAILY_COLUMNS


def test_index_daily_path_kospi(tmp_path):
    p = index_daily_path(tmp_path, "0001")
    assert p.name == "kospi.parquet"
    assert p.parent.name == "index"


def test_index_daily_path_kosdaq(tmp_path):
    assert index_daily_path(tmp_path, "1001").name == "kosdaq.parquet"


def test_index_daily_path_invalid_code(tmp_path):
    with pytest.raises(ValueError):
        index_daily_path(tmp_path, "9999")


def test_write_then_read_round_trip(tmp_path):
    df = pd.DataFrame([
        {"date": date(2026, 5, 4), "close": 2680.45},
        {"date": date(2026, 5, 6), "close": 2701.20},
    ])
    write_index_daily(df, tmp_path, "0001")
    loaded = read_index_daily(tmp_path, "0001")
    assert len(loaded) == 2
    assert loaded.iloc[0]["close"] == 2680.45


def test_write_sorts_ascending(tmp_path):
    df = pd.DataFrame([
        {"date": date(2026, 5, 6), "close": 2701.20},
        {"date": date(2026, 5, 4), "close": 2680.45},
    ])
    write_index_daily(df, tmp_path, "0001")
    loaded = read_index_daily(tmp_path, "0001")
    assert loaded.iloc[0]["date"] == date(2026, 5, 4)
    assert loaded.iloc[1]["date"] == date(2026, 5, 6)


def test_upsert_appends_new(tmp_path):
    write_index_daily(
        pd.DataFrame([{"date": date(2026, 5, 4), "close": 2680.45}]),
        tmp_path, "0001",
    )
    n = upsert_index_daily(
        pd.DataFrame([{"date": date(2026, 5, 6), "close": 2701.20}]),
        tmp_path, "0001",
    )
    assert n == 2
    loaded = read_index_daily(tmp_path, "0001")
    assert len(loaded) == 2


def test_upsert_overwrites_existing_date(tmp_path):
    """동일 date 면 신규 값으로 덮어쓰기."""
    write_index_daily(
        pd.DataFrame([{"date": date(2026, 5, 4), "close": 2680.45}]),
        tmp_path, "0001",
    )
    upsert_index_daily(
        pd.DataFrame([{"date": date(2026, 5, 4), "close": 2999.99}]),
        tmp_path, "0001",
    )
    loaded = read_index_daily(tmp_path, "0001")
    assert len(loaded) == 1
    assert loaded.iloc[0]["close"] == 2999.99


def test_upsert_empty_returns_existing_count(tmp_path):
    write_index_daily(
        pd.DataFrame([{"date": date(2026, 5, 4), "close": 2680.45}]),
        tmp_path, "0001",
    )
    n = upsert_index_daily(pd.DataFrame(), tmp_path, "0001")
    assert n == 1


def test_latest_loaded_date_empty(tmp_path):
    assert latest_loaded_index_date(tmp_path, "0001") is None


def test_latest_loaded_date_returns_max(tmp_path):
    write_index_daily(
        pd.DataFrame([
            {"date": date(2026, 5, 4), "close": 2680.45},
            {"date": date(2026, 5, 6), "close": 2701.20},
            {"date": date(2026, 5, 2), "close": 2660.10},
        ]),
        tmp_path, "0001",
    )
    assert latest_loaded_index_date(tmp_path, "0001") == date(2026, 5, 6)
