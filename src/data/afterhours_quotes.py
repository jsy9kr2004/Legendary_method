"""시간외 단일가 시세 fetcher (16:00 사후 레포트용).

16:00~18:00 시간외 단일가 시간대에 KIS 현재가 endpoint를 호출하면 시간외
가격이 반영된 응답이 돌아온다. 사후 레포트가 candidates 종목들의 갭상
예고 신호로 사용.

엔드포인트:
    /uapi/domestic-stock/v1/quotations/inquire-price
    TR FHKST01010100 — 주식 현재가 시세

응답에서 본 모듈이 쓰는 필드 (KIS Developer Portal 기준):
    hts_kor_isnm     : 종목명
    stck_prpr        : 현재가
    prdy_vrss        : 전일 대비
    prdy_ctrt        : 전일 대비율 (%)

응답이 비어 있거나 KIS가 rt_cd != '0' 으로 응답하면 해당 종목은 결과에서
제외하고 경고 로그만 남긴다 (사후 레포트 발송은 막지 않는다).
"""
from __future__ import annotations

from typing import Any

from loguru import logger

from src.kis.client import KISApiError, KISClient

_PRICE_ENDPOINT = "/uapi/domestic-stock/v1/quotations/inquire-price"
_PRICE_TR_ID = "FHKST01010100"


def _to_int(val: Any) -> int:
    try:
        return int(str(val).strip())
    except (TypeError, ValueError):
        return 0


def _to_float(val: Any) -> float:
    try:
        return float(str(val).strip())
    except (TypeError, ValueError):
        return float("nan")


def fetch_afterhours_quote(client: KISClient, code: str) -> dict[str, Any] | None:
    """단일 종목 시간외 단일가 시세 조회.

    Returns:
        {"code", "name", "price", "prev_close", "change_pct"} 또는 None (실패/빈 응답).
    """
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",  # 주식
        "FID_INPUT_ISCD": code,
    }
    try:
        payload = client.get(_PRICE_ENDPOINT, _PRICE_TR_ID, params)
    except (KISApiError, Exception) as e:  # noqa: BLE001
        logger.warning(f"[시간외] {code} 조회 실패: {e}")
        return None

    out = payload.get("output") or {}
    if not out:
        return None

    price = _to_int(out.get("stck_prpr"))
    if price <= 0:
        return None

    change = _to_int(out.get("prdy_vrss"))
    change_pct = _to_float(out.get("prdy_ctrt"))
    prev_close = price - change

    return {
        "code": code,
        "name": str(out.get("hts_kor_isnm", "")).strip(),
        "price": price,
        "prev_close": prev_close,
        "change_pct": change_pct,
    }


def fetch_afterhours_quotes(
    client: KISClient,
    codes: list[str],
) -> list[dict[str, Any]]:
    """여러 종목의 시간외 단일가 시세 조회. 실패한 종목은 결과에서 제외."""
    results: list[dict[str, Any]] = []
    for code in codes:
        q = fetch_afterhours_quote(client, str(code).zfill(6))
        if q is not None:
            results.append(q)
    return results
