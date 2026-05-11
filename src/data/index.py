"""KIS API 기반 국내 지수(KOSPI/KOSDAQ) 시세 fetcher.

지수 현재가:
    TR_ID: FHPUP02100000
    Endpoint: /uapi/domestic-stock/v1/quotations/inquire-index-price
    FID_COND_MRKT_DIV_CODE: U
    FID_INPUT_ISCD: 0001 (코스피) / 1001 (코스닥)

지수 일자별 시세 (200일 이평 / 60일 수익률 계산):
    TR_ID: FHPUP02120000
    Endpoint: /uapi/domestic-stock/v1/quotations/inquire-index-daily-price
    output2: 일별 30건 (date, close)

⚠ 응답 필드명은 KIS Developer Portal 기준 추정. 운영 mock 검증 필요.
"""
from __future__ import annotations

from typing import Any

import pandas as pd
from loguru import logger

from src.kis.client import KISApiError, KISClient

_INDEX_PRICE_ENDPOINT = "/uapi/domestic-stock/v1/quotations/inquire-index-price"
_INDEX_PRICE_TR_ID = "FHPUP02100000"

_INDEX_DAILY_ENDPOINT = "/uapi/domestic-stock/v1/quotations/inquire-index-daily-price"
_INDEX_DAILY_TR_ID = "FHPUP02120000"

KOSPI_CODE = "0001"
KOSDAQ_CODE = "1001"


def _to_float(val: Any, default: float = float("nan")) -> float:
    try:
        return float(str(val).strip())
    except (TypeError, ValueError):
        return default


def fetch_index_quote(client: KISClient, index_code: str = KOSPI_CODE) -> dict[str, Any] | None:
    """지수 현재가 조회.

    Args:
        client: KIS client.
        index_code: "0001"(KOSPI) | "1001"(KOSDAQ).

    Returns:
        {
            "code": "0001",
            "current": float,    # 현재 지수
            "prev_close": float, # 전일 종가
            "change": float,     # 전일 대비
            "change_rate": float,# 등락률 (%)
        }
        실패 시 None.

    참고 응답 필드 (output):
        bstp_nmix_prpr      현재 지수
        prdy_nmix           전일 지수
        bstp_nmix_prdy_vrss 전일 대비
        prdy_ctrt           등락률
    """
    params = {
        "FID_COND_MRKT_DIV_CODE": "U",
        "FID_INPUT_ISCD": index_code,
    }
    try:
        payload = client.get(_INDEX_PRICE_ENDPOINT, _INDEX_PRICE_TR_ID, params=params)
    except KISApiError as e:
        logger.error(f"지수({index_code}) 현재가 조회 실패: {e}")
        return None

    out = payload.get("output") or {}
    if isinstance(out, list):
        out = out[0] if out else {}
    if not out:
        return None

    return {
        "code": index_code,
        "current": _to_float(out.get("bstp_nmix_prpr")),
        "prev_close": _to_float(out.get("prdy_nmix")),
        "change": _to_float(out.get("bstp_nmix_prdy_vrss")),
        "change_rate": _to_float(out.get("prdy_ctrt")),
    }


def fetch_index_daily(
    client: KISClient,
    index_code: str = KOSPI_CODE,
    days: int = 252,
) -> pd.DataFrame:
    """지수 일자별 시세 (close 만 사용).

    Args:
        client: KIS client.
        index_code: "0001"(KOSPI) | "1001"(KOSDAQ).
        days: 최근 며칠 (200일 이평 계산엔 252 정도 권장).

    Returns:
        columns=[date, close], 오름차순 (가장 최근이 마지막).
        실패 또는 빈 응답 시 빈 DataFrame.

    참고 응답 필드 (output2):
        stck_bsop_date  영업일자 YYYYMMDD
        bstp_nmix_prpr  종가 지수
    """
    params = {
        "FID_COND_MRKT_DIV_CODE": "U",
        "FID_INPUT_ISCD": index_code,
        "FID_INPUT_DATE_1": "",
        "FID_INPUT_DATE_2": "",
        "FID_PERIOD_DIV_CODE": "D",  # D=일봉
    }
    try:
        payload = client.get(_INDEX_DAILY_ENDPOINT, _INDEX_DAILY_TR_ID, params=params)
    except KISApiError as e:
        logger.error(f"지수({index_code}) 일별 조회 실패: {e}")
        return pd.DataFrame(columns=["date", "close"])

    rows = payload.get("output2") or payload.get("output") or []
    if isinstance(rows, dict):
        rows = [rows]
    if not rows:
        return pd.DataFrame(columns=["date", "close"])

    records = []
    for r in rows[:days]:
        date = str(r.get("stck_bsop_date", "")).strip()
        close = _to_float(r.get("bstp_nmix_prpr"))
        if not date or close != close:  # NaN
            continue
        records.append({"date": date, "close": close})

    if not records:
        return pd.DataFrame(columns=["date", "close"])

    df = pd.DataFrame(records).sort_values("date").reset_index(drop=True)
    return df


def compute_market_stats(
    client: KISClient,
    daily_lookback_days: int = 252,
) -> dict[str, Any]:
    """모닝/사후 레포트용 시장 국면 통계 일괄 산출.

    Returns:
        {
            "kospi_current": float,
            "kospi_change_rate": float,
            "kospi_ma200": float,
            "kospi_above_ma200": bool,
            "kospi_60d_return": float,    # %
            "kosdaq_current": float,
            "kosdaq_change_rate": float,
        }
        조회 실패한 항목은 NaN/None.

    fail-safe:
        하나라도 실패해도 가능한 항목은 반환. 빈 dict 만 반환 시 호출부에서
        "(시장 국면 데이터 없음)" 처리.
    """
    stats: dict[str, Any] = {}

    # KOSPI 현재
    kospi_quote = fetch_index_quote(client, KOSPI_CODE)
    if kospi_quote:
        stats["kospi_current"] = kospi_quote["current"]
        stats["kospi_change_rate"] = kospi_quote["change_rate"]

    # KOSDAQ 현재
    kosdaq_quote = fetch_index_quote(client, KOSDAQ_CODE)
    if kosdaq_quote:
        stats["kosdaq_current"] = kosdaq_quote["current"]
        stats["kosdaq_change_rate"] = kosdaq_quote["change_rate"]

    # KOSPI 일자별 → 200일 이평 + 60일 수익률
    kospi_daily = fetch_index_daily(client, KOSPI_CODE, days=daily_lookback_days)
    if not kospi_daily.empty and len(kospi_daily) >= 60:
        last_close = float(kospi_daily.iloc[-1]["close"])
        if len(kospi_daily) >= 200:
            ma200 = float(kospi_daily.tail(200)["close"].mean())
            stats["kospi_ma200"] = ma200
            stats["kospi_above_ma200"] = last_close > ma200
        # 60일 수익률 = (오늘 / 60거래일 전 - 1) × 100
        ref_close = float(kospi_daily.iloc[-60]["close"])
        if ref_close > 0:
            stats["kospi_60d_return"] = (last_close / ref_close - 1.0) * 100.0

    return stats
