"""상한가 진입 감지 모듈.

정량 정의:
    - 상한가 가격 = floor(전일종가 × 1.30)  [KRX ±30% 가격제한폭]
    - 상한가 진입 = 현재가 >= 상한가 가격
    - 일봉 +20% 이상 = daily_return >= 20.0 (종배 후보 기준)

폴링 방식:
    detect_new_limit_up() 를 주기적으로 호출.
    이전 상한가 집합과 비교해 신규 진입 종목만 반환.
    호출부(scheduler)가 알림을 발송한다.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd
from loguru import logger

from src.data.intraday import fetch_quotes_bulk
from src.kis.client import KISClient


def limit_up_price(prev_close: int) -> int:
    """전일종가 → 상한가 가격.

    정량 정의:
        상한가 = floor(전일종가 × 1.30)
        KOSPI/KOSDAQ 공통 ±30% 가격제한폭 (2015-06-15 이후).
    """
    if prev_close <= 0:
        return 0
    return math.floor(prev_close * 1.30)


def is_limit_up(price: int, prev_close: int) -> bool:
    """현재가가 상한가에 도달했는지 판단."""
    if prev_close <= 0:
        return False
    return price >= limit_up_price(prev_close)


def is_strong_candidate(daily_return: float) -> bool:
    """일봉 수익률 +20% 이상인지 (종배 후보 기본 조건).

    정량 정의:
        daily_return(%): (현재가 - 전일종가) / 전일종가 * 100
        기준: daily_return >= 20.0
    """
    return daily_return >= 20.0


def detect_new_limit_up(
    client: KISClient,
    watch_codes: list[str],
    already_limit_up: set[str],
) -> tuple[list[dict[str, Any]], set[str]]:
    """감시 종목 중 신규로 상한가에 진입한 종목을 탐지.

    Args:
        client: KIS API 클라이언트
        watch_codes: 감시할 종목 코드 목록
        already_limit_up: 이미 상한가로 기록된 종목 코드 집합 (중복 알림 방지)

    Returns:
        (new_limit_up_records, updated_already_set)
        new_limit_up_records: 신규 상한가 진입 종목 dict 리스트
        updated_already_set: 갱신된 already_limit_up 집합

    정량 정의:
        신규 상한가 = 현재가 >= floor(전일종가 × 1.30) AND 해당 종목이 already_limit_up에 없음
    """
    if not watch_codes:
        return [], already_limit_up.copy()

    df = fetch_quotes_bulk(client, watch_codes)
    if df.empty:
        return [], already_limit_up.copy()

    new_entries: list[dict[str, Any]] = []
    updated = already_limit_up.copy()

    for _, row in df.iterrows():
        code = str(row["code"])
        if code in updated:
            continue
        if row.get("is_limit_up", False):
            entry = row.to_dict()
            new_entries.append(entry)
            updated.add(code)
            logger.info(
                f"[상한가 진입] {row.get('name', code)}({code}) "
                f"현재가={row.get('price')} "
                f"수익률={row.get('daily_return', 0):.1f}%"
            )

    return new_entries, updated


def filter_limit_up_from_snapshot(snapshot_df: pd.DataFrame) -> pd.DataFrame:
    """스냅샷 DataFrame에서 상한가 종목만 필터링.

    거래대금 순위 스냅샷(fetch_volume_rank 결과)에서 is_limit_up=True인 행만 반환.
    """
    if snapshot_df.empty or "is_limit_up" not in snapshot_df.columns:
        return pd.DataFrame()
    return snapshot_df[snapshot_df["is_limit_up"]].reset_index(drop=True)


def filter_strong_candidates(snapshot_df: pd.DataFrame) -> pd.DataFrame:
    """스냅샷에서 일봉 +20% 이상 종목만 필터링 (종배 후보 1차 필터).

    정량 정의: daily_return >= 20.0%
    """
    if snapshot_df.empty or "daily_return" not in snapshot_df.columns:
        return pd.DataFrame()
    mask = snapshot_df["daily_return"].apply(
        lambda x: is_strong_candidate(float(x)) if pd.notna(x) else False
    )
    return snapshot_df[mask].reset_index(drop=True)
