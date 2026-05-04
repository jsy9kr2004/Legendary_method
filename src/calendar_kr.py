"""KRX 영업일 / 휴장일 헬퍼.

pykrx의 KOSPI 인덱스(코드 1001) OHLCV 일자 인덱스를 KRX 영업일로 사용한다.
연 단위로 lru_cache 적용 (네트워크 호출 최소화).

테스트에서는 `krx_business_days`를 monkeypatch로 갈아끼워 외부 호출을 피한다.
"""
from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache


@lru_cache(maxsize=16)
def _fetch_business_days(year: int) -> frozenset[date]:
    """pykrx로부터 해당 연도 KRX 영업일 집합을 받아온다."""
    from pykrx import stock

    fromdate = f"{year}0101"
    todate = f"{year}1231"
    df = stock.get_index_ohlcv_by_date(fromdate, todate, "1001")
    return frozenset(idx.date() for idx in df.index)


def krx_business_days(year: int) -> frozenset[date]:
    """KRX 영업일 집합 (연 단위)."""
    return _fetch_business_days(year)


def is_weekend(d: date) -> bool:
    return d.weekday() >= 5


def is_business_day(d: date) -> bool:
    return d in krx_business_days(d.year)


def previous_business_day(d: date) -> date:
    cur = d - timedelta(days=1)
    while not is_business_day(cur):
        cur -= timedelta(days=1)
    return cur


def next_business_day(d: date) -> date:
    cur = d + timedelta(days=1)
    while not is_business_day(cur):
        cur += timedelta(days=1)
    return cur
