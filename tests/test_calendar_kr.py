"""src.calendar_kr 테스트. pykrx 호출은 mock으로 차단."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from src import calendar_kr


@pytest.fixture
def mock_weekdays_only(monkeypatch):
    """주말만 휴장하는 단순화된 영업일 집합으로 교체.

    실제 KRX는 신정/설/추석/근로자의날 등이 추가로 휴장이지만,
    헬퍼의 루프 로직 자체를 검증하는 데는 평일=영업일 가정으로 충분.
    """

    def fake(year: int):
        d = date(year, 1, 1)
        end = date(year, 12, 31)
        out: set[date] = set()
        while d <= end:
            if d.weekday() < 5:
                out.add(d)
            d += timedelta(days=1)
        return frozenset(out)

    monkeypatch.setattr(calendar_kr, "krx_business_days", fake)


def test_is_weekend():
    assert calendar_kr.is_weekend(date(2025, 5, 3)) is True   # 토
    assert calendar_kr.is_weekend(date(2025, 5, 4)) is True   # 일
    assert calendar_kr.is_weekend(date(2025, 5, 5)) is False  # 월


def test_is_business_day(mock_weekdays_only):
    assert calendar_kr.is_business_day(date(2025, 5, 2)) is True   # 금
    assert calendar_kr.is_business_day(date(2025, 5, 3)) is False  # 토
    assert calendar_kr.is_business_day(date(2025, 5, 4)) is False  # 일


def test_previous_business_day_skips_weekend(mock_weekdays_only):
    # 월요일(2025-05-05)의 직전 영업일 = 금요일(2025-05-02)
    assert calendar_kr.previous_business_day(date(2025, 5, 5)) == date(2025, 5, 2)


def test_next_business_day_skips_weekend(mock_weekdays_only):
    # 금요일(2025-05-02)의 다음 영업일 = 월요일(2025-05-05)
    assert calendar_kr.next_business_day(date(2025, 5, 2)) == date(2025, 5, 5)


def test_previous_and_next_are_strict(mock_weekdays_only):
    """대상일 자체가 영업일이어도 '직전/다음'은 그 날을 반환하지 X."""
    d = date(2025, 5, 6)  # 화요일 (영업일 가정)
    assert calendar_kr.previous_business_day(d) == date(2025, 5, 5)
    assert calendar_kr.next_business_day(d) == date(2025, 5, 7)
