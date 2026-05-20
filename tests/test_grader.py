"""src.scalping.score.grader (R14) 단위 테스트.

가장 중요한 회귀 케이스:
    - 흥아해운: 모멘텀 죽음 + 호가만 5.3배 → 🔴 AVOID
    - 제룡전기: 상한가 모멘텀 + VP 142 + 장대양봉 → 🟢 STRONG

기타 경계/누락 입력 테스트.

`docs/jongbae-strategy.md` R14 참조.
"""
from __future__ import annotations

import datetime as dt

from src.scalping.score.candle import classify_candle
from src.scalping.score.divergence import compute_divergence
from src.scalping.score.grader import (
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


# ── R14a VWAP 위치 (round 23, P0-1) ─────────────────────────────────────────


def test_vwap_above_threshold_adds_one():
    """가격이 VWAP 대비 +0.3% 이상 위 → +1 점."""
    snap = GraderSnapshot(price_vs_vwap_pct=0.5)
    card = calculate_buy_score(snap)
    assert card.score == 1.0
    assert any("VWAP" in r and "위" in r for r in card.reasons)


def test_vwap_below_threshold_subtracts_one():
    """가격이 VWAP 대비 -0.3% 이하 아래 → -1 점."""
    snap = GraderSnapshot(price_vs_vwap_pct=-0.5)
    card = calculate_buy_score(snap)
    assert card.score == -1.0
    assert any("VWAP" in r and "아래" in r for r in card.reasons)


def test_vwap_boundary_above_exact():
    """+0.3% 정확히 → 가산 (≥ 조건)."""
    snap = GraderSnapshot(price_vs_vwap_pct=0.3)
    card = calculate_buy_score(snap)
    assert card.score == 1.0


def test_vwap_boundary_below_exact():
    """-0.3% 정확히 → 감산 (≤ 조건)."""
    snap = GraderSnapshot(price_vs_vwap_pct=-0.3)
    card = calculate_buy_score(snap)
    assert card.score == -1.0


def test_vwap_neutral_zone_no_change():
    """-0.3% < x < +0.3% → 가/감산 없음 (호가 노이즈 컷오프)."""
    snap = GraderSnapshot(price_vs_vwap_pct=0.1)
    card = calculate_buy_score(snap)
    assert card.score == 0.0
    assert not any("VWAP" in r for r in card.reasons)


def test_vwap_nan_no_change():
    """NaN → 가/감산 없음 (호출자 데이터 부족 시 안전)."""
    snap = GraderSnapshot()  # price_vs_vwap_pct defaults to NaN
    card = calculate_buy_score(snap)
    assert card.score == 0.0


def test_vwap_compounds_with_jeryung_strong():
    """제룡전기 + VWAP 위 → 점수 더 강해짐. 회귀 보강."""
    candle = classify_candle(o=70000, h=91500, l=69800, c=91300)
    snap = GraderSnapshot(
        volume_turnover_rank=2,
        vol_accel_1m=2.4,
        vol_accel_5m=1.6,
        candle=candle,
        vp=142.0,
        vp_5ma=128.0,
        bid_ask_ratio=7.1,
        dist_from_intraday_high_pct=-0.2,
        price_vs_vwap_pct=2.5,  # 상한가 부근에서 VWAP 위 강하게
    )
    card = calculate_buy_score(snap)
    assert card.grade == "STRONG"
    assert any("VWAP" in r and "위" in r for r in card.reasons)


def test_vwap_compounds_with_heungahaeun_avoid():
    """흥아해운 + VWAP 아래 → AVOID 더 강해짐. 회귀 보강."""
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
        price_vs_vwap_pct=-1.2,  # 모멘텀 죽고 VWAP 아래
    )
    card = calculate_buy_score(snap)
    assert card.grade == "AVOID"
    assert any("VWAP" in r and "아래" in r for r in card.reasons)


# ── R14b 5/20분 이평 위치 (round 24, P0-2) ──────────────────────────────────


def test_ma_alignment_bullish_adds_one():
    """가격이 MA5/MA20 둘 다 위 (정배열) → +1."""
    snap = GraderSnapshot(price_vs_ma5_pct=0.5, price_vs_ma20_pct=0.8)
    card = calculate_buy_score(snap)
    assert card.score == 1.0
    assert any("정배열" in r for r in card.reasons)


