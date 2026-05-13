"""src.jongbae.grader (R14) 단위 테스트.

가장 중요한 회귀 케이스:
    - 흥아해운: 모멘텀 죽음 + 호가만 5.3배 → 🔴 AVOID
    - 제룡전기: 상한가 모멘텀 + VP 142 + 장대양봉 → 🟢 STRONG

기타 경계/누락 입력 테스트.

`docs/jongbae-strategy.md` R14 참조.
"""
from __future__ import annotations

from src.jongbae.candle import classify_candle
from src.jongbae.divergence import compute_divergence
from src.jongbae.grader import (
    GraderSnapshot,
    calculate_buy_score,
)


# ── 회귀: 흥아해운 (요구사항 문서 검증 케이스) ────────────────────────────────


def test_regression_heungahaeun_avoid():
    """흥아해운 시나리오 — 점수 ≤ -3, 등급 🔴 AVOID 이어야 함.

    입력:
        - 거래대금 1316억 (1위, 회전율 +19.4%)
        - vol_accel_5m = 0.8, vol_accel_1m = 0.4
        - 호가 매수/매도 5.3배
        - 윗꼬리 큰 음봉 (가정: 윗꼬리 52% 음봉)
        - VP = 95, VP_5MA = 98
        - 당일 고점 -2.3%
    """
    # 윗꼬리 ~ 52% 음봉
    candle = classify_candle(o=2850, h=2880, l=2820, c=2825)
    # upper_wick = (2880-2850)/60 = 0.5 → R12 -2 / R15 C4 boundary
    # 더 확실한 음봉으로:
    candle = classify_candle(o=2860, h=2900, l=2820, c=2825)
    # total=80, upper=(2900-2860)/80 = 0.5, type=bearish

    snap = GraderSnapshot(
        volume_turnover_rank=1,
        vol_accel_1m=0.4,
        vol_accel_5m=0.8,
        candle=candle,
        vp=95.0,
        vp_5ma=98.0,
        divergence=None,
        bid_ask_ratio=5.3,
        dist_from_intraday_high_pct=-2.3,
    )
    card = calculate_buy_score(snap)

    assert card.score <= -3.0, (
        f"흥아해운 회귀 실패: 점수 {card.score} > -3. 사유: {card.reasons}"
    )
    assert card.grade == "AVOID", f"등급 {card.grade}, expected AVOID"
    assert "+1 거래대금" in " ".join(card.reasons)   # 1위
    assert any("가속 죽음" in r for r in card.reasons)
    assert any("VP" in r for r in card.reasons)


def test_regression_heungahaeun_with_bearish_divergence():
    """동일 시나리오 + Bearish Divergence 추가 — 더 강한 AVOID."""
    candle = classify_candle(o=2860, h=2900, l=2820, c=2825)
    div = compute_divergence(price_now=2825, price_5m_ago=2810, vp_5ma_now=98, vp_5ma_5m_ago=104)
    assert div.bearish is True

    snap = GraderSnapshot(
        volume_turnover_rank=1,
        vol_accel_1m=0.4,
        vol_accel_5m=0.8,
        candle=candle,
        vp=95.0,
        vp_5ma=98.0,
        divergence=div,
        bid_ask_ratio=5.3,
        dist_from_intraday_high_pct=-2.3,
    )
    card = calculate_buy_score(snap)
    assert card.grade == "AVOID"
    assert card.score <= -5.0
    assert any("Bearish" in r for r in card.reasons)


def test_regression_heungahaeun_fails_required():
    """흥아해운 — 필수조건 통과 불가 (VP < 110, 가속 죽음 등)."""
    candle = classify_candle(o=2860, h=2900, l=2820, c=2825)
    snap = GraderSnapshot(
        volume_turnover_rank=1,
        vol_accel_1m=0.4,
        vol_accel_5m=0.8,
        candle=candle,
        vp=95.0,
        vp_5ma=98.0,
        bid_ask_ratio=5.3,
        dist_from_intraday_high_pct=-2.3,
    )
    card = calculate_buy_score(snap)
    assert card.passes_required is False
    assert card.required_checks["VP>110+5MA>100"] is False
    assert card.required_checks["가속 5m+1m"] is False
    assert card.required_checks["장대양봉"] is False


