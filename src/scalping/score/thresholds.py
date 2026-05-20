"""주도섹터/주도주 식별 + 모니터링 임계값 (M5.5/M6).

운영 중 사용자 피드백으로 튜닝하는 항목들. 한 곳에 모아 두고
`docs/scalping-strategy.md` Theme/Theme.Leader 의 정량 정의와 1:1 대응.
"""
from __future__ import annotations

# ── Theme 주도섹터(테마) z-score 합산 ───────────────────────────────────────────

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


# ── Theme.Leader 주도주 식별 ───────────────────────────────────────────────────────────

# 한 주도섹터 당 주도주 후보 최대 개수. 회전율 기준 상위 N.
LEADING_STOCK_TOP_PER_SECTOR: int = 1

# 주도주 후보 자격: 절대 거래대금 N위 안에 들어야 한다.
# 회전율만 보면 시총 작고 거래대금 작은 종목까지 1위로 잡혀 노이즈 종목이 올라옴.
# 사용자 의도: "순수 거래 대금으로만 N위 내 들어야 한다." (30 → 50, 2026-05-12)
LEADER_CANDIDATE_RANK_MAX: int = 50

# 주도주 후보 자격: 일일 상승률이 이 임계 미만이어야 한다 (%).
# 한국 일일 상하한 +30% 라서 +29~30% 종목은 사실상 상한가 도달/임박 → 거래정지 또는
# 매수 불가. 종배는 "곧 상한가 도달할 후보" 진입을 노리는데 이미 도달한 종목은
# 진입 불가능하므로 leader 후보에서 제외. 사용자 명시.
LEADER_EXCLUDE_DAILY_RETURN_PCT: float = 29.0

# 주도주 후보 자격: 일일 상승률이 이 임계 이상이어야 한다 (%).
# 하한가 직전 종목도 거래대금/회전율이 터지는데, 인버스 매매를 안 하므로 후보에서 제외.
# "거래대금이 갑자기 늘어나는 종목 = 주도주 후보"는 무조건 상승 중이어야 한다는
# 사용자 명시. > 0 면 상승, == 0 은 보합도 제외(매수 의미 없음).
LEADER_MIN_DAILY_RETURN_PCT: float = 0.0

# 주도주 + 후보 자동 모니터링 풀 확장: 거래대금 50위 안에서 회전율 상위 N 개를
# "후보"로 자동 모니터링에 추가. 주도섹터에 속하지 않더라도 시총 대비 거래대금이
# 갑자기 늘어나는 종목을 first-mover 단계에서 잡기 위함. 사용자 명시.
# MONITORING_MAX_CODES=10 안에서 leader 와 합쳐 들어감.
CANDIDATE_POOL_TOP_N: int = 5

# ── 부상 후보 다단계 funnel (round 21) ────────────────────────────────────────
#
# round 19 까지: identify_rising_candidates 가 snapshot 만 보고 회전율 상위 5개를
#   RISING 카드로 surface. 흥아해운처럼 거래대금만 크고 모멘텀 죽은 종목도 잡힘.
# round 21 정정: "부상 후보 = 매수 점수 높은 후보" 로 재정의. snapshot → 모멘텀
#   → 체결강도 → Buy.Score 풀스코어 4단계 깔때기로 통과한 종목만 카드.
#
# 한국 단타 통설(회전율 1위 / 양봉 우세 / 모멘텀 살아있음 / VP 100 이상) 그대로.

# Stage 1: 회전율 컷오프 — Stage 0 snapshot 필터 (rank 50 + 양봉 + +29% 미만)
# 통과 종목 중 회전율 상위 N 으로 좁힘. 비용 0 (snapshot fetch 결과만 사용).
RISING_STAGE1_TURNOVER_TOP_N: int = 15

# Stage 2/3 hard-fail 임계 — round 37 폐지. Buy.Score score 의 음수 가산 (Buy.VP VP_WEAK /
# Buy.Accel vol_accel weak / Buy.Candle 약한 봉) 과 중복이라 hard cliff 가 깜빡임만 유발하고
# false negative 도 만들었음. 상수는 외부 호환 위해 보존하되 사용 X.
RISING_STAGE2_VOL_ACCEL_MIN: float = 0.8  # deprecated (round 37)
RISING_STAGE3_VP_MIN: float = 100.0  # deprecated (round 37)

