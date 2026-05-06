"""src.data.snapshot 테스트."""
from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import pytz
import pytest

from src.data.snapshot import (
    list_snapshots,
    load_all_snapshots,
    load_snapshot,
    save_snapshot,
    snapshot_path,
)

KST = pytz.timezone("Asia/Seoul")

_DT_1100 = datetime(2026, 5, 6, 11, 0, 0, tzinfo=KST)
_DT_1300 = datetime(2026, 5, 6, 13, 0, 0, tzinfo=KST)
_DATE = date(2026, 5, 6)


def _sample_df(rank_offset: int = 0) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "rank": 1 + rank_offset,
                "code": "075180",
                "name": "제룡전기",
                "price": 91_300,
                "prev_close": 70_230,
                "daily_return": 30.0,
                "intraday_high": 91_300,
                "volume": 5_000_000,
                "trading_value": 400_000_000_000,
                "is_limit_up": True,
            }
        ]
    )


def test_snapshot_path(tmp_path):
    path = snapshot_path(tmp_path, _DT_1100)
    assert path == tmp_path / "intraday" / "snapshots" / "2026-05-06" / "11_00.parquet"


def test_save_and_load_snapshot(tmp_path):
    df = _sample_df()
    save_snapshot(df, tmp_path, _DT_1100)
    loaded = load_snapshot(tmp_path, _DATE, "11:00")

    assert not loaded.empty
    assert "snapshot_time" in loaded.columns
    assert loaded.iloc[0]["code"] == "075180"
    assert loaded.iloc[0]["price"] == 91_300
    assert loaded.iloc[0]["is_limit_up"] == True


def test_save_adds_snapshot_time(tmp_path):
    df = _sample_df()
    assert "snapshot_time" not in df.columns
    save_snapshot(df, tmp_path, _DT_1100)
    loaded = load_snapshot(tmp_path, _DATE, "11:00")
    assert loaded.iloc[0]["snapshot_time"] is not None


def test_load_snapshot_missing(tmp_path):
    """파일 없으면 빈 DataFrame."""
    loaded = load_snapshot(tmp_path, _DATE, "11:00")
    assert loaded.empty


def test_list_snapshots_empty(tmp_path):
    assert list_snapshots(tmp_path, _DATE) == []


def test_list_snapshots_order(tmp_path):
    save_snapshot(_sample_df(), tmp_path, _DT_1300)
    save_snapshot(_sample_df(), tmp_path, _DT_1100)
    times = list_snapshots(tmp_path, _DATE)
    assert times == ["11:00", "13:00"]


def test_load_all_snapshots(tmp_path):
    save_snapshot(_sample_df(0), tmp_path, _DT_1100)
    save_snapshot(_sample_df(1), tmp_path, _DT_1300)
    all_df = load_all_snapshots(tmp_path, _DATE)
    assert len(all_df) == 2


def test_load_all_snapshots_empty(tmp_path):
    all_df = load_all_snapshots(tmp_path, _DATE)
    assert all_df.empty


def test_save_snapshot_creates_dirs(tmp_path):
    """중간 디렉토리가 없어도 자동 생성."""
    new_dir = tmp_path / "brand_new"
    save_snapshot(_sample_df(), new_dir, _DT_1100)
    assert snapshot_path(new_dir, _DT_1100).exists()
