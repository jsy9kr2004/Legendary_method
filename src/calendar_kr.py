"""KRX 영업일 / 휴장일 헬퍼.

v1 (M5+): 주말 + 한국 법정공휴일 + KRX 임시휴장일 정적 테이블.
    `_KRX_HOLIDAYS` set 에 매년 KRX 발표 휴장일 누적. 2024~2027 큐레이션.
    매년 12월 KRX 가 다음해 휴장일 발표 시 본 파일 갱신.

향후:
    - KIS 인덱스 OHLCV 응답에서 영업일 인덱스 자동 추출 (백업 검증)
    - holidays PyPI 패키지 사용 (외부 의존성 추가 시)
"""
from __future__ import annotations

from datetime import date, timedelta


# KRX 휴장일 정적 테이블 (법정공휴일 + KRX 임시휴장 합본).
# 출처: KRX "유가증권시장 휴장일 안내" 공식 발표.
# 매년 12월 갱신 필요. 검증되지 않은 미래 연도는 비워두고 weekday + 발표 후 추가.
_KRX_HOLIDAYS: set[date] = {
    # ── 2024 (확정 + KRX 임시휴장) ───────────────────────────────────────────
    date(2024, 1, 1),    # 신정
    date(2024, 2, 9),    # 설 연휴
    date(2024, 2, 10),   # 설
    date(2024, 2, 11),   # 설 연휴
    date(2024, 2, 12),   # 설 대체공휴일
    date(2024, 3, 1),    # 삼일절
    date(2024, 4, 10),   # 22대 국회의원선거
    date(2024, 5, 1),    # 근로자의 날 (KRX 휴장)
    date(2024, 5, 5),    # 어린이날 (일요일)
    date(2024, 5, 6),    # 어린이날 대체공휴일
    date(2024, 5, 15),   # 부처님오신날
    date(2024, 6, 6),    # 현충일
    date(2024, 8, 15),   # 광복절
    date(2024, 9, 16),   # 추석 대체
    date(2024, 9, 17),   # 추석
    date(2024, 9, 18),   # 추석 연휴
    date(2024, 10, 1),   # 국군의 날 (임시공휴일)
    date(2024, 10, 3),   # 개천절
    date(2024, 10, 9),   # 한글날
    date(2024, 12, 25),  # 성탄절
    date(2024, 12, 31),  # 연말 KRX 임시휴장

    # ── 2025 (KRX 발표 기준) ────────────────────────────────────────────────
    date(2025, 1, 1),    # 신정
    date(2025, 1, 28),   # 설 연휴
    date(2025, 1, 29),   # 설
    date(2025, 1, 30),   # 설 연휴
    date(2025, 3, 3),    # 삼일절 대체
    date(2025, 5, 1),    # 근로자의 날
    date(2025, 5, 5),    # 어린이날 = 부처님오신날
    date(2025, 5, 6),    # 부처님오신날/어린이날 대체
    date(2025, 6, 3),    # 21대 대선
    date(2025, 6, 6),    # 현충일
    date(2025, 8, 15),   # 광복절
    date(2025, 10, 3),   # 개천절
    date(2025, 10, 6),   # 추석 연휴
    date(2025, 10, 7),   # 추석
    date(2025, 10, 8),   # 추석 연휴
    date(2025, 10, 9),   # 한글날
    date(2025, 12, 25),  # 성탄절
    date(2025, 12, 31),  # 연말 KRX 임시휴장

    # ── 2026 (잠정 — 음력 의존 일자는 발표 시 보정) ────────────────────────
    date(2026, 1, 1),    # 신정
    date(2026, 2, 16),   # 설 연휴
    date(2026, 2, 17),   # 설
    date(2026, 2, 18),   # 설 연휴
    date(2026, 3, 1),    # 삼일절 (일요일)
    date(2026, 3, 2),    # 삼일절 대체
    date(2026, 5, 1),    # 근로자의 날
    date(2026, 5, 5),    # 어린이날
    date(2026, 5, 25),   # 부처님오신날
    date(2026, 6, 6),    # 현충일 (토요일)
    date(2026, 8, 15),   # 광복절 (토요일)
    date(2026, 9, 24),   # 추석 연휴
    date(2026, 9, 25),   # 추석
    date(2026, 9, 26),   # 추석 연휴 (토요일)
    date(2026, 10, 3),   # 개천절 (토요일)
    date(2026, 10, 9),   # 한글날
    date(2026, 12, 25),  # 성탄절
    date(2026, 12, 31),  # 연말 KRX 임시휴장
}


def is_weekend(d: date) -> bool:
    return d.weekday() >= 5


def is_weekday(d: date) -> bool:
    return d.weekday() < 5


def is_holiday(d: date) -> bool:
    """KRX 정적 휴장일 테이블에 포함되는지 (법정공휴일 + 임시휴장)."""
    return d in _KRX_HOLIDAYS


def is_business_day(d: date) -> bool:
    """KRX 영업일.

    정량 정의:
        weekday(월~금) AND `_KRX_HOLIDAYS` set 에 없음.
    """
    return is_weekday(d) and not is_holiday(d)


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
