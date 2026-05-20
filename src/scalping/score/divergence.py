"""가격-체결강도 다이버전스 (R13).

`docs/jongbae-strategy.md` R13 참조.

정의 (5분 윈도우):
    price_5m_change = (current - close_5m_ago) / close_5m_ago × 100
    vp_5ma_change   = VP_5MA(now) - VP_5MA(5분 전)

    bearish_divergence = price 상승 AND VP_5MA 하락 → 고점 신호
    bullish_divergence = price 하락 AND VP_5MA 상승 → 매집 신호

R14 매수 점수 ±2, R15 매도 트리거 (Bearish 시 즉시 청산 시그널).

pure 함수 — 입력은 모두 호출자가 준비한 스칼라.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DivergenceState:
    bearish: bool   # 가격↑ / VP_5MA↓
    bullish: bool   # 가격↓ / VP_5MA↑
    price_change_pct: float
    vp_5ma_delta: float


def compute_divergence(
    price_now: float,
    price_5m_ago: float,
    vp_5ma_now: float,
    vp_5ma_5m_ago: float,
) -> DivergenceState:
    """가격/VP 변화 → 다이버전스 판정.

    NaN/0 입력은 안전하게 (False, False) 반환.
    """
    bearish = False
    bullish = False
    price_change_pct = float("nan")
    vp_5ma_delta = float("nan")

    if (
        price_now == price_now
        and price_5m_ago == price_5m_ago
        and price_5m_ago > 0
    ):
        price_change_pct = (price_now - price_5m_ago) / price_5m_ago * 100.0

    if vp_5ma_now == vp_5ma_now and vp_5ma_5m_ago == vp_5ma_5m_ago:
        vp_5ma_delta = vp_5ma_now - vp_5ma_5m_ago

    if (
        price_change_pct == price_change_pct
        and vp_5ma_delta == vp_5ma_delta
    ):
        if price_change_pct > 0 and vp_5ma_delta < 0:
            bearish = True
        elif price_change_pct < 0 and vp_5ma_delta > 0:
            bullish = True

    return DivergenceState(
        bearish=bearish,
        bullish=bullish,
        price_change_pct=price_change_pct,
        vp_5ma_delta=vp_5ma_delta,
    )