def test_ma_alignment_bearish_subtracts_one():
    """가격이 MA5/MA20 둘 다 아래 (역배열) → -1."""
    snap = GraderSnapshot(price_vs_ma5_pct=-0.5, price_vs_ma20_pct=-1.0)
    card = calculate_buy_score(snap)
    assert card.score == -1.0
    assert any("역배열" in r for r in card.reasons)


def test_ma_alignment_mixed_no_change():
    """가격 > MA5 이지만 < MA20 → 가/감산 없음 (혼합/추세 불명)."""
    snap = GraderSnapshot(price_vs_ma5_pct=0.5, price_vs_ma20_pct=-0.5)
    card = calculate_buy_score(snap)
    assert card.score == 0.0
    assert not any("배열" in r for r in card.reasons)


def test_ma_alignment_boundary_exact():
    """+0.3% / +0.3% 정확히 → 가산 (≥ 조건)."""
    snap = GraderSnapshot(price_vs_ma5_pct=0.3, price_vs_ma20_pct=0.3)
    card = calculate_buy_score(snap)
    assert card.score == 1.0


def test_ma_alignment_neutral_zone():
    """±0.3% 사이 → 가/감산 없음 (호가 노이즈 컷)."""
    snap = GraderSnapshot(price_vs_ma5_pct=0.1, price_vs_ma20_pct=0.1)
    card = calculate_buy_score(snap)
    assert card.score == 0.0


def test_ma_alignment_nan_one_field_no_change():
    """MA20 NaN → 정/역배열 판정 불가, 가/감산 없음."""
    snap = GraderSnapshot(price_vs_ma5_pct=1.0)  # ma20 NaN
    card = calculate_buy_score(snap)
    assert card.score == 0.0


def test_ma_alignment_compounds_with_jeryung():
    """제룡전기 + VWAP 위 + 정배열 → 점수 더 강."""
    candle = classify_candle(o=70000, h=91500, l=69800, c=91300)
    snap = GraderSnapshot(
        volume_turnover_rank=2,
        vol_accel_1m=2.4,
        vol_accel_5m=1.6,
        candle=candle,
        vp=142.0,
        vp_5ma=128.0,
        bid_ask_ratio=7.1,
        dist_from_intraday_high_pct=-0.2,
        price_vs_vwap_pct=2.5,
        price_vs_ma5_pct=1.8,
        price_vs_ma20_pct=3.2,
    )
    card = calculate_buy_score(snap)
    assert card.grade == "STRONG"
    assert any("정배열" in r for r in card.reasons)


def test_ma_alignment_compounds_with_heungahaeun():
    """흥아해운 + VWAP 아래 + 역배열 → 더 강한 AVOID."""
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
        price_vs_vwap_pct=-1.2,
        price_vs_ma5_pct=-0.8,
        price_vs_ma20_pct=-1.5,
    )
    card = calculate_buy_score(snap)
    assert card.grade == "AVOID"
    assert any("역배열" in r for r in card.reasons)


# ── R14c 상한가 진입 시간 가산 (round 25, P1-1) ─────────────────────────────


def test_limit_up_early_before_0930_adds_one():
    """09:25 진입 → +1."""
    snap = GraderSnapshot(limit_up_hit_time=dt.time(9, 25))
    card = calculate_buy_score(snap)
    assert card.score == 1.0
    assert any("조기진입" in r for r in card.reasons)


def test_limit_up_mid_between_0930_and_1030_adds_half():
    """10:00 진입 → +0.5."""
    snap = GraderSnapshot(limit_up_hit_time=dt.time(10, 0))
    card = calculate_buy_score(snap)
    assert card.score == 0.5
    assert any("상한가 진입" in r and "조기" not in r for r in card.reasons)


def test_limit_up_boundary_0930_falls_to_mid():
    """09:30 정확히 → +0.5 (early 는 strict <)."""
    snap = GraderSnapshot(limit_up_hit_time=dt.time(9, 30))
    card = calculate_buy_score(snap)
    assert card.score == 0.5


def test_limit_up_boundary_1030_no_gain():
    """10:30 정확히 → 0 (mid 도 strict <)."""
    snap = GraderSnapshot(limit_up_hit_time=dt.time(10, 30))
    card = calculate_buy_score(snap)
    assert card.score == 0.0


