"""시장 폭(breadth) 게이지 — compute_market_breadth (P2-7 표시)."""
from __future__ import annotations

from src.data.intraday import compute_market_breadth


def test_compute_market_breadth():
    snap = {
        "A": {"daily_return": 6.0}, "B": {"daily_return": 2.0},
        "C": {"daily_return": -1.0}, "D": {"daily_return": 7.0},
        "E": {"daily_return": None},  # NaN 제외
    }
    b = compute_market_breadth(snap)
    assert b["n_total"] == 4          # E 제외
    assert b["n_up"] == 3
    assert b["n_up5"] == 2            # 6,7
    assert abs(b["breadth_up_frac"] - 0.75) < 1e-9


def test_compute_market_breadth_empty():
    assert compute_market_breadth({}) is None
    assert compute_market_breadth({"A": {"daily_return": None}}) is None
