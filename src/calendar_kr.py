"""KRX 영업일 / 휴장일 헬퍼 (v0 단순화 버전).

v0 에서는 평일=영업일로 간주. 실제 KRX 공휴일은 일봉 fetcher 가 빈 응답을
받았을 때 자연 처리(휴장)되므로, 영업일 정확성이 critical 하지 않다.

향후 정밀화 옵션:
- KIS 인덱스 OHLCV (`FHKUP03500100`) 응답에서 영업일 인덱스 추출
- 한국 공휴일 정적 테이블 주입
"""
from __future__ import annotations

from datetime import date, timedelta


def is_weekend(d: date) -> bool:
    return d.weekday() >= 5


def is_weekday(d: date) -> bool:
    return d.weekday() < 5


def is_business_day(d: date) -> bool:
    """v0: weekend 만 거름. 공휴일은 fetcher 빈응답으로 자연 처리."""
    return is_weekday(d)


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
