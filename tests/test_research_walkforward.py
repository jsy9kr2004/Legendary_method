"""검증 루프 (P0) 단위 테스트 — config 직렬화 + 게이트 로직 (합성 데이터)."""
from __future__ import annotations

from src.research.strategy_config import StrategyConfig, candidate_configs
from src.research.walkforward import adoption_verdict


def test_config_roundtrip():
    cfg = candidate_configs()[1]  # breakout_gated_v5
    again = StrategyConfig.from_dict(cfg.to_dict())
    assert again == cfg
    assert again.fingerprint() == cfg.fingerprint()


def test_fingerprint_ignores_label():
    a = StrategyConfig(label="x", method="breakout", cut=6.0, exit_kind="breakout")
    b = StrategyConfig(label="y", method="breakout", cut=6.0, exit_kind="breakout")
    assert a.fingerprint() == b.fingerprint()  # label 만 다르면 동일 지문
    c = StrategyConfig(label="x", method="breakout", cut=7.0, exit_kind="breakout")
    assert a.fingerprint() != c.fingerprint()  # 파라미터 다르면 다른 지문


def test_gate_blocks_insufficient_data():
    wf = {"oos_days": 2, "sel_trades": 23, "sel_net": 0.30, "baseline_net": 0.10}
    v = adoption_verdict(wf)
    assert v["adopt"] is False
    assert any("거래일 부족" in r for r in v["reasons"])


def test_gate_blocks_negative_net():
    wf = {"oos_days": 20, "sel_trades": 100, "sel_net": -0.15, "baseline_net": -0.29}
    v = adoption_verdict(wf)
    assert v["adopt"] is False
    assert any("net 음수" in r for r in v["reasons"])


def test_gate_blocks_insufficient_margin():
    wf = {"oos_days": 20, "sel_trades": 100, "sel_net": 0.12, "baseline_net": 0.10}
    v = adoption_verdict(wf)
    assert v["adopt"] is False
    assert any("마진 부족" in r for r in v["reasons"])


def test_gate_passes_when_all_met():
    wf = {"oos_days": 20, "sel_trades": 100, "sel_net": 0.35, "baseline_net": 0.10}
    v = adoption_verdict(wf)
    assert v["adopt"] is True
    assert v["reasons"] == ["게이트 통과"]
