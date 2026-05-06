"""KIS Open API 기반 장중 데이터 fetcher.

거래대금 순위:
    TR_ID: FHPST01710000
    Endpoint: /uapi/domestic-stock/v1/quotations/volume-rank
    - 한 번 호출로 상위 30위(또는 top_n) 전종목 반환

종목 현재가:
    TR_ID: FHKST01010100
    Endpoint: /uapi/domestic-stock/v1/quotations/inquire-price
    - 종목별 1회 호출

정량 정의:
    - daily_return(%): (현재가 - 전일종가) / 전일종가 * 100
    - limit_up_price: 전일종가 * 1.30 (KOSPI/KOSDAQ 공통 +30%)
    - intraday_high: 당일 장중 고가 (stck_hgpr)
"""
from __future__ import annotations

from typing import Any

import pandas as pd
from loguru import logger

from src.kis.client import KISApiError, KISClient

_VOLUME_RANK_ENDPOINT = "/uapi/domestic-stock/v1/quotations/volume-rank"
_VOLUME_RANK_TR_ID = "FHPST01710000"

_INQUIRE_PRICE_ENDPOINT = "/uapi/domestic-stock/v1/quotations/inquire-price"
_INQUIRE_PRICE_TR_ID = "FHKST01010100"

SNAPSHOT_COLUMNS = [
    "rank",
    "code",
    "name",
    "price",
    "prev_close",
    "daily_return",
    "intraday_high",
    "intraday_low",
    "volume",
    "trading_value",
    "is_limit_up",
]


def _to_int(val: Any, default: int = 0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _to_float(val: Any, default: float = float("nan")) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def fetch_volume_rank(
    client: KISClient,
    top_n: int = 30,
) -> pd.DataFrame:
    """거래대금 상위 top_n 종목 스냅샷.

    정량 정의:
        - FID_COND_MRKT_DIV_CODE=J: KOSPI+KOSDAQ 통합
        - FID_COND_SCR_DIV_CODE=20171: 거래대금 순위 스크리너

    Returns:
        SNAPSHOT_COLUMNS 스키마의 DataFrame (rank 오름차순).
        API 실패 시 빈 DataFrame 반환 (fail-loud: 호출부에서 로그 확인).
    """
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_COND_SCR_DIV_CODE": "20171",
        "FID_INPUT_ISCD": "0000",
        "FID_DIV_CLS_CODE": "0",
        "FID_BLNG_CLS_CODE": "0",
        "FID_TRGT_CLS_CODE": "111111111",
        "FID_TRGT_EXLS_CLS_CODE": "000000",
        "FID_INPUT_PRICE_1": "",
        "FID_INPUT_PRICE_2": "",
        "FID_VOL_CNT": "",
        "FID_INPUT_DATE_1": "",
    }
    try:
        payload = client.get(_VOLUME_RANK_ENDPOINT, _VOLUME_RANK_TR_ID, params=params)
    except KISApiError as e:
        logger.error(f"거래대금 순위 조회 실패: {e}")
        return pd.DataFrame(columns=SNAPSHOT_COLUMNS)

    rows: list[dict] = payload.get("output") or []
    records = []
    for row in rows:
        rank = _to_int(row.get("data_rank"), 0)
        if rank < 1 or rank > top_n:
            continue
        price = _to_int(row.get("stck_prpr"))
        prev_close = _to_int(row.get("stck_prdy_clpr"))
        daily_return = _to_float(row.get("prdy_ctrt"))
        intraday_high = _to_int(row.get("stck_hgpr"))
        intraday_low = _to_int(row.get("stck_lwpr"))
        volume = _to_int(row.get("acml_vol"))
        trading_value = _to_int(row.get("acml_tr_pbmn"))
        lup = _is_limit_up_price(price, prev_close) if prev_close > 0 else False
        records.append(
            {
                "rank": rank,
                "code": str(row.get("mksc_shrn_iscd", "")).zfill(6),
                "name": str(row.get("hts_kor_isnm", "")),
                "price": price,
                "prev_close": prev_close,
                "daily_return": daily_return,
                "intraday_high": intraday_high,
                "intraday_low": intraday_low,
                "volume": volume,
                "trading_value": trading_value,
                "is_limit_up": lup,
            }
        )
    if not records:
        return pd.DataFrame(columns=SNAPSHOT_COLUMNS)
    df = pd.DataFrame(records, columns=SNAPSHOT_COLUMNS).sort_values("rank").reset_index(drop=True)
    logger.debug(f"거래대금 순위 조회 완료: {len(df)}종목")
    return df


def fetch_quote(client: KISClient, code: str) -> dict[str, Any] | None:
    """단일 종목 현재가 조회.

    Returns:
        {code, name, price, prev_close, daily_return, intraday_high,
         volume, trading_value, is_limit_up}
        조회 실패 시 None.
    """
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": code,
    }
    try:
        payload = client.get(_INQUIRE_PRICE_ENDPOINT, _INQUIRE_PRICE_TR_ID, params=params)
    except KISApiError as e:
        logger.error(f"{code} 현재가 조회 실패: {e}")
        return None

    out: dict = payload.get("output") or {}
    if not out:
        return None

    price = _to_int(out.get("stck_prpr"))
    prev_close = _to_int(out.get("stck_prdy_clpr"))
    daily_return = _to_float(out.get("prdy_ctrt"))
    intraday_high = _to_int(out.get("stck_hgpr"))
    intraday_low = _to_int(out.get("stck_lwpr"))
    volume = _to_int(out.get("acml_vol"))
    trading_value = _to_int(out.get("acml_tr_pbmn"))
    lup = _is_limit_up_price(price, prev_close) if prev_close > 0 else False

    return {
        "code": code,
        "name": str(out.get("hts_kor_isnm", "")),
        "price": price,
        "prev_close": prev_close,
        "daily_return": daily_return,
        "intraday_high": intraday_high,
        "intraday_low": intraday_low,
        "volume": volume,
        "trading_value": trading_value,
        "is_limit_up": lup,
    }


def fetch_quotes_bulk(
    client: KISClient,
    codes: list[str],
) -> pd.DataFrame:
    """복수 종목 현재가 일괄 조회. rate limit은 client가 처리.

    Returns:
        SNAPSHOT_COLUMNS 중 rank 제외한 컬럼 DataFrame.
        실패 종목은 제외된다.
    """
    records = []
    for code in codes:
        q = fetch_quote(client, code)
        if q is not None:
            records.append(q)
    if not records:
        cols = [c for c in SNAPSHOT_COLUMNS if c != "rank"]
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(records)


# 상한가 계산은 src.jongbae.limit_up 의 단일 정의를 사용 (M1: SSoT 통합).
from src.jongbae.limit_up import is_limit_up as _is_limit_up_price  # noqa: E402
from src.jongbae.limit_up import limit_up_price  # noqa: E402, F401
