"""매매법 분류 순수함수 테스트 — docs §11.1."""
from __future__ import annotations

from src.scalping.score.method_label import classify_method


def test_breakout_setup():
    """레벨 재테스트(-0.8%) + 강한 거래량 + 양봉 → 돌파."""
    r = classify_method(
        dist_high_pct=-0.8, daily_return_pct=8.0, vol_accel_5m=2.0,
        vp=120, candle_bullish=True, candle_upper_wick=0.1,
        volume_ratio_vs_prev_day=2.0,
    )
    assert r.setup == "breakout"
    assert r.score_breakout >= 6.0


def test_pullback_setup():
    """1차급등 후 5MA 지지 + 거래량 재유입 + 망치형 → 눌림. 가속 죽어도 OK."""
    r = classify_method(
        dist_high_pct=-3.0, daily_return_pct=12.0, vol_accel_5m=0.6,
        vol_accel_1m=1.8, vp=105, price_vs_ma5_pct=-0.5,
        candle_lower_wick=0.6, divergence_bullish=True,
    )
    assert r.setup == "pullback"
    assert r.score_pullback >= 7.0


def test_chase_blowoff():
    """고점 정각 + 일봉 과열(+22%) → 추격 경고."""
    r = classify_method(
        dist_high_pct=0.0, daily_return_pct=22.0, vol_accel_5m=2.5, vp=130,
    )
    assert r.setup == "chase"
    assert r.chase_warning is True


def test_quiet_is_none():
    """급등도 없고 셋업 신호도 약함 → none."""
    r = classify_method(
        dist_high_pct=-8.0, daily_return_pct=1.0, vol_accel_5m=0.5,
        vol_accel_1m=0.5, vp=95, price_vs_ma5_pct=-5.0,
    )
    assert r.setup == "none"


def test_pullback_not_breakout_when_resting():
    """눌림 자리(가속 죽음)는 돌파로 안 잡힘 — 모멘텀 게이트 실패."""
    r = classify_method(
        dist_high_pct=-3.0, daily_return_pct=10.0, vol_accel_5m=0.5,
        vol_accel_1m=1.6, vp=100, price_vs_ma5_pct=-0.3, candle_lower_wick=0.6,
    )
    assert r.setup == "pullback"
    assert r.score_breakout == 0.0  # 돌파 게이트(거래량) 미통과
