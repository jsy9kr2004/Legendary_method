"""R14 매수 점수 가중치/임계 dataclass.

`calculate_buy_score(snap, thresholds=DEFAULT_THRESHOLDS)` 의 thresholds 인자.
기존 호출자(worker, scheduler 등)는 인자 안 주면 default 로 동작 — 역호환.

설계 목적: 가중치 sensitivity backtest. tick_log raw 시그널을 같은
GraderSnapshot 으로 재구성한 뒤 N variant 로 동시 재계산해서 STRONG 분포 /
다음날 수익률 차이 비교. `scripts/backtest_grader.py` 가 사용.

본체 grader.py 안 건드리고 가중치만 변경하려면 thresholds 만 교체.
로직 변경 (예: 다단계 가산) 은 default 가중치를 0 으로 두는 방식으로 default
동작 유지 + variant 가 weight 켜는 방식.

variant 정의 위치도 본 파일 — `VARIANTS` dict 로 backtest 스크립트에서 일괄
로딩.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass


@dataclass(frozen=True)
class GraderThresholds:
    """R14 매수 점수의 모든 가중치/임계 캡슐화.

    `frozen=True` — variant 가 실수로 mutate 못 하게. 새 variant 는
    `dataclasses.replace(DEFAULT_THRESHOLDS, ...)` 로.
    """

    # ── R3 회전율 (1차 약한 필터) ──────────────────────────────────────────
    volume_turnover_top_n: int = 10
    weight_turnover_top: float = 1.0

    # ── R11 가속 (5m + 1m 동반) ────────────────────────────────────────────
    vol_accel_5m_strong: float = 1.2
    vol_accel_1m_strong: float = 1.0
    vol_accel_5m_weak: float = 0.8
    vol_accel_1m_weak: float = 0.5
    weight_accel_double_strong: float = 2.0    # 5m+1m 동반 가속 (>)
    weight_accel_double_weak: float = -3.0     # 5m+1m 동반 감속 (≤)

    # ── R11 가속 (1m 단일) ─────────────────────────────────────────────────
    vol_accel_1m_very_strong: float = 2.0
    vol_accel_1m_drain: float = 0.5
    weight_accel_1m_very_strong: float = 1.0
    weight_accel_1m_drain: float = -1.0
    # Q1: 1분 가속 중간 단계 (default 비활성 — weight 0)
    # Q1 적용 시 vol_accel_1m_mild=1.5 + weight_accel_1m_mild=0.5
    vol_accel_1m_mild: float = 999.0           # 기본은 임계 너무 높게 → 안 통과
    weight_accel_1m_mild: float = 0.0          # default 무가산

    # ── R12 봉 패턴 (helper is_clean_bullish/is_weak_candle 호출) ───────────
    weight_candle_clean_bullish: float = 2.0
    weight_candle_weak: float = -2.0

    # ── R10 체결강도 VP (helper is_vp_strong/is_vp_weak 호출) ──────────────
    weight_vp_strong: float = 2.0
    weight_vp_weak: float = -2.0

    # ── R13 다이버전스 (round 27 강등 ±1) ──────────────────────────────────
    weight_div_bearish: float = -1.0
    weight_div_bullish: float = 1.0

    # ── 호가 잔량 보조 (round 13 강등) ─────────────────────────────────────
    bid_ask_ratio_threshold: float = 3.0
    weight_bid_ask_high: float = 0.5

    # ── R14a VWAP 위치 (3단 가산 — default 는 중간 단계만 활성) ───────────
    # default: ≥+0.3% → +1 / ≤-0.3% → -1 / 사이 0
    # Q3: ≥+1.0% → +1 / ≥+0.3% → +0.5 / ≥+0.0% → +0.2 (점진 ramp)
    vwap_strong_above: float = 999.0           # default 비활성
    vwap_above: float = 0.3
    vwap_mild_above: float = -999.0            # default 비활성
    vwap_below: float = -0.3
    vwap_strong_below: float = -999.0          # default 비활성
    weight_vwap_strong_above: float = 0.0
    weight_vwap_above: float = 1.0
    weight_vwap_mild_above: float = 0.0
    weight_vwap_below: float = -1.0
    weight_vwap_strong_below: float = 0.0

    # ── R14b MA5/MA20 정/역배열 ────────────────────────────────────────────
    ma5_threshold: float = 0.3
    ma20_threshold: float = 0.3
    weight_ma_bullish: float = 1.0
    weight_ma_bearish: float = -1.0

    # ── R14c 상한가 도달 시각 ──────────────────────────────────────────────
    limit_up_early_hh: int = 9
    limit_up_early_mm: int = 30
    limit_up_mid_hh: int = 10
    limit_up_mid_mm: int = 30
    weight_limit_up_early: float = 1.0
    weight_limit_up_mid: float = 0.5

    # ── R14d 거래량 비율 vs 전일 ────────────────────────────────────────────
    volume_ratio_normal_min: float = 1.0
    volume_ratio_normal_max: float = 3.0
    volume_ratio_excessive: float = 10.0
    weight_volume_ratio_normal: float = 0.5
    weight_volume_ratio_excessive: float = -1.0

    # ── 등급 컷 ────────────────────────────────────────────────────────────
    grade_strong: float = 5.0
    grade_watch: float = 2.0
    grade_neutral: float = -1.0

    # ── 진입 필수조건 임계 (점수와 별도, AND 체크용) ───────────────────────
    dist_from_high_max_pct: float = -2.0
    vp_strong_threshold: float = 110.0         # helper 임계와 동일
    vp_balanced: float = 100.0


# 기본 thresholds — 현재 운영 가중치. config_thresholds.py 와 동일.
DEFAULT_THRESHOLDS = GraderThresholds()


# ── Variant 정의 (backtest 비교용) ──────────────────────────────────────────
#
# Q1: 1분 가속 임계 완화 + 중간 단계 가산
#   first-mover 시점에 vol_accel_1m 이 1.5~2.0 인데 default 는 무가산.
#   Q1 은 1.5 부터 +0.5, 2.0 부터 +1.5 (default +1 보다 0.5 강화).
THRESHOLDS_Q1 = dataclasses.replace(
    DEFAULT_THRESHOLDS,
    vol_accel_1m_mild=1.5,
    weight_accel_1m_mild=0.5,
    weight_accel_1m_very_strong=1.5,
)

# Q3: VWAP/MA 점진 가산 (cliff → ramp)
#   default 는 ±0.3% hard cliff. Q3 는 0~+0.3 약한 가산(+0.2), +1.0 이상 강한
#   가산(+1.0), +0.3~+1.0 중간(+0.5). 음수도 대칭.
THRESHOLDS_Q3 = dataclasses.replace(
    DEFAULT_THRESHOLDS,
    vwap_strong_above=1.0,
    vwap_mild_above=0.0,
    vwap_strong_below=-1.0,
    weight_vwap_strong_above=1.0,
    weight_vwap_above=0.5,
    weight_vwap_mild_above=0.2,
    weight_vwap_strong_below=-1.0,
)

# Q5: STRONG 컷 5 → 4 (1점 낮춤)
#   first-mover 단계에서 surface. 위양성 ↑ 위험 — 단독 효과 측정.
THRESHOLDS_Q5 = dataclasses.replace(
    DEFAULT_THRESHOLDS,
    grade_strong=4.0,
)

# 조합 — Q1+Q3 (가장 first-mover 친화)
THRESHOLDS_Q1_Q3 = dataclasses.replace(
    THRESHOLDS_Q1,
    vwap_strong_above=THRESHOLDS_Q3.vwap_strong_above,
    vwap_mild_above=THRESHOLDS_Q3.vwap_mild_above,
    vwap_strong_below=THRESHOLDS_Q3.vwap_strong_below,
    weight_vwap_strong_above=THRESHOLDS_Q3.weight_vwap_strong_above,
    weight_vwap_above=THRESHOLDS_Q3.weight_vwap_above,
    weight_vwap_mild_above=THRESHOLDS_Q3.weight_vwap_mild_above,
    weight_vwap_strong_below=THRESHOLDS_Q3.weight_vwap_strong_below,
)

# 조합 — Q1+Q3+Q5 (모두 적용 — STRONG 컷도 4)
THRESHOLDS_Q1_Q3_Q5 = dataclasses.replace(
    THRESHOLDS_Q1_Q3,
    grade_strong=4.0,
)


VARIANTS: dict[str, GraderThresholds] = {
    "current": DEFAULT_THRESHOLDS,
    "q1": THRESHOLDS_Q1,
    "q3": THRESHOLDS_Q3,
    "q5": THRESHOLDS_Q5,
    "q1+q3": THRESHOLDS_Q1_Q3,
    "q1+q3+q5": THRESHOLDS_Q1_Q3_Q5,
}
