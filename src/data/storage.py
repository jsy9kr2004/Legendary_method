"""Parquet 저장소 helper.

전종목 일봉은 long format 단일 parquet 파일로 보관:
    {DATA_DIR}/daily/ohlcv.parquet

종목 마스터는:
    {DATA_DIR}/meta/stocks.parquet

5년치 ~ 250MB 수준이라 단일 파일도 메모리에 올려서 처리 가능.
누적이 1GB를 넘는 시점부터 SQLite 마이그레이션 검토 (data-infra.md Phase 2).
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

DAILY_OHLCV_FILENAME = "ohlcv.parquet"
STOCK_MASTER_FILENAME = "stocks.parquet"

DAILY_OHLCV_COLUMNS = [
    "code",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "trading_value",
    "change_rate",
]

STOCK_MASTER_COLUMNS = ["code", "name", "market", "market_cap", "listed_at"]


def daily_ohlcv_path(data_dir: Path) -> Path:
    return data_dir / "daily" / DAILY_OHLCV_FILENAME


def stock_master_path(data_dir: Path) -> Path:
    return data_dir / "meta" / STOCK_MASTER_FILENAME


def read_daily_ohlcv(data_dir: Path) -> pd.DataFrame:
    """일봉 parquet 읽기. 없으면 빈 DF (스키마 유지)."""
    path = daily_ohlcv_path(data_dir)
    if not path.exists():
        return pd.DataFrame(columns=DAILY_OHLCV_COLUMNS)
    return pd.read_parquet(path)


def write_daily_ohlcv(df: pd.DataFrame, data_dir: Path) -> None:
    """전체 덮어쓰기. (code, date) 정렬 후 저장."""
    path = daily_ohlcv_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    if df.empty:
        df = pd.DataFrame(columns=DAILY_OHLCV_COLUMNS)
    else:
        df = df.sort_values(["code", "date"]).reset_index(drop=True)
    df.to_parquet(path, index=False)


def upsert_daily_ohlcv(new_rows: pd.DataFrame, data_dir: Path) -> int:
    """기존 데이터에 신규 row를 합치고 (code, date) 중복은 신규로 덮어쓴다.

    Returns:
        병합 후 전체 행 수.
    """
    if new_rows is None or new_rows.empty:
        return len(read_daily_ohlcv(data_dir))

    existing = read_daily_ohlcv(data_dir)
    combined = pd.concat([existing, new_rows], ignore_index=True)
    combined = combined.drop_duplicates(subset=["code", "date"], keep="last")
    write_daily_ohlcv(combined, data_dir)
    return len(combined)


def latest_loaded_date(data_dir: Path) -> date | None:
    """전체에서 가장 최근 적재된 날짜 (incremental 시작점 결정용)."""
    df = read_daily_ohlcv(data_dir)
    if df.empty:
        return None
    val = df["date"].max()
    if hasattr(val, "date"):
        return val.date()
    return val


def loaded_dates(data_dir: Path) -> set[date]:
    """이미 적재된 날짜 집합 (init 재실행 시 skip 용)."""
    df = read_daily_ohlcv(data_dir)
    if df.empty:
        return set()
    out: set[date] = set()
    for v in df["date"].unique():
        out.add(v.date() if hasattr(v, "date") else v)
    return out


def read_stock_master(data_dir: Path) -> pd.DataFrame:
    path = stock_master_path(data_dir)
    if not path.exists():
        return pd.DataFrame(columns=STOCK_MASTER_COLUMNS)
    return pd.read_parquet(path)


def write_stock_master(df: pd.DataFrame, data_dir: Path) -> None:
    path = stock_master_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
