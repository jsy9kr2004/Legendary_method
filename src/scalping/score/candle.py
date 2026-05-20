"""봉 패턴 분석 (Buy.Candle) — 5분봉 OHLC 기반.

`docs/scalping-strategy.md` Buy.Candle 참조. 정정 이력 round 14.

배경: Buy.Accel 가속만으론 "양봉 정체"인지 "큰 음봉"인지 구분 못함. 봉 자체 형태를
별도 시그널로.

기준: 최근 완성봉 (진행 중 봉 제외). 5분봉 권장.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from src.scalping.score.thresholds import (
    UPPER_WICK_BEARISH_EXIT,
    UPPER_WICK_CLEAN,
    UPPER_WICK_LONG,
)

CandleType = Literal["bullish", "bearish", "doji"]

# 0 division 가드. 1원 미만은 정체 = doji 처리하므로 사실상 이 ε 도달 X
EPS = 1e-9


@dataclass(frozen=True)
class CandleShape:
    """봉 1개의 형태 요약 (Buy.Score 매수 점수 / Exit.Triggers 매도 트리거 입력)."""
    type: CandleType
    open: float
    high: float
    low: float
    close: float
    body: float          # |close - open|
    upper_wick: float    # (high - max(open,close)) / total_range
    lower_wick: float    # (min(open,close) - low) / total_range


def classify_candle(o: float, h: float, l: float, c: float) -> CandleShape:
    """단일 봉 OHLC → 패턴 분류 + 꼬리 비율.

    Args:
        o, h, l, c: 시가 / 고가 / 저가 / 종가. 모두 양수 가정.

    Returns:
        CandleShape. close == open 이고 high == low 면 type=doji.
    """
    total = max(h - l, EPS)
    body = abs(c - o)

    if c > o:
        ctype: CandleType = "bullish"
    elif c < o:
        ctype = "bearish"
    else:
        ctype = "doji"

    upper_wick = (h - max(o, c)) / total
    lower_wick = (min(o, c) - l) / total

    # clamping (부동소수 안전)
    upper_wick = max(0.0, min(1.0, upper_wick))
    lower_wick = max(0.0, min(1.0, lower_wick))

    return CandleShape(
        type=ctype,
        open=float(o),
        high=float(h),
        low=float(l),
        close=float(c),
        body=float(body),
        upper_wick=float(upper_wick),
        lower_wick=float(lower_wick),
    )


def latest_completed_candle(minute_bars: pd.DataFrame) -> CandleShape | None:
    """`minute_bars` 의 가장 최근 완성봉. 비어 있거나 컬럼 부족이면 None.

    DataFrame columns 가정: open, high, low, close (대소문자 무시).
    "완성봉" 판정은 호출자가 책임 — 본 함수는 단순히 마지막 행 사용.
    """
    if minute_bars is None or minute_bars.empty:
        return None

    cols = {c.lower(): c for c in minute_bars.columns}
    required = ("open", "high", "low", "close")
    if not all(r in cols for r in required):
        return None

    row = minute_bars.iloc[-1]
    try:
        o = float(row[cols["open"]])
        h = float(row[cols["high"]])
        l = float(row[cols["low"]])
        c = float(row[cols["close"]])
    except (TypeError, ValueError):
        return None
    if any(v != v for v in (o, h, l, c)):  # NaN
        return None
    if o <= 0 or h <= 0 or l <= 0 or c <= 0:
        return None
    return classify_candle(o, h, l, c)


# ── Buy.Score / Exit.Triggers 임계 판정 ────────────────────────────────────────────────────────


def is_clean_bullish(shape: CandleShape) -> bool:
    """Buy.Score +2: 양봉 AND upper_wick < 0.3. 장대양봉 / 깨끗한 상승."""
    return shape.type == "bullish" and shape.upper_wick < UPPER_WICK_CLEAN


def is_weak_candle(shape: CandleShape) -> bool:
    """Buy.Score -2: 음봉 OR upper_wick > 0.4. 약한 봉 / 매도 우위."""
    return shape.type == "bearish" or shape.upper_wick > UPPER_WICK_LONG


def is_bearish_exit_signal(shape: CandleShape) -> bool:
    """Exit.Triggers C4: 음봉 AND upper_wick > 0.5. 보유 모드 즉시 청산 신호."""
    return shape.type == "bearish" and shape.upper_wick > UPPER_WICK_BEARISH_EXIT