# Stage 4: Buy.Score 매수 점수 컷오프 — 이 점수 이상이어야 RISING 카드로 surface.
# 2.0 = WATCH 이상. STRONG 만 surface 하려면 5.0 으로 올리면 됨.
RISING_MIN_SCORE: float = 2.0


# ── Theme.Leader 주도주 교체 상태 머신 (M6) ───────────────────────────────────────────

# TRANSITION 진입 — 부상 후보 감지.
#   a2 가속배율 ≥ X AND 분봉거래대금 ≥ Y AND a2 회전율 ≥ a1 × Z
TRANSITION_ACCEL_RATIO: float = 5.0           # 가속배율 5배
TRANSITION_MIN_BAR_VALUE: int = 20_000_000_000  # 분봉 거래대금 20억 (원)
TRANSITION_TURNOVER_RATIO: float = 0.6         # a2 회전율 ≥ a1 × 0.6

# 강한 부상 강조.
STRONG_RISE_ACCEL_RATIO: float = 10.0          # 가속배율 10배 = 실무 화살표 신호

# 자금 이탈 경보 — 가속배율 음수 (감소율).
EXIT_ACCEL_RATIO: float = -0.4                 # 직전 30분 대비 -40% 이하

# 1분봉 가속 — 더 빠른 first-mover / 이탈 시그널 (5분봉은 lag 3~5분).
# 통설(i-whale 등): 직전 10분 평균 대비 3~5배 = 강한 진입 시그널.
ONE_MIN_RISE_ACCEL_RATIO: float = 3.0          # 1분봉 가속 3배 이상 → ⚡ 진입
ONE_MIN_RISE_MIN_BAR_VALUE: int = 500_000_000  # 동시 만족: 1분봉 거래대금 ≥ 5억
ONE_MIN_EXIT_ACCEL_RATIO: float = 0.4          # 1분봉 가속 0.4 미만 → ⚠ 이탈

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


MONITORING_MAX_CODES: int = 4   # 텔레그램 화면 한도 — 4개 넘으면 한 번에 안 보임

# tick 1회 소요시간 경고 임계. scheduler IntervalTrigger(2초) + max_instances=1 +
# coalesce=True 라 tick 이 이 값을 넘으면 다음 trigger 가 병합·드롭되어 실효
# 갱신 주기가 길어진다. worker 가 매 tick 계측 후 임계 초과 시 warning.
TICK_DURATION_WARN_SEC: float = 2.0

# 분봉 가속배율 계산 윈도우.
ACCEL_RECENT_BAR_MINUTES: int = 5    # 최근 5분봉 거래대금
ACCEL_BASELINE_MINUTES: int = 30     # 직전 30분 평균

# 신고가 임계 (보조 지표).
RECENT_HIGH_LOOKBACK_DAYS: int = 20


# ── Buy.VP 체결강도 (Volume Power, VP) ───────────────────────────────────────────

VP_BALANCED: float = 100.0                  # 균형선 (매수=매도 체결)
VP_STRONG_THRESHOLD: float = 110.0          # VP > 110 + 5MA > 100 = 강한 매수 우세 (+2)
VP_WEAK_THRESHOLD: float = 100.0            # VP < 100 = 매수 압력 약함 (-2)
VP_MA_SHORT_MINUTES: int = 5                # 5MA
VP_MA_LONG_MINUTES: int = 20                # 20MA


# ── Buy.Accel 다중 윈도우 거래대금 가속 (매수 점수/매도 트리거 전용) ─────────────────
# Theme.Leader 30분 분모 ACCEL_RECENT_BAR_MINUTES/ACCEL_BASELINE_MINUTES 와는 별개 용도

