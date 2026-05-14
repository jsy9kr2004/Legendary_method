"""src.jongbae.jongbae_exit (종배 청산 시초가 룰, round 30, P3-2) 단위 테스트.

검증 기반: 통설 (WikiDocs 종가베팅, brokdam 광전자 사례).
    ≤ +1% (또는 마이너스) → 전량 매도 (갭 미발생)
    +1% ~ +6%              → 전량 익절 (정상 갭)
    ≥ +6%                  → 분할 익절 40% (강한 갭, 관망)
"""
from __future__ import annotations

import pytest

from src.jongbae.jongbae_exit import (
    JongbaeExitDecision,
    evaluate_jongbae_open_exit,
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
