"""매수 점수 + 등급 (Buy.Score).

`docs/scalping-strategy.md` Buy.Score 참조. 정정 이력 round 14.

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

from src.scalping.score.candle import (
    CandleShape,
    is_clean_bullish,
    is_weak_candle,
)
from src.scalping.score.thresholds import (
    BID_ASK_RATIO_THRESHOLD,
    DIST_FROM_HIGH_MAX_PCT,
    DIST_FROM_HIGH_NEAR_PENALTY_PCT,
    DIST_FROM_HIGH_VERY_NEAR_PENALTY_PCT,
    GRADE_NEUTRAL,
    GRADE_STRONG,
    GRADE_WATCH,
    LIMIT_UP_EARLY_HH,
    LIMIT_UP_EARLY_MM,
    LIMIT_UP_MID_HH,
    LIMIT_UP_MID_MM,
    SIDEWAYS_PEAK_DAILY_RETURN_PCT,
    SIDEWAYS_PEAK_DIST_FROM_HIGH_PCT,
    VOL_ACCEL_1M_DRAIN,
    VOL_ACCEL_1M_STRONG,
    VOL_ACCEL_1M_VERY_STRONG,
    VOL_ACCEL_1M_WEAK,
    VOL_ACCEL_5M_STRONG,
    VOL_ACCEL_5M_WEAK,
    MA5_THRESHOLD_PCT,
    MA20_THRESHOLD_PCT,
    VOLUME_RATIO_EXCESSIVE,
    VOLUME_RATIO_NORMAL_MAX,
    VOLUME_RATIO_NORMAL_MIN,
    VOLUME_TURNOVER_TOP_N,
    VP_BALANCED,
    VWAP_ABOVE_THRESHOLD_PCT,
    VWAP_BELOW_THRESHOLD_PCT,
    WEIGHT_DIST_FROM_HIGH_NEAR,
    WEIGHT_DIST_FROM_HIGH_VERY_NEAR,
    WEIGHT_SIDEWAYS_PEAK,
)
from src.scalping.score.divergence import DivergenceState
from src.scalping.score.vp import is_vp_strong, is_vp_weak

Grade = Literal["STRONG", "WATCH", "NEUTRAL", "AVOID"]
GRADE_EMOJI: dict[Grade, str] = {
    "STRONG": "🟢",
    "WATCH": "🟡",
    "NEUTRAL": "⚫",
    "AVOID": "🔴",
}


@dataclass
class GraderSnapshot:
    """Buy.Score 매수 점수 계산 입력. 호출자가 1 tick 마다 수집해서 채운다.

    All fields optional — 누락은 해당 항목 가산점 X (감점 X). 다만 필수조건
    체크는 별도.
    """
    # 거래대금 회전율 순위 (1=최상위). None 이면 무시.
    volume_turnover_rank: int | None = None

    # Buy.Accel 가속 (NaN 가능)
    vol_accel_1m: float = float("nan")
    vol_accel_5m: float = float("nan")

    # Buy.Candle 봉 패턴
    candle: CandleShape | None = None

    # Buy.VP 체결강도
    vp: float = float("nan")
    vp_5ma: float = float("nan")

    # Buy.Div 다이버전스
    divergence: DivergenceState | None = None

    # 호가잔량 (보조)
    bid_ask_ratio: float = float("nan")

    # Buy.Position 위치/맥락 (진입 필수조건용)
    dist_from_intraday_high_pct: float = float("nan")

    # Buy.Score.a VWAP 위치 (round 23, P0-1)
    # 호출자가 momentum.compute_vwap() + price_vs_vwap_pct() 로 미리 계산.
    price_vs_vwap_pct: float = float("nan")

    # Buy.Score.b 5/20분 이평 위치 (round 24, P0-2)
    # 호출자가 momentum.compute_minute_ma() + price_vs_ma_pct() 로 미리 계산.
    price_vs_ma5_pct: float = float("nan")
    price_vs_ma20_pct: float = float("nan")

    # Buy.Score.c 상한가 진입 시각 (round 25, P1-1) — None 이면 도달 안 함.
    # 호출자가 상한가 감지 시점에 dt.time(hour, minute) 으로 채움.
    limit_up_hit_time: dt.time | None = None

    # Buy.Score.d 거래량 비율 (round 28, P2-2) — 오늘누적 / 전일.
    # 호출자가 일봉 데이터 조회해서 채움. NaN 이면 무가산.
    volume_ratio_vs_prev_day: float = float("nan")

    # Buy.Score.l 일중 등락률 (2026-05-21, R14l 횡보 정점 페널티용).
    # snap_row.daily_return (KIS API prdy_ctrt) 그대로. NaN 이면 무가산.
    daily_return_pct: float = float("nan")


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


def _grade_for(score: float) -> Grade:
    if score >= GRADE_STRONG:
        return "STRONG"
    if score >= GRADE_WATCH:
        return "WATCH"
    if score >= GRADE_NEUTRAL:
        return "NEUTRAL"
    return "AVOID"


def calculate_buy_score(snap: GraderSnapshot) -> ScoreCard:
    """Buy.Score 매수 점수 + 등급. `docs/scalping-strategy.md` Buy.Score 의 score 공식 그대로."""
    score = 0.0
    reasons: list[str] = []

    # 거래대금 회전율 순위 (+1)
    if snap.volume_turnover_rank is not None and snap.volume_turnover_rank <= VOLUME_TURNOVER_TOP_N:
        score += 1
        reasons.append(f"+1 거래대금 {VOLUME_TURNOVER_TOP_N}위내")

    # Buy.Accel 가속 — 동반 가속/동반 감속
    a5 = snap.vol_accel_5m
    a1 = snap.vol_accel_1m
    a5_ok = a5 == a5  # not NaN
    a1_ok = a1 == a1
    if a5_ok and a1_ok and a5 > VOL_ACCEL_5M_STRONG and a1 > VOL_ACCEL_1M_STRONG:
        score += 2
        reasons.append(f"+2 가속 동반 (5m {a5:.1f} / 1m {a1:.1f})")
    # 감속(WEAK)은 ≤ 임계 — "0.8 이하" 같은 한국 단타 통설 표현 부합.
    # 가속(STRONG)은 strict > — 임계 초과만 가산.
    if a5_ok and a1_ok and a5 <= VOL_ACCEL_5M_WEAK and a1 <= VOL_ACCEL_1M_WEAK:
        score -= 3
        reasons.append(f"-3 가속 죽음 (5m {a5:.1f} / 1m {a1:.1f})")

    # Buy.Candle 봉 패턴
    if snap.candle is not None:
        if is_clean_bullish(snap.candle):
            score += 2
            reasons.append(f"+2 장대양봉 (윗꼬리 {snap.candle.upper_wick*100:.0f}%)")
        if is_weak_candle(snap.candle):
            score -= 2
            reasons.append(
                f"-2 약한 봉 ({snap.candle.type} / 윗꼬리 {snap.candle.upper_wick*100:.0f}%)"
            )

    # Buy.VP 체결강도
    if is_vp_strong(snap.vp, snap.vp_5ma):
        score += 2
        reasons.append(f"+2 VP {snap.vp:.0f} 5MA {snap.vp_5ma:.0f}")
    if is_vp_weak(snap.vp):
        score -= 2
        reasons.append(f"-2 VP<{VP_BALANCED:.0f} ({snap.vp:.0f})")

    # 가속 단일 (Buy.Accel 추가)
    if a1_ok and a1 > VOL_ACCEL_1M_VERY_STRONG:
        score += 1
        reasons.append(f"+1 vol_accel_1m {a1:.1f}배")
    if a1_ok and a1 < VOL_ACCEL_1M_DRAIN:
        score -= 1
        reasons.append(f"-1 자금 고갈 (1m {a1:.1f})")

    # Buy.Div 다이버전스 (round 27, P2-1: ±2 → ±1 강등)
    # 통설 검색(namu.wiki 단타매매기법, i-whale 등)에서 다이버전스는 잘 안 나옴.
    # 차트분석/스윙 영역 지표라 단타 신뢰도 낮음 — 회전율(+1) 동급으로 강등.
    if snap.divergence is not None:
        if snap.divergence.bearish:
            score -= 1
            reasons.append("-1 Bearish Divergence")
        if snap.divergence.bullish:
            score += 1
            reasons.append("+1 Bullish Divergence")

    # 호가잔량 (강등된 보조 가중)
    if snap.bid_ask_ratio == snap.bid_ask_ratio and snap.bid_ask_ratio > BID_ASK_RATIO_THRESHOLD:
        score += 0.5
        reasons.append(f"+0.5 호가 {snap.bid_ask_ratio:.1f}배 (보조)")

    # Buy.Score.a VWAP 위치 (round 23, P0-1) — 통설: VWAP 위 = 세력 평단 위 = 매수 우위
    v = snap.price_vs_vwap_pct
    if v == v:  # not NaN
        if v >= VWAP_ABOVE_THRESHOLD_PCT:
            score += 1
            reasons.append(f"+1 VWAP +{v:.2f}% 위")
        elif v <= VWAP_BELOW_THRESHOLD_PCT:
            score -= 1
            reasons.append(f"-1 VWAP {v:.2f}% 아래")

    # Buy.Score.b 5/20분 이평 위치 (round 24, P0-2) — 통설: 정배열/역배열
    m5 = snap.price_vs_ma5_pct
    m20 = snap.price_vs_ma20_pct
    m5_ok = m5 == m5
    m20_ok = m20 == m20
    if m5_ok and m20_ok:
        if m5 >= MA5_THRESHOLD_PCT and m20 >= MA20_THRESHOLD_PCT:
            score += 1
            reasons.append(f"+1 정배열 (MA5 +{m5:.2f}% / MA20 +{m20:.2f}%)")
        elif m5 <= -MA5_THRESHOLD_PCT and m20 <= -MA20_THRESHOLD_PCT:
            score -= 1
            reasons.append(f"-1 역배열 (MA5 {m5:.2f}% / MA20 {m20:.2f}%)")

    # Buy.Score.c 상한가 진입 시간 가산 (round 25, P1-1)
    # 통설(상따): 9:30 이내 진입이 가장 강함, 10:30 이내까지 first-mover 인정.
    t = snap.limit_up_hit_time
    if t is not None:
        hm = (t.hour, t.minute)
        if hm < (LIMIT_UP_EARLY_HH, LIMIT_UP_EARLY_MM):
            score += 1
            reasons.append(f"+1 상한가 조기진입 ({t.hour:02d}:{t.minute:02d})")
        elif hm < (LIMIT_UP_MID_HH, LIMIT_UP_MID_MM):
            score += 0.5
            reasons.append(f"+0.5 상한가 진입 ({t.hour:02d}:{t.minute:02d})")

    # Buy.Score.d 거래량 비율 검증 (round 28, P2-2)
    # 통설(상따): 전일 대비 100~300% 정상 매집, 10배↑ 과열(약신호).
    vr = snap.volume_ratio_vs_prev_day
    if vr == vr:  # not NaN
        if vr >= VOLUME_RATIO_EXCESSIVE:
            score -= 1
            reasons.append(f"-1 거래량 {vr:.1f}배 (과열)")
        elif VOLUME_RATIO_NORMAL_MIN <= vr <= VOLUME_RATIO_NORMAL_MAX:
            score += 0.5
            reasons.append(f"+0.5 거래량 {vr:.1f}배 (정상)")

    # ── R14k 일중 최고점 거리 페널티 (2026-05-21) ─────────────────────────────
    # 사용자 의도: "차트의 매수 포인트가 너무 고점에서 찾아옴" — 5/20 매매일지
    # §H7 + backtest_user_trades.py 검증. 정점 직후 + 횡보 정점 매수 회피.
    # 통설: namu.wiki 상따 "고점 추격 매수 회피" + Bollinger mean reversion.
    dist = snap.dist_from_intraday_high_pct
    if dist == dist:  # not NaN
        if dist >= DIST_FROM_HIGH_VERY_NEAR_PENALTY_PCT:  # -2% 이내 (정점 직전)
            score += WEIGHT_DIST_FROM_HIGH_VERY_NEAR  # -2
            reasons.append(f"{WEIGHT_DIST_FROM_HIGH_VERY_NEAR:+.0f} 정점 {dist:+.1f}% (R14k 정점근접)")
        elif dist >= DIST_FROM_HIGH_NEAR_PENALTY_PCT:  # -5% 이내
            score += WEIGHT_DIST_FROM_HIGH_NEAR  # -1
            reasons.append(f"{WEIGHT_DIST_FROM_HIGH_NEAR:+.0f} 정점근접 {dist:+.1f}% (R14k)")

    # ── R14l 횡보 정점 페널티 (2026-05-21) ────────────────────────────────────
    # 일중 상승률 ≥ 15% + 정점 5% 이내 = 폭등 후 횡보 micro fluctuation.
    # 수젠텍 케이스 (5/20 backtest 차단 효과 확인). 통설: i-whale "+15% 후 횡보 회피".
    dr = snap.daily_return_pct
    if dr == dr and dist == dist and abs(dr) <= 200:  # NaN/잡음 가드
        if dr >= SIDEWAYS_PEAK_DAILY_RETURN_PCT and dist >= SIDEWAYS_PEAK_DIST_FROM_HIGH_PCT:
            score += WEIGHT_SIDEWAYS_PEAK  # -1.5
            reasons.append(
                f"{WEIGHT_SIDEWAYS_PEAK:+.1f} 횡보고점 ret={dr:+.1f}% dist={dist:+.1f}% (R14l)"
            )

    # 진입 필수조건 (등급과 별도)
    required = _check_required(snap)

    return ScoreCard(
        score=score,
        grade=_grade_for(score),
        reasons=reasons,
        required_checks=required,
    )


def _check_required(snap: GraderSnapshot) -> dict[str, bool]:
    """진입 필수조건 (AND).

    None / NaN 은 unknown — 보수적으로 False 처리.
    """
    checks: dict[str, bool] = {}

    checks["회전율↑"] = (
        snap.volume_turnover_rank is not None
        and snap.volume_turnover_rank <= VOLUME_TURNOVER_TOP_N
    )

    checks["VP>110+5MA>100"] = is_vp_strong(snap.vp, snap.vp_5ma)

    a5 = snap.vol_accel_5m
    a1 = snap.vol_accel_1m
    checks["가속 5m+1m"] = (
        a5 == a5 and a1 == a1
        and a5 > VOL_ACCEL_5M_STRONG and a1 > VOL_ACCEL_1M_STRONG
    )

    checks["장대양봉"] = (
        snap.candle is not None and is_clean_bullish(snap.candle)
    )

    dist = snap.dist_from_intraday_high_pct
    checks["고점-2%이내"] = (
        dist == dist and dist >= DIST_FROM_HIGH_MAX_PCT
    )

    return checks
