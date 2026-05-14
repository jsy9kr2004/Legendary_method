"""장중 스냅샷 저장/로드.

저장 경로:
    {DATA_DIR}/intraday/snapshots/YYYY-MM-DD/HH_MM.parquet

스냅샷 1개 = 특정 시점에 찍은 거래대금 상위 종목 DataFrame.
스키마는 intraday.SNAPSHOT_COLUMNS + snapshot_time 컬럼 추가.

4시점: 11:00, 13:00, 14:00, 14:50 (KST)
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytz

from src.data.intraday import SNAPSHOT_COLUMNS
from src.data.storage import _safe_read_parquet

KST = pytz.timezone("Asia/Seoul")

SNAPSHOT_WITH_TIME_COLUMNS = ["snapshot_time"] + SNAPSHOT_COLUMNS


def _snapshot_dir(data_dir: Path, d: date) -> Path:
    return data_dir / "intraday" / "snapshots" / d.strftime("%Y-%m-%d")


def _snapshot_filename(dt: datetime) -> str:
    """datetime → 'HH_MM.parquet'"""
    return dt.strftime("%H_%M") + ".parquet"


def snapshot_path(data_dir: Path, dt: datetime) -> Path:
    """스냅샷 parquet 경로."""
    d = dt.date() if hasattr(dt, "date") else dt
    return _snapshot_dir(data_dir, d) / _snapshot_filename(dt)


def save_snapshot(df: pd.DataFrame, data_dir: Path, dt: datetime) -> Path:
    """스냅샷 DataFrame을 parquet으로 저장.

    df에 snapshot_time 컬럼이 없으면 dt로 채워서 추가한다.

    Returns:
        저장된 파일 경로.
    """
    out = df.copy()
    if "snapshot_time" not in out.columns:
        out.insert(0, "snapshot_time", dt)
    path = snapshot_path(data_dir, dt)
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(path, index=False)
    return path


def load_snapshot(data_dir: Path, d: date, hhmm: str) -> pd.DataFrame:
    """저장된 스냅샷 로드.

    Args:
        d: 날짜 (date 객체)
        hhmm: 시각 문자열 '11:00', '13:00', '14:00', '14:50' 등.
              내부적으로 ':' → '_' 변환.

    Returns:
        DataFrame. 파일 없으면 빈 DataFrame.
    """
    filename = hhmm.replace(":", "_") + ".parquet"
    path = _snapshot_dir(data_dir, d) / filename
    return _safe_read_parquet(
        path, SNAPSHOT_WITH_TIME_COLUMNS, f"snapshot {d} {hhmm}"
    )


def list_snapshots(data_dir: Path, d: date) -> list[str]:
    """날짜에 저장된 스냅샷 시각 목록 (HH:MM 형식, 오름차순).

    예: ['11:00', '13:00', '14:00']
    """
    snap_dir = _snapshot_dir(data_dir, d)
    if not snap_dir.exists():
        return []
    times = []
    for p in sorted(snap_dir.glob("*.parquet")):
        stem = p.stem  # e.g. '11_00'
        times.append(stem.replace("_", ":"))
    return times


def load_all_snapshots(data_dir: Path, d: date) -> pd.DataFrame:
    """날짜의 모든 스냅샷을 이어 붙인 DataFrame.

    Returns:
        snapshot_time 오름차순 정렬. 없으면 빈 DataFrame.
    """
    times = list_snapshots(data_dir, d)
    if not times:
        return pd.DataFrame(columns=SNAPSHOT_WITH_TIME_COLUMNS)
    parts = [load_snapshot(data_dir, d, t) for t in times]
    return pd.concat(parts, ignore_index=True)