def test_limit_up_late_no_gain():
    """11:00 진입 → 0 (자금 식음 시간대)."""
    snap = GraderSnapshot(limit_up_hit_time=dt.time(11, 0))
    card = calculate_buy_score(snap)
    assert card.score == 0.0


def test_limit_up_none_no_gain():
    """상한가 미도달 → None → 무가산."""
    snap = GraderSnapshot()
    card = calculate_buy_score(snap)
    assert card.score == 0.0
    assert not any("상한가" in r for r in card.reasons)


# ── R13 다이버전스 강등 (round 27, P2-1) ─────────────────────────────────────


def test_bearish_divergence_subtracts_one_not_two():
    """round 27: 통설 외 약신호라 ±2 → ±1 강등."""
    div = compute_divergence(price_now=110, price_5m_ago=100, vp_5ma_now=90, vp_5ma_5m_ago=100)
    assert div.bearish is True
    snap = GraderSnapshot(divergence=div)
    card = calculate_buy_score(snap)
    assert card.score == -1.0
    assert any("Bearish Divergence" in r for r in card.reasons)
    assert any("-1" in r and "Bearish" in r for r in card.reasons)


def test_bullish_divergence_adds_one_not_two():
    div = compute_divergence(price_now=90, price_5m_ago=100, vp_5ma_now=110, vp_5ma_5m_ago=100)
    assert div.bullish is True
    snap = GraderSnapshot(divergence=div)
    card = calculate_buy_score(snap)
    assert card.score == 1.0
    assert any("Bullish Divergence" in r for r in card.reasons)
    assert any("+1" in r and "Bullish" in r for r in card.reasons)


# ── R14d 거래량 비율 검증 (round 28, P2-2) ──────────────────────────────────


def test_volume_ratio_normal_adds_half():
    """전일 대비 200% → +0.5 (정상 매집)."""
    snap = GraderSnapshot(volume_ratio_vs_prev_day=2.0)
    card = calculate_buy_score(snap)
    assert card.score == 0.5
    assert any("거래량" in r and "정상" in r for r in card.reasons)


def test_volume_ratio_excessive_subtracts_one():
    """전일 대비 15배 → -1 (과열, 강한 상한가 X)."""
    snap = GraderSnapshot(volume_ratio_vs_prev_day=15.0)
    card = calculate_buy_score(snap)
    assert card.score == -1.0
    assert any("거래량" in r and "과열" in r for r in card.reasons)


def test_volume_ratio_boundary_low_exact():
    """전일 대비 정확히 100% → +0.5 (≥ 조건)."""
    snap = GraderSnapshot(volume_ratio_vs_prev_day=1.0)
    card = calculate_buy_score(snap)
    assert card.score == 0.5


def test_volume_ratio_boundary_high_exact():
    """전일 대비 정확히 300% → +0.5 (≤ 조건)."""
    snap = GraderSnapshot(volume_ratio_vs_prev_day=3.0)
    card = calculate_buy_score(snap)
    assert card.score == 0.5


def test_volume_ratio_boundary_excessive_exact():
    """정확히 10배 → -1 (≥ 조건)."""
    snap = GraderSnapshot(volume_ratio_vs_prev_day=10.0)
    card = calculate_buy_score(snap)
    assert card.score == -1.0


def test_volume_ratio_between_3_and_10_no_change():
    """3~10배 사이 → 가/감산 없음 (강한 상승이지만 통설 안전구간 외)."""
    snap = GraderSnapshot(volume_ratio_vs_prev_day=5.0)
    card = calculate_buy_score(snap)
    assert card.score == 0.0


def test_volume_ratio_below_1_no_change():
    """전일보다 적음 → 가/감산 없음 (통설에서 명시 X)."""
    snap = GraderSnapshot(volume_ratio_vs_prev_day=0.5)
    card = calculate_buy_score(snap)
    assert card.score == 0.0


def test_volume_ratio_nan_no_change():
    snap = GraderSnapshot()  # NaN default
    card = calculate_buy_score(snap)
    assert card.score == 0.0


# ── ritual 3 가드레일: 통설 가중치 합 ≥ 비통설 (round 31) ────────────────────
#
# docs/plan.md "R14/R15 가중치 검증 ritual" 참조.
# 가중치 변경 PR 마다 통설 우위 invariant 가 깨지지 않는지 자동 검증.
#
# 통설: R3(회전율) / R10(VP) / R11(가속) / R12(봉) / R14a~d(VWAP/MA/시간/거래량)
# 비통설: R13(다이버전스) — 한국 단타 통설 검색에서 거의 안 나옴
#
# 가드레일: 통설 양/음수 합산이 비통설의 2배 이상. R13 가중치를 통설 합산의
# 50% 이상으로 키우면 테스트 깨짐 → 의식적 결정 강제.


