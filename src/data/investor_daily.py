"""종목별 + 시장 전체 외인/기관/프로그램 일별 누적 저장 + N일 평균 계산.

2026-05-22 도입 — 결정 레포트 [수급] 라인에서 "오늘 vs N일 평균" 비교 제공.
KIS 일별 endpoint (FHPTJ04160001 / FHPPG04650201 / FHPTJ04040000) 가 시간 제한
(00:00~15:40) 으로 새벽 차단 + 응답 구조 미검증 → 자체 누적으로 우선 작동.

데이터 흐름:
    매일 14:50 결정 레포트 빌드 시
      → append_today_stock(code, inv_dict, today) — 종목별 1행 append
      → append_today_market(market_inv_dict, today) — 시장 전체 1행 append (KOSPI/KOSDAQ)
    결정 레포트 [수급] 라인 빌드 시
      → get_nday_avg_stock(code, n=20) — 최근 N일 (N≤20) 종목 평균
      → get_nday_avg_market(market, n=20) — 시장 평균

스키마:
    종목별 (`data/investor_daily/{code}.parquet`):
        date | foreign_net_buy | institution_net_buy | program_net_buy | program_net_buy_value
    시장 전체 (`data/investor_market_daily.parquet`):
        date | market (KOSPI/KOSDAQ) | foreign_net_buy | institution_net_buy | program_net_buy

수량 단위: 주 (KIS investor-trend-estimate 의 fake_ntby_qty 그대로).
거래대금 단위: 원 (program-trade-by-stock 의 whol_smtn_ntby_tr_pbmn).

날짜 unique 보장 — 같은 날 두 번 호출 시 덮어쓰기 (재실행 안전).
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from loguru import logger

MAX_LOOKBACK_DAYS = 20  # 표시할 평균의 최대 N
_STOCK_COLS = [
    "date", "foreign_net_buy", "institution_net_buy",
    "program_net_buy", "program_net_buy_value",
]
_MARKET_COLS = [
    "date", "market",
    "foreign_net_buy", "institution_net_buy", "program_net_buy",
]


def _data_dir() -> Path:
    return Path(os.getenv("DATA_DIR", "data"))


def _stock_path(code: str) -> Path:
    base = _data_dir() / "investor_daily"
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{code}.parquet"


def _market_path() -> Path:
    base = _data_dir()
    base.mkdir(parents=True, exist_ok=True)
    return base / "investor_market_daily.parquet"


def _read_or_empty(path: Path, cols: list[str]) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame({c: pd.Series(dtype="object") for c in cols})
    try:
        return pd.read_parquet(path)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[investor_daily] {path} read 실패 — 빈 DF 반환: {e}")
        return pd.DataFrame({c: pd.Series(dtype="object") for c in cols})


def append_today_stock(code: str, inv: dict[str, Any] | None, today: date) -> None:
    """오늘의 종목별 외인/기관/프로그램 1행 누적 저장.

    inv = fetch_investor_flow(client, code) 반환 dict.
    None 또는 모든 값 0 이면 append skip (장 마감 후 KIS 시간 제한 등 케이스).
    같은 날 두 번 호출 시 덮어쓰기 (재실행 안전).
    """
    if inv is None:
        return
    foreign = int(inv.get("foreign_net_buy") or 0)
    inst = int(inv.get("institution_net_buy") or 0)
    program = int(inv.get("program_net_buy") or 0)
    program_value = int(inv.get("program_net_buy_value") or 0)
    if foreign == 0 and inst == 0 and program == 0:
        logger.debug(f"[investor_daily] {code} {today} 모두 0 — append skip")
        return

    path = _stock_path(code)
    df = _read_or_empty(path, _STOCK_COLS)
    # 같은 날 row 가 이미 있으면 제거 후 추가 (덮어쓰기)
    if not df.empty and "date" in df.columns:
        df = df[df["date"] != today]
    new = pd.DataFrame([{
        "date": today,
        "foreign_net_buy": foreign,
        "institution_net_buy": inst,
        "program_net_buy": program,
        "program_net_buy_value": program_value,
    }])
    out = pd.concat([df, new], ignore_index=True).sort_values("date")
    out.to_parquet(path, index=False)


def append_today_market(market: str, inv: dict[str, Any] | None, today: date) -> None:
    """오늘의 시장 전체 외인/기관/프로그램 1행 누적 (KOSPI/KOSDAQ 별로 호출).

    market: "KOSPI" | "KOSDAQ".
    inv: 시장 전체 dict — 키 foreign_net_buy / institution_net_buy / program_net_buy.
         None 이면 skip (KIS endpoint 미작동 시).
    """
    if inv is None or market not in ("KOSPI", "KOSDAQ"):
        return
    foreign = int(inv.get("foreign_net_buy") or 0)
    inst = int(inv.get("institution_net_buy") or 0)
    program = int(inv.get("program_net_buy") or 0)
    if foreign == 0 and inst == 0 and program == 0:
        return

    path = _market_path()
    df = _read_or_empty(path, _MARKET_COLS)
    if not df.empty and "date" in df.columns and "market" in df.columns:
        df = df[~((df["date"] == today) & (df["market"] == market))]
    new = pd.DataFrame([{
        "date": today, "market": market,
        "foreign_net_buy": foreign, "institution_net_buy": inst, "program_net_buy": program,
    }])
    out = pd.concat([df, new], ignore_index=True).sort_values(["date", "market"])
    out.to_parquet(path, index=False)


def get_nday_avg_stock(code: str, today: date, n: int = MAX_LOOKBACK_DAYS) -> dict[str, Any] | None:
    """최근 N일 (N≤20) 종목 외인/기관/프로그램 평균.

    Returns:
        {
            "n_days": int,                   # 실제 사용한 일수 (≤ n)
            "foreign_net_buy_avg": float,
            "institution_net_buy_avg": float,
            "program_net_buy_avg": float,
            "program_net_buy_value_avg": float,
        }
        누적 데이터 없으면 None.
    """
    path = _stock_path(code)
    if not path.exists():
        return None
    df = _read_or_empty(path, _STOCK_COLS)
    if df.empty:
        return None
    # 오늘 row 는 평균 계산에서 제외 (오늘 vs 과거 평균 비교 의도).
    past = df[df["date"] != today].sort_values("date").tail(n)
    n_days = len(past)
    if n_days == 0:
        return None
    return {
        "n_days": n_days,
        "foreign_net_buy_avg": float(past["foreign_net_buy"].mean()),
        "institution_net_buy_avg": float(past["institution_net_buy"].mean()),
        "program_net_buy_avg": float(past["program_net_buy"].mean()),
        "program_net_buy_value_avg": float(past["program_net_buy_value"].mean()),
    }


def get_nday_avg_market(market: str, today: date, n: int = MAX_LOOKBACK_DAYS) -> dict[str, Any] | None:
    """최근 N일 (N≤20) 시장 전체 평균. market="KOSPI"|"KOSDAQ"."""
    path = _market_path()
    if not path.exists() or market not in ("KOSPI", "KOSDAQ"):
        return None
    df = _read_or_empty(path, _MARKET_COLS)
    if df.empty:
        return None
    past = df[(df["market"] == market) & (df["date"] != today)].sort_values("date").tail(n)
    n_days = len(past)
    if n_days == 0:
        return None
    return {
        "n_days": n_days,
        "foreign_net_buy_avg": float(past["foreign_net_buy"].mean()),
        "institution_net_buy_avg": float(past["institution_net_buy"].mean()),
        "program_net_buy_avg": float(past["program_net_buy"].mean()),
    }
