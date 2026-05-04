"""pykrx 일봉 OHLCV fetcher.

pykrx 응답(한국어 컬럼)을 표준 스키마로 정규화한다:
    code, date, open, high, low, close, volume, trading_value, change_rate

호출 빈도 throttle은 호출자(init/incremental) 책임. 네트워크 실패는
tenacity로 3회 재시도.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential

_COLMAP = {
    "시가": "open",
    "고가": "high",
    "저가": "low",
    "종가": "close",
    "거래량": "volume",
    "거래대금": "trading_value",
    "등락률": "change_rate",
}

_OUTPUT_COLS = ["code", "date", "open", "high", "low", "close",
                "volume", "trading_value", "change_rate"]


def _to_yyyymmdd(d: date) -> str:
    return d.strftime("%Y%m%d")


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=_OUTPUT_COLS)


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns=_COLMAP)
    keep = [c for c in _COLMAP.values() if c in df.columns]
    return df[keep]


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10), reraise=True)
def fetch_one_ticker(code: str, fromdate: date, todate: date) -> pd.DataFrame:
    """단일 종목의 [fromdate, todate] 일봉 (수정주가)."""
    from pykrx import stock

    raw = stock.get_market_ohlcv_by_date(
        _to_yyyymmdd(fromdate), _to_yyyymmdd(todate), code, adjusted=True
    )
    if raw is None or raw.empty:
        return _empty()

    df = _normalize_columns(raw).reset_index()
    # pykrx는 index 이름이 '날짜' 또는 datetime index.
    if "날짜" in df.columns:
        df = df.rename(columns={"날짜": "date"})
    elif "index" in df.columns:
        df = df.rename(columns={"index": "date"})
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df.insert(0, "code", code)
    return df[_OUTPUT_COLS]


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10), reraise=True)
def fetch_all_tickers_for_date(target: date, market: str) -> pd.DataFrame:
    """단일 날짜의 시장(KOSPI/KOSDAQ) 전종목 일봉.

    market: "KOSPI" | "KOSDAQ"
    """
    from pykrx import stock

    raw = stock.get_market_ohlcv_by_ticker(_to_yyyymmdd(target), market=market)
    if raw is None or raw.empty:
        return _empty()

    df = _normalize_columns(raw).reset_index()
    if "티커" in df.columns:
        df = df.rename(columns={"티커": "code"})
    elif "index" in df.columns:
        df = df.rename(columns={"index": "code"})
    df["date"] = target
    return df[_OUTPUT_COLS]


def fetch_all_market_for_date(target: date) -> pd.DataFrame:
    """KOSPI + KOSDAQ 합본."""
    kospi = fetch_all_tickers_for_date(target, "KOSPI")
    kosdaq = fetch_all_tickers_for_date(target, "KOSDAQ")
    return pd.concat([kospi, kosdaq], ignore_index=True)
