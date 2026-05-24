"""src.overnight.exit (종배 청산 시초가 룰, round 30, P3-2) 단위 테스트.

검증 기반: 통설 (WikiDocs 종가베팅, brokdam 광전자 사례).
    ≤ +1% (또는 마이너스) → 전량 매도 (갭 미발생)
    +1% ~ +6%              → 전량 익절 (정상 갭)
    ≥ +6%                  → 분할 익절 40% (강한 갭, 관망)
"""
from __future__ import annotations

import pytest

from src.overnight.exit import (
    JongbaeExitDecision,
    evaluate_jongbae_open_exit,
    evaluate_overnight_exit_live,
    format_overnight_exit_line,
)


# ── 갭 미발생 (sell_all, 갭 ≤ +1%) ─────────────────────────────────────────


def test_open_below_close_sells_all():
    """시초 < 전일종가 → 전량 매도 (실패한 종배)."""
    d = evaluate_jongbae_open_exit(open_price=98_000, prev_close=100_000)
    assert d.action == "sell_all"
    assert d.partial_ratio == 1.0
    assert d.open_gap_pct == pytest.approx(-2.0)
    assert "갭 미발생" in d.reason


def test_open_flat_sells_all():
    """시초 = 전일종가 (0% 갭) → 전량."""
    d = evaluate_jongbae_open_exit(open_price=100_000, prev_close=100_000)
    assert d.action == "sell_all"
    assert d.open_gap_pct == pytest.approx(0.0)


def test_open_boundary_plus_one_pct_sells_all():
    """시초 +1% 정확히 → 전량 (≤ 조건, 갭 미발생 영역에 포함)."""
    d = evaluate_jongbae_open_exit(open_price=101_000, prev_close=100_000)
    assert d.action == "sell_all"
    assert d.open_gap_pct == pytest.approx(1.0)
    assert "갭 미발생" in d.reason


# ── 정상 갭 (sell_all 익절, +1% < 갭 < +6%) ───────────────────────────────


def test_normal_gap_sells_all_with_profit_reason():
    """시초 +3% → 전량 익절 (정상 갭)."""
    d = evaluate_jongbae_open_exit(open_price=103_000, prev_close=100_000)
    assert d.action == "sell_all"
    assert d.partial_ratio == 1.0
    assert d.open_gap_pct == pytest.approx(3.0)
    assert "정상 갭" in d.reason


def test_normal_gap_just_above_one_pct():
    """시초 +1.01% → 전량 익절 (1% 경계 바로 위)."""
    d = evaluate_jongbae_open_exit(open_price=101_010, prev_close=100_000)
    assert d.action == "sell_all"
    assert "정상 갭" in d.reason


def test_normal_gap_just_below_six_pct():
    """시초 +5.99% → 전량 익절 (6% 경계 바로 아래)."""
    d = evaluate_jongbae_open_exit(open_price=105_990, prev_close=100_000)
    assert d.action == "sell_all"
    assert "정상 갭" in d.reason


# ── 강한 갭 (sell_partial 40%, 갭 ≥ +6%) ──────────────────────────────────


def test_strong_gap_sells_partial_40_percent():
    """시초 +8% → 40% 분할 익절, 60% 관망."""
    d = evaluate_jongbae_open_exit(open_price=108_000, prev_close=100_000)
    assert d.action == "sell_partial"
    assert d.partial_ratio == pytest.approx(0.4)
    assert d.open_gap_pct == pytest.approx(8.0)
    assert "강한 갭" in d.reason
    assert "40%" in d.reason


def test_strong_gap_boundary_six_pct_sells_partial():
    """시초 +6% 정확히 → 분할 (≥ 조건)."""
    d = evaluate_jongbae_open_exit(open_price=106_000, prev_close=100_000)
    assert d.action == "sell_partial"
    assert d.partial_ratio == pytest.approx(0.4)


def test_strong_gap_large_still_partial():
    """시초 +15% → 여전히 40% 분할 (분할 비중 동일, 추가 슈팅 노림)."""
    d = evaluate_jongbae_open_exit(open_price=115_000, prev_close=100_000)
    assert d.action == "sell_partial"
    assert d.partial_ratio == pytest.approx(0.4)