VOL_ACCEL_1M_RECENT: int = 1                # 최근 1분
VOL_ACCEL_1M_BASELINE: int = 5              # 직전 5분 평균
VOL_ACCEL_5M_RECENT: int = 5                # 최근 5분
VOL_ACCEL_5M_BASELINE: int = 20             # 직전 20분 평균

# Buy.Score 매수 점수 임계
VOL_ACCEL_5M_STRONG: float = 1.2            # +2점 (1분도 > 1.0 동반 시)
VOL_ACCEL_1M_STRONG: float = 1.0
VOL_ACCEL_5M_WEAK: float = 0.8              # -3점 (1분도 < 0.5 동반 시)
VOL_ACCEL_1M_WEAK: float = 0.5
VOL_ACCEL_1M_VERY_STRONG: float = 2.0       # +1점 (단일)
VOL_ACCEL_1M_DRAIN: float = 0.5             # -1점 (단일) / Exit.Triggers 자금 고갈 트리거
VOL_ACCEL_DRAIN_PERSIST_SECONDS: int = 120  # 자금 고갈 2분 지속 시 Exit.Triggers C3


# ── Buy.Candle 봉 패턴 ───────────────────────────────────────────────────────────────

CANDLE_BAR_MINUTES: int = 5                 # 5분봉 기준
UPPER_WICK_LONG: float = 0.4                # 0.4 초과 = 긴 윗꼬리 (-2)
UPPER_WICK_CLEAN: float = 0.3               # 0.3 미만 = 깨끗한 양봉 (+2 with bullish)
UPPER_WICK_BEARISH_EXIT: float = 0.5        # Exit.Triggers C4 윗꼬리 50%↑ 음봉 트리거


# ── Buy.Position 위치/맥락 ────────────────────────────────────────────────────────────

DIST_FROM_HIGH_MAX_PCT: float = -2.0        # 진입 필수조건: 당일고점 -2% 이내


# ── Buy.Div 다이버전스 ─────────────────────────────────────────────────────────────

DIVERGENCE_PRICE_WINDOW_MINUTES: int = 5    # 가격 변화 측정 윈도우


# ── Buy.Score.a VWAP 위치 (round 23, P0-1) ──────────────────────────────────────────
# 통설: VWAP = 거래량 가중 평균가 = 장중 세력 평단가의 근사. 단타에서 가격이
# VWAP 위면 매수 우위, 아래면 매도 우위. 한국 단타 통설(거래량 가중 평균값 -
# TradingView/KRX) 그대로. 임계 ±0.3% 는 호가 노이즈 컷오프.
VWAP_ABOVE_THRESHOLD_PCT: float = 0.3       # +0.3% 이상 위 → +1 (Buy.Score)
VWAP_BELOW_THRESHOLD_PCT: float = -0.3      # -0.3% 이하 아래 → -1 (Buy.Score)


# ── Buy.Score.b 5/20분 이평 위치 (round 24, P0-2) ───────────────────────────────────
# 통설: 단타에선 5일/20일 이평이 가장 많이 쓰임 (namu.wiki 단타매매기법,
# 알파스퀘어). 5분봉 기준 → 1분봉 5개/20개 close 평균. Exit.Triggers A3 (5분 이평 이탈
# = 청산)와 대칭 — 진입에서 가격>MA5 가산. 정배열(가격이 MA5/MA20 둘 다 위)
# 매수 우위, 역배열 매도 우위. ±0.3% VWAP 과 동일 노이즈 컷.
MA5_THRESHOLD_PCT: float = 0.3              # 정배열 컷
MA20_THRESHOLD_PCT: float = 0.3
MA_SHORT_MINUTES: int = 5                   # 1분봉 5개 = 5분 SMA
MA_LONG_MINUTES: int = 20                   # 1분봉 20개 = 20분 SMA


# ── Buy.Score.c 상한가 진입 시간 가산 (round 25, P1-1) ──────────────────────────────
# 통설(namu.wiki 상따): "강한 상한가 진입 시간은 대략 오전 9~10시, 보통 9:30
# 이내 진입이 강함". 일중 first-mover 가산점. 상한가 도달 시각 기준 — 도달
# 안 한 종목은 None → 무가산. 시간이 늦을수록 가산 감쇠.
LIMIT_UP_EARLY_HH: int = 9                  # 09:30 이전 → +1
LIMIT_UP_EARLY_MM: int = 30
LIMIT_UP_MID_HH: int = 10                   # 10:30 이전 → +0.5
LIMIT_UP_MID_MM: int = 30


