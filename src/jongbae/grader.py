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

from dataclasses import dataclass, field
from typing import Literal

from src.jongbae.candle import (
    CandleShape,
    is_clean_bullish,
    is_weak_candle,
)
from src.jongbae.config_thresholds import (
    BID_ASK_RATIO_THRESHOLD,
    DIST_FROM_HIGH_MAX_PCT,
    GRADE_NEUTRAL,
    GRADE_STRONG,
    GRADE_WATCH,
    VOL_ACCEL_1M_DRAIN,
    VOL_ACCEL_1M_STRONG,
    VOL_ACCEL_1M_VERY_STRONG,
    VOL_ACCEL_1M_WEAK,
    VOL_ACCEL_5M_STRONG,
    VOL_ACCEL_5M_WEAK,
    VOLUME_TURNOVER_TOP_N,
    VP_BALANCED,
)
from src.jongbae.divergence import DivergenceState
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
    """R14 매수 점수 + 등급. `docs/jongbae-strategy.md` R14 의 score 공식 그대로."""
    score = 0.0
    reasons: list[str] = []

    # 거래대금 회전율 순위 (+1)
    if snap.volume_turnover_rank is not None and snap.volume_turnover_rank <= VOLUME_TURNOVER_TOP_N:
        score += 1
        reasons.append(f"+1 거래대금 {VOLUME_TURNOVER_TOP_N}위내")

    # R11 가속 — 동반 가속/동반 감속
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

    # R12 봉 패턴
    if snap.candle is not None:
        if is_clean_bullish(snap.candle):
            score += 2
            reasons.append(f"+2 장대양봉 (윗꼬리 {snap.candle.upper_wick*100:.0f}%)")
        if is_weak_candle(snap.candle):
            score -= 2
            reasons.append(
                f"-2 약한 봉 ({snap.candle.type} / 윗꼬리 {snap.candle.upper_wick*100:.0f}%)"
            )

    # R10 체결강도
    if is_vp_strong(snap.vp, snap.vp_5ma):
        score += 2
        reasons.append(f"+2 VP {snap.vp:.0f} 5MA {snap.vp_5ma:.0f}")
    if is_vp_weak(snap.vp):
        score -= 2
        reasons.append(f"-2 VP<{VP_BALANCED:.0f} ({snap.vp:.0f})")

    # 가속 단일 (R11 추가)
    if a1_ok and a1 > VOL_ACCEL_1M_VERY_STRONG:
        score += 1
        reasons.append(f"+1 vol_accel_1m {a1:.1f}배")
    if a1_ok and a1 < VOL_ACCEL_1M_DRAIN:
        score -= 1
        reasons.append(f"-1 자금 고갈 (1m {a1:.1f})")

    # R13 다이버전스
    if snap.divergence is not None:
        if snap.divergence.bearish:
            score -= 2
            reasons.append("-2 Bearish Divergence")
        if snap.divergence.bullish:
            score += 2
            reasons.append("+2 Bullish Divergence")

    # 호가잔량 (강등된 보조 가중)
    if snap.bid_ask_ratio == snap.bid_ask_ratio and snap.bid_ask_ratio > BID_ASK_RATIO_THRESHOLD:
        score += 0.5
        reasons.append(f"+0.5 호가 {snap.bid_ask_ratio:.1f}배 (보조)")

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
