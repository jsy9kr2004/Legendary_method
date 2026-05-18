"""KIS 시세 fetch 병렬화 — tick 1회용 buffer.

worker.dashboard_tick 의 funnel 후보 ∪ monitored ∪ 보유 종목 합집합에 대해
종목별 시세 (분봉/체결강도/호가/투자자) 를 ThreadPoolExecutor 로 동시 수집.

배경
----
- 직렬 fetch 시 종목당 ~600ms (4 API × ~150ms). 종목 20개면 12초 → tick interval(2초)
  대비 6배 초과 → APScheduler coalesce 로 실효 갱신 ~10초 (2026-05-18 실측 12.9s).
- KIS rate limit 은 키 기준이고 듀얼 키 운영 중 (.env KIS_APP_KEY/KIS_APP_KEY_2,
  `src/kis/client.py:60` 라운드 로빈, `src/kis/rate_limit.py:68` 키별 limiter).
  합산 ~40 req/s. limiter 가 thread-safe lock 으로 동시 호출을 자연 throttle.
- httpx.Client 는 thread-safe (`KISClient._http` 연결 풀).

설계
----
- fetch_stock_bundle: 한 종목의 4 API 직렬 fetch (기존 dashboard_tick 의 종목별
  fetch 와 동일). 종목 단위 예외 격리 — 한 API 실패가 다른 API 막지 않음.
- fetch_bundles_parallel: 종목 리스트를 worker 풀에서 동시 처리. 종목 단위 예외
  격리 — 한 종목 실패가 다른 종목 막지 않음.
- tick 안 1회용 buffer 채울 용도. **tick 간 cache X** — 매 tick 새로 fetch
  (CLAUDE.md "고주파 모니터링 fresh 우선" 정책).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Iterable

import pandas as pd
from loguru import logger

from src.data.intraday_realtime import (
    fetch_asking_price,
    fetch_ccnl_strength,
    fetch_investor_flow,
    fetch_minute_bars,
)
from src.kis.client import KISClient

DEFAULT_MAX_WORKERS = 12


@dataclass
class StockBundle:
    """한 종목의 한 tick 시세 묶음.

    각 필드는 fetch 실패 시 fetcher 의 기본 반환값 그대로 (빈 DataFrame / None).
    호출자는 NaN-safe 처리 필요.
    """

    code: str
    bars: pd.DataFrame
    ccnl: dict[str, Any] | None
    asking: dict[str, Any] | None
    investor: dict[str, Any] | None


def fetch_stock_bundle(client: KISClient, code: str) -> StockBundle:
    """한 종목의 4 API 직렬 fetch. API 별 예외 격리."""
    bars: pd.DataFrame = pd.DataFrame()
    ccnl: dict[str, Any] | None = None
    asking: dict[str, Any] | None = None
    investor: dict[str, Any] | None = None
    try:
        bars = fetch_minute_bars(client, code)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[parallel_fetch] {code} bars 실패: {e}")
    try:
        ccnl = fetch_ccnl_strength(client, code)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[parallel_fetch] {code} ccnl 실패: {e}")
    try:
        asking = fetch_asking_price(client, code)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[parallel_fetch] {code} asking 실패: {e}")
    try:
        investor = fetch_investor_flow(client, code)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[parallel_fetch] {code} investor 실패: {e}")
    return StockBundle(
        code=code, bars=bars, ccnl=ccnl, asking=asking, investor=investor,
    )


def fetch_bundles_parallel(
    client: KISClient,
    codes: Iterable[str],
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> dict[str, StockBundle]:
    """종목 리스트의 fetch 를 worker 풀에서 동시 처리.

    Args:
        client: KISClient (thread-safe httpx + 키별 limiter).
        codes: 6자리 종목 코드. 중복은 내부 제거 (순서 유지).
        max_workers: 동시 thread 수. KIS limiter 가 자연 throttle 하므로 너무 크게
            잡아도 lock 대기로 수렴. 12 면 듀얼 키 ~40 req/s 합산에 합리적.

    Returns:
        code → StockBundle dict. 모든 종목이 들어감 (fetch 실패는 bundle 내부 필드).
    """
    unique_codes = list(dict.fromkeys(str(c) for c in codes))
    if not unique_codes:
        return {}
    workers = max(1, min(max_workers, len(unique_codes)))
    bundles: dict[str, StockBundle] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(fetch_stock_bundle, client, c): c for c in unique_codes
        }
        for fut in as_completed(futures):
            code = futures[fut]
            try:
                bundles[code] = fut.result()
            except Exception as e:  # noqa: BLE001
                # fetch_stock_bundle 안에서 이미 격리되어 정상 경로엔 도달 안 함.
                logger.error(f"[parallel_fetch] {code} bundle 예외: {e}")
                bundles[code] = StockBundle(
                    code=code, bars=pd.DataFrame(),
                    ccnl=None, asking=None, investor=None,
                )
    return bundles
