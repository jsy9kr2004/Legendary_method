"""KIS Open API 기반 일봉 OHLCV fetcher.

TR_ID: FHKST03010100 (국내주식 기간별시세)
Endpoint: /uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice

한 번 호출 최대 100건(영업일). 긴 기간은 90일(달력일) 청크로 분할 호출.

표준 출력 스키마:
    code, date, open, high, low, close, volume, trading_value, change_rate

`change_rate` 는 적재 시점에는 NaN. 분석 시 storage 에서
`groupby('code')['close'].pct_change()` 로 계산.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
from loguru import logger

from src.kis.client import KISApiError, KISClient

_ENDPOINT = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
_TR_ID = "FHKST03010100"
_CHUNK_DAYS = 90  # 100 건 제한 안전 버퍼

_OUTPUT_COLS = [
    "code", "date", "open", "high", "low", "close",
    "volume", "trading_value", "change_rate",
]


def _yyyymmdd(d: date) -> str:
    return d.strftime("%Y%m%d")


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=_OUTPUT_COLS)


def _parse_output2(rows: list[dict], code: str) -> pd.DataFrame:
    if not rows:
        return _empty()
    df = pd.DataFrame(rows)
    out = pd.DataFrame(
        {
            "code": code,
            "date": pd.to_datetime(df["stck_bsop_date"], format="%Y%m%d").dt.date,
            "open": pd.to_numeric(df["stck_oprc"], errors="coerce").astype("Int64"),
            "high": pd.to_numeric(df["stck_hgpr"], errors="coerce").astype("Int64"),
            "low": pd.to_numeric(df["stck_lwpr"], errors="coerce").astype("Int64"),
            "close": pd.to_numeric(df["stck_clpr"], errors="coerce").astype("Int64"),
            "volume": pd.to_numeric(df["acml_vol"], errors="coerce").astype("Int64"),
            "trading_value": pd.to_numeric(df["acml_tr_pbmn"], errors="coerce").astype("Int64"),
            "change_rate": pd.array([pd.NA] * len(df), dtype="Float64"),
        }
    )
    return out[_OUTPUT_COLS]


def _fetch_chunk(
    client: KISClient,
    code: str,
    fromdate: date,
    todate: date,
    adjusted: bool,
) -> pd.DataFrame:
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": code,
        "FID_INPUT_DATE_1": _yyyymmdd(fromdate),
        "FID_INPUT_DATE_2": _yyyymmdd(todate),
        "FID_PERIOD_DIV_CODE": "D",
        "FID_ORG_ADJ_PRC": "0" if adjusted else "1",
    }
    payload = client.get(_ENDPOINT, _TR_ID, params=params)
    rows = payload.get("output2") or []
    rows = [r for r in rows if r.get("stck_bsop_date")]
    return _parse_output2(rows, code)


def fetch_one_ticker(
    client: KISClient,
    code: str,
    fromdate: date,
    todate: date,
    adjusted: bool = True,
) -> pd.DataFrame:
    """단일 종목 [fromdate, todate] 일봉. 100건 초과 기간은 청크 분할.

    KIS 응답은 최신순(desc)이라 가장 오래된 날짜 기준으로 다음 청크를 잘라낸다.
    빈 청크가 나오면 그 이전 데이터는 없다고 판단하고 종료(상장 전 / 거래정지 장기).
    """
    if fromdate > todate:
        return _empty()

    chunks: list[pd.DataFrame] = []
    cur_to = todate
    safety = 100  # 무한루프 방지 (1만일=27년 분량 충분)
    while cur_to >= fromdate and safety > 0:
        cur_from = max(fromdate, cur_to - timedelta(days=_CHUNK_DAYS))
        try:
            chunk = _fetch_chunk(client, code, cur_from, cur_to, adjusted)
        except KISApiError as e:
            logger.warning(f"{code} {cur_from}~{cur_to}: KIS 에러 — {e}")
            break
        if chunk.empty:
            break
        chunks.append(chunk)
        oldest = chunk["date"].min()
        cur_to = oldest - timedelta(days=1)
        safety -= 1

    if not chunks:
        return _empty()

    df = pd.concat(chunks, ignore_index=True)
    df = (
        df.drop_duplicates(subset=["code", "date"])
        .sort_values(["code", "date"])
        .reset_index(drop=True)
    )
    return df
