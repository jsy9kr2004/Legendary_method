"""src.calendar_kr 테스트 (v0 weekday 기반)."""
from __future__ import annotations

from datetime import date

from src import calendar_kr


def test_is_weekend():
    assert calendar_kr.is_weekend(date(2025, 5, 3)) is True   # 토
    assert calendar_kr.is_weekend(date(2025, 5, 4)) is True   # 일
    assert calendar_kr.is_weekend(date(2025, 5, 5)) is False  # 월


def test_is_weekday_inverse():
    assert calendar_kr.is_weekday(date(2025, 5, 2)) is True
    assert calendar_kr.is_weekday(date(2025, 5, 3)) is False


def test_is_business_day():
    assert calendar_kr.is_business_day(date(2025, 5, 2)) is True   # 금
    assert calendar_kr.is_business_day(date(2025, 5, 3)) is False  # 토


def test_previous_business_day_skips_weekend():
    # 월요일 직전 영업일 = 금요일
    assert calendar_kr.previous_business_day(date(2025, 5, 5)) == date(2025, 5, 2)


def test_next_business_day_skips_weekend():
    # 금요일 다음 영업일 = 월요일
    assert calendar_kr.next_business_day(date(2025, 5, 2)) == date(2025, 5, 5)


def test_previous_and_next_are_strict():
    """대상일이 영업일이어도 직전/다음은 그 날을 반환하지 X."""
    d = date(2025, 5, 6)  # 화
    assert calendar_kr.previous_business_day(d) == date(2025, 5, 5)
    assert calendar_kr.next_business_day(d) == date(2025, 5, 7)
