"""지수 일봉(KOSPI/KOSDAQ) 영구 저장.

종목 일봉(src/data/storage.py)과 별도 인덱스 단위로 분리 저장. 매일 incremental
update 누적해서 historical layer3_strong_mkt 매칭 범위를 확대하기 위함
(KIS API 1회 호출 제한 ~100건을 우회).

스키마:
    [date(python date), close(float)]

저장 경로:
    {DATA_DIR}/index/{kospi,kosdaq}.parquet
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from src.data.storage import _safe_read_parquet

INDEX_DAILY_COLUMNS = ["date", "close"]

_INDEX_FILENAMES = {
    "0001": "kospi.parquet",
    "1001": "kosdaq.parquet",
}


def index_daily_path(data_dir: Path, index_code: str) -> Path:
    """index_code: '0001' KOSPI, '1001' KOSDAQ."""
    fname = _INDEX_FILENAMES.get(index_code)
    if fname is None:
        raise ValueError(f"지원하지 않는 index_code: {index_code}")
    return Path(data_dir) / "index" / fname


def read_index_daily(data_dir: Path, index_code: str) -> pd.DataFrame:
    """지수 일봉 parquet 읽기. 없거나 손상 시 빈 DF (스키마 유지)."""
    return _safe_read_parquet(
        index_daily_path(data_dir, index_code),
        INDEX_DAILY_COLUMNS,
        f"index daily ({index_code})",
    )


def write_index_daily(df: pd.DataFrame, data_dir: Path, index_code: str) -> None:
    """전체 덮어쓰기. date 오름차순 정렬."""
    path = index_daily_path(data_dir, index_code)
    path.parent.mkdir(parents=True, exist_ok=True)
    if df is None or df.empty:
        df = pd.DataFrame(columns=INDEX_DAILY_COLUMNS)
    else:
        df = df.sort_values("date").reset_index(drop=True)
    df.to_parquet(path, index=False)


def upsert_index_daily(
    new_rows: pd.DataFrame,
    data_dir: Path,
    index_code: str,
) -> int:
    """기존 + 신규 합치고 date 중복은 신규로 덮어쓴다.

    Returns:
        병합 후 전체 행 수.
    """
    if new_rows is None or new_rows.empty:
        return len(read_index_daily(data_dir, index_code))

    existing = read_index_daily(data_dir, index_code)
    if existing.empty:
        combined = new_rows.copy()
    else:
        combined = pd.concat([existing, new_rows], ignore_index=True)
    combined = combined.drop_duplicates(subset=["date"], keep="last")
    write_index_daily(combined, data_dir, index_code)
    return len(combined)


def latest_loaded_index_date(data_dir: Path, index_code: str) -> date | None:
    """가장 최근 적재된 날짜 (incremental 시작점)."""
    df = read_index_daily(data_dir, index_code)
    if df.empty:
        return None
    val = df["date"].max()
    if hasattr(val, "date") and not isinstance(val, date):
        return val.date()
    return val