# ── Buy.Score.d 거래량 비율 검증 (round 28, P2-2) ───────────────────────────────────
# 통설(namu.wiki 상따): "거래량은 전날 대비 300% 이내가 정상. 10배 이상 동반
# 시 강한 상한가 아니므로 주의" — 폭증은 매도 출회/단발 신호일 가능성.
# 오늘 누적 거래량 / 전일 일봉 거래량.
VOLUME_RATIO_NORMAL_MIN: float = 1.0        # 전일 100% 이상
VOLUME_RATIO_NORMAL_MAX: float = 3.0        # 300% 이내 (정상 매집) → +0.5
VOLUME_RATIO_EXCESSIVE: float = 10.0        # 10배 이상 (과열 약신호) → -1


# ── 종배 청산 시초가 룰 (round 30, P3-2) ─────────────────────────────────────
# 통설(WikiDocs 종가베팅, brokdam 광전자 사례): 다음날 시초가가
#   ≤ +1% (또는 마이너스) → 갭 미발생/실패, 전량 매도 (보유 의미 없음)
#   +1% ~ +6%               → 정상 갭, 전량 익절 (단타 종료)
#   ≥ +6%                   → 강한 갭, 30~50% 분할 익절 후 관망 (추가 슈팅 노림)
# 이건 Exit.Triggers (장중 보유 모니터링) 와 다른 시각/컨텍스트.
JONGBAE_OPEN_FULL_SELL_MAX_PCT: float = 1.0      # ≤ +1% → 갭 실패, 전량
JONGBAE_OPEN_PARTIAL_SELL_MIN_PCT: float = 6.0   # ≥ +6% → 분할 익절
JONGBAE_OPEN_PARTIAL_RATIO: float = 0.4          # 30~50% 중간값 (40% 익절, 60% 관망)


# ── Buy.Score 매수 점수 등급 ─────────────────────────────────────────────────────────

GRADE_STRONG: float = 5.0
GRADE_WATCH: float = 2.0
GRADE_NEUTRAL: float = -1.0
# 그 외 = AVOID

# 거래대금 회전율 순위 임계
VOLUME_TURNOVER_TOP_N: int = 10             # 회전율 10위 이내 (+1)

# 호가잔량 보조 가산
BID_ASK_RATIO_THRESHOLD: float = 3.0        # +0.5 (강등된 가중치)


# ── Exit.Triggers 매도 트리거 ────────────────────────────────────────────────────────────

# A5 EOD 컷오프 (round 26, P1-2) — 통설: "14:45 이평선 밑 음봉이면 목숨 걸고
# 팔아라". 장 마감 임박 + 약세 시그널 AND 조건. 단순 시간 손절(A4)과 별개.
EOD_CUTOFF_HH: int = 14
EOD_CUTOFF_MM: int = 45

STOP_LOSS_PCT: float = -2.0                 # A1 진입가 대비 -2% (사용자 룰 통일, 2026-05-21)
TAKE_PROFIT_1_PCT: float = 2.0              # B1 +2.0% (1/3)
TAKE_PROFIT_2_PCT: float = 3.5              # B2 +3.5% (1/3)
TRAILING_STOP_PCT: float = -1.5             # B3 고점 대비 -1.5%
TIME_STOP_MINUTES_DEFAULT: int = 10         # A4 시간 손절 (오버라이드 가능)
TIME_STOP_REQUIRED_PROFIT_PCT: float = 0.5  # A4 N분 내 +0.5% 미달 시 발화
ENTRY_BAR_MA_MINUTES: int = 5               # A3 5분 이평 이탈 기준
VI_FAILURE_WINDOW_SECONDS: int = 300        # C5 VI 발동 후 5분 내 고가 회복 X
