"""연구 backtest 레이어 테스트 — breadth 피처 + 국면 게이트 (P2-7)."""
from __future__ import annotations

import pandas as pd

from src.research.backtest import compute_breadth, score
from src.research.strategy_config import StrategyConfig


def test_compute_breadth():
    ts = pd.Timestamp("2026-05-25 09:30:00")
    df = pd.DataFrame({
        "ts": [ts, ts, ts, ts],
        "code": ["A", "B", "C", "D"],
        "dr": [6.0, 2.0, -1.0, 7.0],  # 4종목 중 3 상승=0.75, +5%↑ 2개
    })
    b = compute_breadth(df)
    assert len(b) == 1
    assert abs(b["breadth_up_frac"].iloc[0] - 0.75) < 1e-9
    assert b["breadth_n_up5"].iloc[0] == 2


def _bo_row(breadth: float) -> pd.DataFrame:
    """돌파 셋업 1행 (게이트 통과 조건) + 주어진 breadth."""
    return pd.DataFrame({
        "dist_high": [-0.8], "va5": [2.0], "va1": [1.0], "vp_": [120.0],
        "uw": [0.1], "lw": [0.0], "ma5_": [0.0], "dr": [8.0], "volr": [2.0],
        "bull": [True], "divbull": [False], "buy_grade": ["NEUTRAL"],
        "breadth_up_frac": [breadth],
    })


def test_regime_gate_blocks_weak_market():
    cfg = StrategyConfig(label="r", method="breakout", cut=6.0,
                         exit_kind="breakout", regime_breadth_min=0.5)
    assert score(_bo_row(0.70), cfg)[0] >= 6.0   # 강세장 → 통과
    assert score(_bo_row(0.30), cfg)[0] == 0.0   # 약세장 → 차단


def test_no_regime_gate_when_zero():
    cfg = StrategyConfig(label="r", method="breakout", cut=6.0,
                         exit_kind="breakout", regime_breadth_min=0.0)
    assert score(_bo_row(0.10), cfg)[0] >= 6.0   # 게이트 없음 → breadth 무관 통과
