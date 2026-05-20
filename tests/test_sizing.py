"""src.overnight.sizing 테스트."""
from __future__ import annotations

import math

import pytest

from src.overnight.sizing import (
    KELLY_MAX_FRACTION,
    _kelly_sample_factor,
    compute_sizing,
    equal_weights,
    kelly_fraction,
    sharpe_score,
)


# ── 표본 보정 계수 ────────────────────────────────────────────────────────────

def test_sample_factor_n4_excluded():
    assert _kelly_sample_factor(4) is None


def test_sample_factor_n5_to_9():
    assert _kelly_sample_factor(5) == 0.3
    assert _kelly_sample_factor(9) == 0.3


def test_sample_factor_n10_to_19():
    assert _kelly_sample_factor(10) == 0.6
    assert _kelly_sample_factor(19) == 0.6


def test_sample_factor_n20_plus():
    assert _kelly_sample_factor(20) == 0.8
    assert _kelly_sample_factor(100) == 0.8


# ── Kelly ────────────────────────────────────────────────────────────────────

def test_kelly_fraction_normal():
    """p=0.7, W=3, L=2, n=20 → raw = 0.7/2 - 0.3/3 = 0.35 - 0.1 = 0.25.
    × 0.8 = 0.20."""
    stats = {"n": 20, "p": 0.7, "avg_gap_when_up": 3.0, "avg_gap_when_dn": 2.0}
    f = kelly_fraction(stats)
    assert f == pytest.approx(0.20, abs=0.001)


def test_kelly_fraction_caps_at_25_pct():
    """비현실적으로 좋은 베팅이라도 25% 캡."""
    stats = {"n": 100, "p": 0.95, "avg_gap_when_up": 10.0, "avg_gap_when_dn": 0.5}
    f = kelly_fraction(stats)
    assert f == KELLY_MAX_FRACTION


def test_kelly_fraction_negative_returns_zero():
    """기댓값 음수면 0."""
    stats = {"n": 30, "p": 0.3, "avg_gap_when_up": 1.0, "avg_gap_when_dn": 5.0}
    f = kelly_fraction(stats)
    assert f == 0.0


def test_kelly_fraction_n_below_5_excluded():
    stats = {"n": 4, "p": 0.7, "avg_gap_when_up": 3.0, "avg_gap_when_dn": 1.0}
    assert kelly_fraction(stats) is None


def test_kelly_fraction_n_5_uses_03_factor():
    """n=5: factor 0.3. raw = 0.7/2 - 0.3/3 = 0.25, × 0.3 = 0.075."""
    stats = {"n": 5, "p": 0.7, "avg_gap_when_up": 3.0, "avg_gap_when_dn": 2.0}
    f = kelly_fraction(stats)
    assert f == pytest.approx(0.075, abs=0.001)


def test_kelly_fraction_no_loss_history_applies_sample_factor():
    """갭하 사례 0건 — 캡 × sample factor (H2 수정).

    n=30 (>= 20) → factor=0.8 → 0.25 * 0.8 = 0.20
    """
    stats = {"n": 30, "p": 1.0, "avg_gap_when_up": 3.0, "avg_gap_when_dn": float("nan")}
    f = kelly_fraction(stats)
    assert f == pytest.approx(0.20, rel=1e-9)


def test_kelly_fraction_no_loss_history_small_sample():
    """L=0 + n<10 → factor=0.3 적용. 0.25 * 0.3 = 0.075"""
    stats = {"n": 5, "p": 1.0, "avg_gap_when_up": 3.0, "avg_gap_when_dn": float("nan")}
    f = kelly_fraction(stats)
    assert f == pytest.approx(0.075, rel=1e-9)


def test_kelly_fraction_zero_W_returns_zero():
    stats = {"n": 30, "p": 0.5, "avg_gap_when_up": 0.0, "avg_gap_when_dn": 1.0}
    assert kelly_fraction(stats) == 0.0


# ── Sharpe ───────────────────────────────────────────────────────────────────

def test_sharpe_score_normal():
    """p=0.7, W=3, std=2 → 0.7×3/2 = 1.05"""
    stats = {"p": 0.7, "avg_gap_when_up": 3.0, "std_gap": 2.0}
    assert sharpe_score(stats) == pytest.approx(1.05, abs=0.001)


def test_sharpe_score_zero_std():
    """std=0 이면 expected 자체 반환 (분모 0 회피)."""
    stats = {"p": 0.7, "avg_gap_when_up": 3.0, "std_gap": 0.0}
    assert sharpe_score(stats) == pytest.approx(2.1, abs=0.001)


def test_sharpe_score_zero_W():
    stats = {"p": 0.7, "avg_gap_when_up": 0.0, "std_gap": 2.0}
    assert sharpe_score(stats) == 0.0


# ── equal_weights ────────────────────────────────────────────────────────────

def test_equal_weights():
    assert equal_weights(4) == [0.25, 0.25, 0.25, 0.25]


def test_equal_weights_zero():
    assert equal_weights(0) == []


# ── compute_sizing ───────────────────────────────────────────────────────────

def _candidate(n=20, p=0.7, W=3.0, L=2.0, std=2.0):
    return {
        "code": "X", "name": "X",
        "sizing_stats": {
            "n": n, "p": p,
            "avg_gap_when_up": W, "avg_gap_when_dn": L,
            "std_gap": std,
        },
    }


def test_compute_sizing_two_signals():
    cands = [_candidate(), _candidate()]
    result = compute_sizing(cands)
    assert result["equal"] == [0.5, 0.5]
    assert all(k == pytest.approx(0.20, abs=0.001) for k in result["kelly"])
    assert sum(result["sharpe"]) == pytest.approx(1.0, abs=0.001)


def test_compute_sizing_kelly_excludes_low_n():
    """n=4 종목은 kelly None, sharpe 정상."""
    cands = [_candidate(n=20), _candidate(n=4)]
    result = compute_sizing(cands)
    assert result["kelly"][0] is not None
    assert result["kelly"][1] is None


def test_compute_sizing_empty():
    result = compute_sizing([])
    assert result == {"equal": [], "kelly": [], "sharpe": []}


def test_compute_sizing_sharpe_normalizes():
    """sharpe 가중치 합 1.0 (양수 score 있을 때)."""
    cands = [_candidate(p=0.8, W=3.0), _candidate(p=0.5, W=2.0)]
    result = compute_sizing(cands)
    assert sum(result["sharpe"]) == pytest.approx(1.0, abs=0.001)
    # 첫번째 종목이 더 좋으니 더 큰 비중
    assert result["sharpe"][0] > result["sharpe"][1]


def test_compute_sizing_all_zero_sharpe_falls_back_to_zero():
    """모두 score 0 → sharpe weights 모두 0."""
    cands = [_candidate(p=0.5, W=0.0), _candidate(p=0.5, W=0.0)]
    result = compute_sizing(cands)
    assert result["sharpe"] == [0.0, 0.0]