# ── 회귀: 제룡전기 (STRONG 케이스) ────────────────────────────────────────────


def test_regression_jeryung_strong():
    """제룡전기 상한가 모멘텀 — 점수 ≥ 5, 등급 🟢 STRONG."""
    # 장대양봉 (윗꼬리 ~ 5%)
    candle = classify_candle(o=70000, h=91500, l=69800, c=91300)
    snap = GraderSnapshot(
        volume_turnover_rank=2,
        vol_accel_1m=2.4,
        vol_accel_5m=1.6,
        candle=candle,
        vp=142.0,
        vp_5ma=128.0,
        divergence=None,
        bid_ask_ratio=7.1,
        dist_from_intraday_high_pct=-0.2,
    )
    card = calculate_buy_score(snap)

    assert card.score >= 5.0, (
        f"제룡전기 회귀 실패: 점수 {card.score} < 5. 사유: {card.reasons}"
    )
    assert card.grade == "STRONG", f"등급 {card.grade}, expected STRONG"
    assert card.passes_required is True


# ── 등급 경계 ────────────────────────────────────────────────────────────────


def test_grade_strong_at_5():
    snap = GraderSnapshot(
        volume_turnover_rank=1,                      # +1
        vol_accel_5m=1.5, vol_accel_1m=1.5,          # +2 (동반)
        candle=classify_candle(100, 110, 99, 109),   # +2
        vp=120, vp_5ma=110,                          # +2
    )  # total = 7
    card = calculate_buy_score(snap)
    assert card.grade == "STRONG"


def test_grade_neutral_zero_score():
    """입력 없음 → 점수 0 → NEUTRAL."""
    snap = GraderSnapshot()
    card = calculate_buy_score(snap)
    assert card.score == 0
    assert card.grade == "NEUTRAL"


def test_grade_avoid_below_minus_1():
    snap = GraderSnapshot(
        vol_accel_5m=0.5, vol_accel_1m=0.3,          # -3 + -1 = -4
        vp=80.0,                                      # -2
    )  # total = -6
    card = calculate_buy_score(snap)
    assert card.grade == "AVOID"


# ── 필수조건 ────────────────────────────────────────────────────────────────


def test_required_dist_from_high_boundary():
    """당일 고점 -2.0% 정확히 → 통과."""
    snap = GraderSnapshot(
        volume_turnover_rank=1,
        vol_accel_5m=1.5, vol_accel_1m=1.5,
        candle=classify_candle(100, 110, 99, 109),
        vp=120, vp_5ma=110,
        dist_from_intraday_high_pct=-2.0,
    )
    card = calculate_buy_score(snap)
    assert card.required_checks["고점-2%이내"] is True


def test_required_dist_from_high_fail():
    snap = GraderSnapshot(
        volume_turnover_rank=1,
        vol_accel_5m=1.5, vol_accel_1m=1.5,
        candle=classify_candle(100, 110, 99, 109),
        vp=120, vp_5ma=110,
        dist_from_intraday_high_pct=-3.0,            # 추격구간
    )
    card = calculate_buy_score(snap)
    assert card.required_checks["고점-2%이내"] is False
    assert card.passes_required is False


# ── 경계 / 누락 입력 ─────────────────────────────────────────────────────────


def test_missing_inputs_no_crash():
    """모든 입력 None/NaN — score 0, 모든 필수조건 False."""
    snap = GraderSnapshot()
    card = calculate_buy_score(snap)
    assert card.score == 0
    assert card.passes_required is False


def test_bid_ask_ratio_only_gives_half_point():
    """호가 잔량 강등 검증 — 5.3배라도 +0.5 만 가산."""
    snap = GraderSnapshot(bid_ask_ratio=5.3)
    card = calculate_buy_score(snap)
    assert card.score == 0.5
    assert card.grade == "NEUTRAL"
