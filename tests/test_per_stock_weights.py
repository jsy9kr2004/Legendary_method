"""Per-stock weight v11.1 — load + per-code score 사용 검증 (2026-05-29)."""
from __future__ import annotations

import json
import os
import tempfile

import pandas as pd
import pytest

from src.scalping.signals.weighted_score import (
    BUY_FEATS_V11, SELL_FEATS_V11,
    compute_score_buy, compute_score_sell,
    reload_per_stock_weights,
)


def _make_row(touch_count=5, zscore=-1.5, stoch_k=20, atr_pct=2.0,
              lower_wick_pct=0.4, upper_wick_pct=0.1, is_doji=0,
              is_bullish=0, is_bearish=1, consec_bear=3, consec_bull=0,
              williams_r=-80, rsi=30, support_dist_pct=-0.5):
    return pd.Series({
        "touch_count": touch_count, "zscore": zscore, "stoch_k": stoch_k,
        "williams_r": williams_r, "atr_pct": atr_pct,
        "support_dist_pct": support_dist_pct, "rsi": rsi,
        "lower_wick_pct": lower_wick_pct, "upper_wick_pct": upper_wick_pct,
        "is_doji": is_doji, "is_bullish": is_bullish, "is_bearish": is_bearish,
        "consec_bear": consec_bear, "consec_bull": consec_bull,
    })


@pytest.fixture
def per_stock_json(tmp_path, monkeypatch):
    """일회용 per_stock_weights.json — TEST_CODE 만 강제 weight 부여."""
    data = {
        "version": "v11.1-test",
        "per_stock": {
            "TEST_HIGH_BUY": {
                # zscore 만 매우 강하게 (단저 시 LOW 방향)
                "buy": {"zscore": [-1, 0.95]},
                "sell": {},
                "n_gt": 100,
            },
            "TEST_HIGH_SELL": {
                "buy": {},
                "sell": {"zscore": [+1, 0.95]},
                "n_gt": 100,
            },
        },
    }
    path = tmp_path / "weights.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    monkeypatch.setenv("PER_STOCK_WEIGHTS_PATH", str(path))
    reload_per_stock_weights()
    yield path
    reload_per_stock_weights()


def test_global_weight_when_no_code(per_stock_json):
    """code=None 이면 global v11 BUY_FEATS 사용."""
    row = _make_row(zscore=-2.5)  # 단저 강 신호
    sc_global = compute_score_buy(row)
    sc_none = compute_score_buy(row, code=None)
    assert sc_global == sc_none
    assert sc_global > 0  # global v11 도 zscore 음수면 단저 양수 score


def test_per_stock_buy_only_uses_zscore(per_stock_json):
    """TEST_HIGH_BUY 는 zscore 단일 feature 만 사용 (다른 변동 영향 X)."""
    row_low_z = _make_row(zscore=-2.5)  # zscore 매우 낮음 (단저 신호)
    row_high_z = _make_row(zscore=+2.5)  # zscore 매우 높음
    sc_low = compute_score_buy(row_low_z, code="TEST_HIGH_BUY")
    sc_high = compute_score_buy(row_high_z, code="TEST_HIGH_BUY")
    assert sc_low > sc_high  # 낮은 zscore 일수록 단저 score 높음 (direction=-1)
    assert sc_low > 0.8  # 매우 강한 신호 → score 거의 1
    assert sc_high < 0.2


def test_per_stock_sell_only_uses_zscore(per_stock_json):
    row_low_z = _make_row(zscore=-2.5)
    row_high_z = _make_row(zscore=+2.5)
    sc_low = compute_score_sell(row_low_z, code="TEST_HIGH_SELL")
    sc_high = compute_score_sell(row_high_z, code="TEST_HIGH_SELL")
    assert sc_high > sc_low  # zscore 높을수록 단고 score 높음 (direction=+1)


def test_unknown_code_falls_back_to_global(per_stock_json):
    """등록 안 된 종목은 global v11 fallback."""
    row = _make_row(zscore=-2.5)
    sc_unknown = compute_score_buy(row, code="999999")
    sc_global = compute_score_buy(row)
    assert sc_unknown == sc_global


def test_empty_feats_falls_back_to_global(per_stock_json):
    """per-stock 에 buy feats 가 빈 경우 global fallback (TEST_HIGH_SELL 의 buy)."""
    row = _make_row(zscore=-2.5)
    sc_fallback = compute_score_buy(row, code="TEST_HIGH_SELL")  # sell 만 등록됨, buy 빈 dict
    sc_global = compute_score_buy(row)
    assert sc_fallback == sc_global