def test_invariant_consensus_weights_dominate_positive():
    """통설 가산 최대 케이스 vs 비통설(Bullish Div) 단일 — 2배 이상."""
    consensus_only = GraderSnapshot(
        volume_turnover_rank=1,
        vol_accel_5m=2.5, vol_accel_1m=2.5,
        candle=classify_candle(100, 110, 99, 109),
        vp=120, vp_5ma=110,
        bid_ask_ratio=5.0,
        dist_from_intraday_high_pct=-0.5,
        price_vs_vwap_pct=1.0,
        price_vs_ma5_pct=1.0, price_vs_ma20_pct=1.0,
        limit_up_hit_time=dt.time(9, 15),
        volume_ratio_vs_prev_day=2.0,
    )
    div_bull = compute_divergence(
        price_now=90, price_5m_ago=100, vp_5ma_now=110, vp_5ma_5m_ago=100,
    )
    non_consensus_only = GraderSnapshot(divergence=div_bull)

    cs = calculate_buy_score(consensus_only).score
    ns = calculate_buy_score(non_consensus_only).score

    assert cs >= 2.0 * ns, (
        f"통설 양수 합 {cs} 가 비통설 {ns} 의 2배 미만. "
        f"R13 가중치를 너무 키웠는지 가중치 변경 PR 점검."
    )


def test_invariant_consensus_penalties_dominate_negative():
    """통설 페널티 최대 케이스 vs 비통설(Bearish Div) — 2배 이상 (음수)."""
    weak = classify_candle(100, 115, 90, 92)
    consensus_neg = GraderSnapshot(
        vol_accel_5m=0.5, vol_accel_1m=0.3,
        candle=weak,
        vp=80,
        price_vs_vwap_pct=-1.0,
        price_vs_ma5_pct=-1.0, price_vs_ma20_pct=-1.0,
        volume_ratio_vs_prev_day=15.0,
    )
    div_bear = compute_divergence(
        price_now=110, price_5m_ago=100, vp_5ma_now=90, vp_5ma_5m_ago=100,
    )
    non_consensus_neg = GraderSnapshot(divergence=div_bear)

    cs = calculate_buy_score(consensus_neg).score
    ns = calculate_buy_score(non_consensus_neg).score

    assert cs <= 2.0 * ns, (
        f"통설 음수 합 {cs} 가 비통설 {ns} 의 2배 미달 (절댓값). "
        f"R13 페널티 가중치를 너무 키웠는지 점검."
    )


def test_invariant_divergence_weight_capped_at_one():
    """다이버전스 단일 ±1 — 가중치 강등 유지 검증 (round 27)."""
    div_bull = compute_divergence(
        price_now=90, price_5m_ago=100, vp_5ma_now=110, vp_5ma_5m_ago=100,
    )
    div_bear = compute_divergence(
        price_now=110, price_5m_ago=100, vp_5ma_now=90, vp_5ma_5m_ago=100,
    )
    assert abs(calculate_buy_score(GraderSnapshot(divergence=div_bull)).score) <= 1.0
    assert abs(calculate_buy_score(GraderSnapshot(divergence=div_bear)).score) <= 1.0


def test_limit_up_compounds_with_jeryung_strong():
    """제룡전기 + 09:15 조기 상한가 → 점수 폭증."""
    candle = classify_candle(o=70000, h=91500, l=69800, c=91300)
    snap = GraderSnapshot(
        volume_turnover_rank=2,
        vol_accel_1m=2.4,
        vol_accel_5m=1.6,
        candle=candle,
        vp=142.0,
        vp_5ma=128.0,
        bid_ask_ratio=7.1,
        dist_from_intraday_high_pct=-0.2,
        price_vs_vwap_pct=2.5,
        price_vs_ma5_pct=1.8,
        price_vs_ma20_pct=3.2,
        limit_up_hit_time=dt.time(9, 15),
    )
    card = calculate_buy_score(snap)
    assert card.grade == "STRONG"
    assert any("조기진입" in r for r in card.reasons)
