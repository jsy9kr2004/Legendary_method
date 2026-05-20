"""src.scalping.score.divergence (Buy.Div) 단위 테스트."""
from __future__ import annotations

from src.scalping.score.divergence import compute_divergence


def test_bearish_divergence():
    """가격 상승 + VP_5MA 하락 → 고점 신호."""
    d = compute_divergence(price_now=110, price_5m_ago=100, vp_5ma_now=105, vp_5ma_5m_ago=120)
    assert d.bearish is True
    assert d.bullish is False
    assert d.price_change_pct == 10.0
    assert d.vp_5ma_delta == -15.0


def test_bullish_divergence():
    """가격 하락 + VP_5MA 상승 → 매집 신호."""
    d = compute_divergence(price_now=98, price_5m_ago=100, vp_5ma_now=130, vp_5ma_5m_ago=110)
    assert d.bearish is False
    assert d.bullish is True


def test_no_divergence_same_direction():
    """가격 ↑ + VP ↑ — 정상 동조."""
    d = compute_divergence(price_now=105, price_5m_ago=100, vp_5ma_now=130, vp_5ma_5m_ago=120)
    assert d.bearish is False
    assert d.bullish is False


def test_no_divergence_zero_change():
    """가격 변화 0."""
    d = compute_divergence(price_now=100, price_5m_ago=100, vp_5ma_now=130, vp_5ma_5m_ago=120)
    assert d.bearish is False
    assert d.bullish is False


def test_nan_safe():
    d = compute_divergence(
        price_now=float("nan"), price_5m_ago=100,
        vp_5ma_now=105, vp_5ma_5m_ago=120,
    )
    assert d.bearish is False
    assert d.bullish is False


def test_zero_price_safe():
    """price_5m_ago = 0 division 회피."""
    d = compute_divergence(price_now=110, price_5m_ago=0, vp_5ma_now=105, vp_5ma_5m_ago=120)
    assert d.bearish is False
    assert d.bullish is False
