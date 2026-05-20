"""매수 점수 + 등급 (R14).

`docs/jongbae-strategy.md` R14 참조. 정정 이력 round 14.

배경: "개별 시그널마다 색상 부여" 방식은 호가 잔량 하나로 초록불 켜지는 가짜
매수 신호 발생 (흥아해운 케이스). 조합 점수 기반 등급으로 통일.

경고: 가중치/임계는 한국 단타 통설 조합이며 검증 데이터 누적 전엔 추정치.
흥아해운 회귀 + 추가 5~10 케이스 미통과 시 단순 룰
(VP < 100 AND vol_accel_1m < 0.5 → AVOID)로 폴백.

pure 함수 — Snapshot dataclass 입력, ScoreCard 출력.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Literal

from src.jongbae.candle import (
    CandleShape,
    is_clean_bullish,
    is_weak_candle,
)
from src.jongbae.divergence import DivergenceState
from src.jongbae.grader_thresholds import DEFAULT_THRESHOLDS, GraderThresholds
from src.jongbae.volume_power import is_vp_strong, is_vp_weak

Grade = Literal["STRONG", "WATCH", "NEUTRAL", "AVOID"]
GRADE_EMOJI: dict[Grade, str] = {
    "STRONG": "🟢",
    "WATCH": "🟡",
    "NEUTRAL": "⚫",
    "AVOID": "🔴",
}


@dataclass
class GraderSnapshot:
    """R14 매수 점수 계산 입력. 호출자가 1 tick 마다 수집해서 채운다.

    All fields optional — 누락은 해당 항목 가산점 X (감점 X). 다만 필수조건
    체크는 별도.
    """
    # 거래대금 회전율 순위 (1=최상위). None 이면 무시.
    volume_turnover_rank: int | None = None

    # R11 가속 (NaN 가능)
    vol_accel_1m: float = float("nan")
    vol_accel_5m: float = float("nan")

    # R12 봉 패턴
    candle: CandleShape | None = None

    # R10 체결강도
    vp: float = float("nan")
    vp_5ma: float = float("nan")

    # R13 다이버전스
    divergence: DivergenceState | None = None

    # 호가잔량 (보조)
    bid_ask_ratio: float = float("nan")

    # R12.5 위치/맥락 (진입 필수조건용)
    dist_from_intraday_high_pct: float = float("nan")

    # R14a VWAP 위치 (round 23, P0-1)
    # 호출자가 momentum.compute_vwap() + price_vs_vwap_pct() 로 미리 계산.
    price_vs_vwap_pct: float = float("nan")

    # R14b 5/20분 이평 위치 (round 24, P0-2)
    # 호출자가 momentum.compute_minute_ma() + price_vs_ma_pct() 로 미리 계산.
    price_vs_ma5_pct: float = float("nan")
    price_vs_ma20_pct: float = float("nan")

    # R14c 상한가 진입 시각 (round 25, P1-1) — None 이면 도달 안 함.
    # 호출자가 상한가 감지 시점에 dt.time(hour, minute) 으로 채움.
    limit_up_hit_time: dt.time | None = None

    # R14d 거래량 비율 (round 28, P2-2) — 오늘누적 / 전일.
    # 호출자가 일봉 데이터 조회해서 채움. NaN 이면 무가산.
    volume_ratio_vs_prev_day: float = float("nan")


@dataclass
class ScoreCard:
    score: float
    grade: Grade
    reasons: list[str] = field(default_factory=list)
    required_checks: dict[str, bool] = field(default_factory=dict)

    @property
    def passes_required(self) -> bool:
        if not self.required_checks:
            return False
        return all(self.required_checks.values())

    @property
    def emoji(self) -> str:
        return GRADE_EMOJI[self.grade]


def _grade_for(score: float, th: GraderThresholds) -> Grade:
    if score >= th.grade_strong:
        return "STRONG"
    if score >= th.grade_watch:
        return "WATCH"
    if score >= th.grade_neutral:
        return "NEUTRAL"
    return "AVOID"


def calculate_buy_score(
    snap: GraderSnapshot,
    thresholds: GraderThresholds = DEFAULT_THRESHOLDS,
) -> ScoreCard:
    """R14 매수 점수 + 등급. `docs/jongbae-strategy.md` R14 의 score 공식 그대로.

    Args:
        snap: 시그널 입력값 한 묶음 (호출자가 매 tick 수집).
        thresholds: 가중치/임계 묶음. default 는 운영 가중치 — 인자 안 주면 기존
            동작과 동일. backtest variant 비교 시 `THRESHOLDS_Q1` 등 전달.
    """
    th = thresholds
    score = 0.0
    reasons: list[str] = []

    # 거래대금 회전율 순위 (+1)
    if snap.volume_turnover_rank is not None and snap.volume_turnover_rank <= th.volume_turnover_top_n:
        score += th.weight_turnover_top
        reasons.append(f"+{th.weight_turnover_top:g} 거래대금 {th.volume_turnover_top_n}위내")

    # R11 가속 — 동반 가속/동반 감속
    a5 = snap.vol_accel_5m
    a1 = snap.vol_accel_1m
    a5_ok = a5 == a5  # not NaN
    a1_ok = a1 == a1
    if a5_ok and a1_ok and a5 > th.vol_accel_5m_strong and a1 > th.vol_accel_1m_strong:
        score += th.weight_accel_double_strong
        reasons.append(f"+{th.weight_accel_double_strong:g} 가속 동반 (5m {a5:.1f} / 1m {a1:.1f})")
    # 감속(WEAK)은 ≤ 임계 — "0.8 이하" 같은 한국 단타 통설 표현 부합.
    # 가속(STRONG)은 strict > — 임계 초과만 가산.
    if a5_ok and a1_ok and a5 <= th.vol_accel_5m_weak and a1 <= th.vol_accel_1m_weak:
        score += th.weight_accel_double_weak
        reasons.append(f"{th.weight_accel_double_weak:+g} 가속 죽음 (5m {a5:.1f} / 1m {a1:.1f})")

    # R12 봉 패턴
    if snap.candle is not None:
        if is_clean_bullish(snap.candle):
            score += th.weight_candle_clean_bullish
            reasons.append(f"+{th.weight_candle_clean_bullish:g} 장대양봉 (윗꼬리 {snap.candle.upper_wick*100:.0f}%)")
        if is_weak_candle(snap.candle):
            score += th.weight_candle_weak
            reasons.append(
                f"{th.weight_candle_weak:+g} 약한 봉 ({snap.candle.type} / 윗꼬리 {snap.candle.upper_wick*100:.0f}%)"
            )

    # R10 체결강도
    if is_vp_strong(snap.vp, snap.vp_5ma):
        score += th.weight_vp_strong
        reasons.append(f"+{th.weight_vp_strong:g} VP {snap.vp:.0f} 5MA {snap.vp_5ma:.0f}")
    if is_vp_weak(snap.vp):
        score += th.weight_vp_weak
        reasons.append(f"{th.weight_vp_weak:+g} VP<{th.vp_balanced:.0f} ({snap.vp:.0f})")

    # 가속 단일 (R11 추가) — VERY_STRONG / MILD (Q1) / DRAIN
    # 다단계는 strongest first — 같은 신호로 두 번 안 잡히게.
    if a1_ok and a1 > th.vol_accel_1m_very_strong:
        score += th.weight_accel_1m_very_strong
        reasons.append(f"+{th.weight_accel_1m_very_strong:g} vol_accel_1m {a1:.1f}배")
    elif a1_ok and a1 > th.vol_accel_1m_mild:
        score += th.weight_accel_1m_mild
        if th.weight_accel_1m_mild != 0.0:
            reasons.append(f"+{th.weight_accel_1m_mild:g} vol_accel_1m {a1:.1f}배 (mild)")
    if a1_ok and a1 < th.vol_accel_1m_drain:
        score += th.weight_accel_1m_drain
        reasons.append(f"{th.weight_accel_1m_drain:+g} 자금 고갈 (1m {a1:.1f})")

    # R13 다이버전스 (round 27, P2-1: ±2 → ±1 강등)
    # 통설 검색(namu.wiki 단타매매기법, i-whale 등)에서 다이버전스는 잘 안 나옴.
    # 차트분석/스윙 영역 지표라 단타 신뢰도 낮음 — 회전율(+1) 동급으로 강등.
    if snap.divergence is not None:
        if snap.divergence.bearish:
            score += th.weight_div_bearish
            reasons.append(f"{th.weight_div_bearish:+g} Bearish Divergence")
        if snap.divergence.bullish:
            score += th.weight_div_bullish
            reasons.append(f"+{th.weight_div_bullish:g} Bullish Divergence")

    # 호가잔량 (강등된 보조 가중)
    if snap.bid_ask_ratio == snap.bid_ask_ratio and snap.bid_ask_ratio > th.bid_ask_ratio_threshold:
        score += th.weight_bid_ask_high
        reasons.append(f"+{th.weight_bid_ask_high:g} 호가 {snap.bid_ask_ratio:.1f}배 (보조)")

    # R14a VWAP 위치 (round 23, P0-1) — 통설: VWAP 위 = 세력 평단 위 = 매수 우위
    # 다단계 분기 (Q3 ramp 지원). default 는 strong/mild 가중치 0 + 임계 ±999
    # 로 비활성 → 기존 cliff 동작 동일.
    v = snap.price_vs_vwap_pct
    if v == v:  # not NaN
        if v >= th.vwap_strong_above:
            score += th.weight_vwap_strong_above
            if th.weight_vwap_strong_above != 0.0:
                reasons.append(f"+{th.weight_vwap_strong_above:g} VWAP +{v:.2f}% 위(강)")
        elif v >= th.vwap_above:
            score += th.weight_vwap_above
            reasons.append(f"+{th.weight_vwap_above:g} VWAP +{v:.2f}% 위")
        elif v >= th.vwap_mild_above:
            score += th.weight_vwap_mild_above
            if th.weight_vwap_mild_above != 0.0:
                reasons.append(f"+{th.weight_vwap_mild_above:g} VWAP +{v:.2f}% 위(약)")
        elif v <= th.vwap_strong_below:
            score += th.weight_vwap_strong_below
            if th.weight_vwap_strong_below != 0.0:
                reasons.append(f"{th.weight_vwap_strong_below:+g} VWAP {v:.2f}% 아래(강)")
        elif v <= th.vwap_below:
            score += th.weight_vwap_below
            reasons.append(f"{th.weight_vwap_below:+g} VWAP {v:.2f}% 아래")

    # R14b 5/20분 이평 위치 (round 24, P0-2) — 통설: 정배열/역배열
    m5 = snap.price_vs_ma5_pct
    m20 = snap.price_vs_ma20_pct
    m5_ok = m5 == m5
    m20_ok = m20 == m20
    if m5_ok and m20_ok:
        if m5 >= th.ma5_threshold and m20 >= th.ma20_threshold:
            score += th.weight_ma_bullish
            reasons.append(f"+{th.weight_ma_bullish:g} 정배열 (MA5 +{m5:.2f}% / MA20 +{m20:.2f}%)")
        elif m5 <= -th.ma5_threshold and m20 <= -th.ma20_threshold:
            score += th.weight_ma_bearish
            reasons.append(f"{th.weight_ma_bearish:+g} 역배열 (MA5 {m5:.2f}% / MA20 {m20:.2f}%)")

    # R14c 상한가 진입 시간 가산 (round 25, P1-1)
    # 통설(상따): 9:30 이내 진입이 가장 강함, 10:30 이내까지 first-mover 인정.
    t = snap.limit_up_hit_time
    if t is not None:
        hm = (t.hour, t.minute)
        if hm < (th.limit_up_early_hh, th.limit_up_early_mm):
            score += th.weight_limit_up_early
            reasons.append(f"+{th.weight_limit_up_early:g} 상한가 조기진입 ({t.hour:02d}:{t.minute:02d})")
        elif hm < (th.limit_up_mid_hh, th.limit_up_mid_mm):
            score += th.weight_limit_up_mid
            reasons.append(f"+{th.weight_limit_up_mid:g} 상한가 진입 ({t.hour:02d}:{t.minute:02d})")

    # R14d 거래량 비율 검증 (round 28, P2-2)
    # 통설(상따): 전일 대비 100~300% 정상 매집, 10배↑ 과열(약신호).
    vr = snap.volume_ratio_vs_prev_day
    if vr == vr:  # not NaN
        if vr >= th.volume_ratio_excessive:
            score += th.weight_volume_ratio_excessive
            reasons.append(f"{th.weight_volume_ratio_excessive:+g} 거래량 {vr:.1f}배 (과열)")
        elif th.volume_ratio_normal_min <= vr <= th.volume_ratio_normal_max:
            score += th.weight_volume_ratio_normal
            reasons.append(f"+{th.weight_volume_ratio_normal:g} 거래량 {vr:.1f}배 (정상)")

    # 진입 필수조건 (등급과 별도)
    required = _check_required(snap, th)

    return ScoreCard(
        score=score,
        grade=_grade_for(score, th),
        reasons=reasons,
        required_checks=required,
    )


def _check_required(snap: GraderSnapshot, th: GraderThresholds) -> dict[str, bool]:
    """진입 필수조건 (AND).

    None / NaN 은 unknown — 보수적으로 False 처리.
    """
    checks: dict[str, bool] = {}

    checks["회전율↑"] = (
        snap.volume_turnover_rank is not None
        and snap.volume_turnover_rank <= th.volume_turnover_top_n
    )

    checks["VP>110+5MA>100"] = is_vp_strong(snap.vp, snap.vp_5ma)

    a5 = snap.vol_accel_5m
    a1 = snap.vol_accel_1m
    checks["가속 5m+1m"] = (
        a5 == a5 and a1 == a1
        and a5 > th.vol_accel_5m_strong and a1 > th.vol_accel_1m_strong
    )

    checks["장대양봉"] = (
        snap.candle is not None and is_clean_bullish(snap.candle)
    )

    dist = snap.dist_from_intraday_high_pct
    checks["고점-2%이내"] = (
        dist == dist and dist >= th.dist_from_high_max_pct
    )

    return checks