# ── 가드 ───────────────────────────────────────────────────────────────────


def test_zero_open_raises():
    with pytest.raises(ValueError):
        evaluate_jongbae_open_exit(open_price=0, prev_close=100_000)


def test_zero_prev_close_raises():
    with pytest.raises(ValueError):
        evaluate_jongbae_open_exit(open_price=100_000, prev_close=0)


def test_negative_raises():
    with pytest.raises(ValueError):
        evaluate_jongbae_open_exit(open_price=-100, prev_close=100_000)


def test_decision_is_frozen():
    """JongbaeExitDecision 은 immutable — 실수로 변경 X."""
    d = evaluate_jongbae_open_exit(open_price=110_000, prev_close=100_000)
    with pytest.raises((AttributeError, Exception)):
        d.action = "sell_all"  # type: ignore[misc]


# ── 라이브 청산 (evaluate_overnight_exit_live, 2026-05-25) ───────────────────


def test_live_strong_gap_still_high_partial():
    """현재 +8% (고점 근처) → 강한 갭, 분할 (시초 룰과 동일 임계값)."""
    ctx = evaluate_overnight_exit_live(
        prev_close=100_000, current=108_000, intraday_high=108_500, open_price=107_000,
    )
    assert ctx.decision.action == "sell_partial"
    assert ctx.current_gap_pct == pytest.approx(8.0)
    assert ctx.open_gap_pct == pytest.approx(7.0)
    assert ctx.pullback_from_high_pct < 0  # 고점 대비 되돌림


def test_live_captures_fade_from_strong_open():
    """★ 시초 +7%(분할 영역)였다가 현재 +2%로 fade → 현재가 기준 전량 매도.

    이게 라이브 엔진의 핵심 — 시초 1회 룰이라면 분할(60% 관망)로 물렸을 상황을,
    현재가 재평가로 '정상 갭 전량 익절' 로 전환해 fade 를 포착.
    """
    ctx = evaluate_overnight_exit_live(
        prev_close=100_000, current=102_000, intraday_high=107_000, open_price=107_000,
    )
    assert ctx.decision.action == "sell_all"
    assert ctx.current_gap_pct == pytest.approx(2.0)
    # 고점(107k) 대비 현재(102k) 되돌림 ≈ -4.67%
    assert ctx.pullback_from_high_pct == pytest.approx(-4.67, abs=0.1)


def test_live_gap_collapsed_sells_all():
    """현재 ≤ +1% (갭 소멸) → 전량 매도."""
    ctx = evaluate_overnight_exit_live(
        prev_close=100_000, current=100_500, intraday_high=105_000,
    )
    assert ctx.decision.action == "sell_all"
    assert "고점대비" in ctx.note


def test_live_open_optional_no_open_gap():
    """open 미상이면 open_gap NaN, note 에 시초 줄 생략."""
    ctx = evaluate_overnight_exit_live(
        prev_close=100_000, current=104_000, intraday_high=104_000,
    )
    assert ctx.open_gap_pct != ctx.open_gap_pct  # NaN
    assert "시초" not in ctx.note
    assert ctx.current_gap_pct == pytest.approx(4.0)


def test_live_high_below_current_clamped():
    """일중고가가 현재가보다 작게 들어오면 현재가로 보정 (되돌림 0)."""
    ctx = evaluate_overnight_exit_live(
        prev_close=100_000, current=106_000, intraday_high=105_000,
    )
    assert ctx.pullback_from_high_pct == pytest.approx(0.0)


def test_live_raises_on_nonpositive():
    with pytest.raises(ValueError):
        evaluate_overnight_exit_live(prev_close=0, current=100_000, intraday_high=100_000)
    with pytest.raises(ValueError):
        evaluate_overnight_exit_live(prev_close=100_000, current=0, intraday_high=100_000)


def test_format_overnight_exit_line():
    ctx = evaluate_overnight_exit_live(
        prev_close=100_000, current=108_000, intraday_high=108_500, open_price=107_000,
    )
    line = format_overnight_exit_line("제룡전기", "033100", ctx)
    assert "제룡전기(033100)" in line
    assert "매도 40%" in line  # 분할 비중
    assert "고점대비" in line
