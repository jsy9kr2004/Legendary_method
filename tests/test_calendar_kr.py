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
    # 2025-05-13(화) 직전 영업일 = 5-12(월). 어린이날/대체공휴일과 무관 영역.
    assert calendar_kr.previous_business_day(date(2025, 5, 13)) == date(2025, 5, 12)


def test_next_business_day_skips_weekend():
    # 5-13(화) 다음 영업일 = 5-14(수)
    assert calendar_kr.next_business_day(date(2025, 5, 13)) == date(2025, 5, 14)


def test_previous_and_next_are_strict():
    """대상일이 영업일이어도 직전/다음은 그 날을 반환하지 X."""
    d = date(2025, 5, 14)  # 수
    assert calendar_kr.previous_business_day(d) == date(2025, 5, 13)
    assert calendar_kr.next_business_day(d) == date(2025, 5, 15)


# ── KRX 정밀 휴장일 ──────────────────────────────────────────────────────────


def test_korean_new_year_2025_is_not_business():
    """설 2025-01-29 (수) — 평일이지만 휴장."""
    assert calendar_kr.is_business_day(date(2025, 1, 29)) is False
    assert calendar_kr.is_holiday(date(2025, 1, 29)) is True


def test_korean_new_year_eve_is_not_business():
    """2025-01-28 (화) 설 전날 — 휴장."""
    assert calendar_kr.is_business_day(date(2025, 1, 28)) is False


def test_chuseok_2025_is_not_business():
    """추석 2025-10-07 (화) — 휴장."""
    assert calendar_kr.is_business_day(date(2025, 10, 7)) is False


def test_year_end_krx_is_not_business():
    """12월 31일 KRX 임시휴장 — 매년."""
    assert calendar_kr.is_business_day(date(2024, 12, 31)) is False  # 화
    assert calendar_kr.is_business_day(date(2025, 12, 31)) is False  # 수


def test_labor_day_2025_is_krx_holiday():
    """근로자의 날 5월 1일 — 법정 휴일은 아니나 KRX 휴장."""
    assert calendar_kr.is_business_day(date(2025, 5, 1)) is False


def test_normal_weekday_is_business():
    """평일 + 비공휴일."""
    assert calendar_kr.is_business_day(date(2025, 5, 7)) is True   # 수
    assert calendar_kr.is_business_day(date(2026, 5, 11)) is True  # 월


def test_previous_business_day_skips_holiday():
    """2025-01-31 (금)의 직전 영업일 = 2025-01-27 (월). 1/28~30 설연휴."""
    assert calendar_kr.previous_business_day(date(2025, 1, 31)) == date(2025, 1, 27)


def test_next_business_day_skips_holiday():
    """2024-12-30 (월) 다음 영업일 = 2025-01-02 (목).
       12-31 KRX 임시휴장 + 1-1 신정 + 1-2 정상."""
    assert calendar_kr.next_business_day(date(2024, 12, 30)) == date(2025, 1, 2)


def test_is_holiday_returns_false_for_normal_day():
    assert calendar_kr.is_holiday(date(2025, 5, 7)) is False


def test_is_holiday_returns_false_for_weekend():
    """주말은 holiday set 에 없음 — is_holiday 는 False, is_business_day 는 weekend 로 거름."""
    assert calendar_kr.is_holiday(date(2025, 5, 3)) is False  # 토
    assert calendar_kr.is_business_day(date(2025, 5, 3)) is False
