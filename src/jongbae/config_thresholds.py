"""주도섹터/주도주 식별 + 모니터링 임계값 (M5.5/M6).

운영 중 사용자 피드백으로 튜닝하는 항목들. 한 곳에 모아 두고
`docs/jongbae-strategy.md` R3/R3' 의 정량 정의와 1:1 대응.
"""
from __future__ import annotations

# ── R3 주도섹터(테마) z-score 합산 ───────────────────────────────────────────

# 거래대금 상위 몇 위까지 보고 테마 집계할지. v0=30, v1(M5.5)=50.
LEADING_SECTOR_TOP_N: int = 50

# 테마 스코어 상위 몇 개를 주도섹터로 채택할지.
LEADING_SECTOR_COUNT: int = 3

# 테마 스코어 가중치 (breadth, 평균 상승률, 회전율 합계).
# 동일 가중 1:1:1 시작. 운영 튜닝.
SECTOR_WEIGHT_BREADTH: float = 1.0
SECTOR_WEIGHT_RETURN: float = 1.0
SECTOR_WEIGHT_TURNOVER: float = 1.0

# breadth 임계 — 테마 내 +X%↑ 종목 수.
SECTOR_BREADTH_RETURN_THRESHOLD: float = 5.0  # +5% 이상 종목 수가 breadth

# 테마가 주도섹터 후보로 인정받는 최소 구성종목 수
# (한 종목짜리 테마는 의미 없음).
SECTOR_MIN_MEMBER_COUNT: int = 3


# ── R3' 주도주 식별 ───────────────────────────────────────────────────────────

# 한 주도섹터 당 주도주 후보 최대 개수. 회전율 기준 상위 N.
LEADING_STOCK_TOP_PER_SECTOR: int = 1


# ── R3' 주도주 교체 상태 머신 (M6) ───────────────────────────────────────────

# TRANSITION 진입 — 부상 후보 감지.
#   a2 가속배율 ≥ X AND 분봉거래대금 ≥ Y AND a2 회전율 ≥ a1 × Z
TRANSITION_ACCEL_RATIO: float = 5.0           # 가속배율 5배
TRANSITION_MIN_BAR_VALUE: int = 20_000_000_000  # 분봉 거래대금 20억 (원)
TRANSITION_TURNOVER_RATIO: float = 0.6         # a2 회전율 ≥ a1 × 0.6

# 강한 부상 강조.
STRONG_RISE_ACCEL_RATIO: float = 10.0          # 가속배율 10배 = 실무 화살표 신호

# 자금 이탈 경보 — 가속배율 음수 (감소율).
EXIT_ACCEL_RATIO: float = -0.4                 # 직전 30분 대비 -40% 이하

# GRACE — 실제 교체 후 유예기 (a1, a2 함께 표시).
GRACE_PERIOD_SECONDS: int = 5 * 60             # 5분

# TRANSITION 해제 — 후보 a2 약화 시.
TRANSITION_EXIT_TURNOVER_RATIO: float = 0.4    # a2 회전율 < a1 × 0.4
TRANSITION_EXIT_PERSIST_SECONDS: int = 3 * 60  # 3분 지속


# ── M6 모니터링 운영 ─────────────────────────────────────────────────────────

# 자동 운영 시간 (평일).
MONITORING_START_HOUR: int = 9
MONITORING_START_MINUTE: int = 0
MONITORING_END_HOUR: int = 10
MONITORING_END_MINUTE: int = 30

# 동시 모니터링 종목 수 → 갱신 간격 (초).
def monitoring_interval_seconds(n_codes: int) -> int:
    """종목 수에 따른 갱신 간격. KIS rate limit(20cps) 보호.

    종목당 4지표(분봉/체결강도/호가/순매수) = 4콜.
    """
    if n_codes <= 0:
        return 0
    if n_codes <= 2:
        return 2
    if n_codes <= 5:
        return 3
    if n_codes <= 10:
        return 5
    return 0  # 거부


MONITORING_MAX_CODES: int = 10  # 이 이상은 추가 거부

# 분봉 가속배율 계산 윈도우.
ACCEL_RECENT_BAR_MINUTES: int = 5    # 최근 5분봉 거래대금
ACCEL_BASELINE_MINUTES: int = 30     # 직전 30분 평균

# 신고가 임계 (보조 지표).
RECENT_HIGH_LOOKBACK_DAYS: int = 20
