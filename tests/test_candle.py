"""src.scalping.score.candle (R12) 단위 테스트."""
from __future__ import annotations

import pandas as pd

from src.scalping.score.candle import (
    classify_candle,
    is_bearish_exit_signal,
    is_clean_bullish,
    is_weak_candle,
    latest_completed_candle,
)


# ── classify_candle ──────────────────────────────────────────────────────────


def test_classify_bullish_clean():
    s = classify_candle(o=100, h=110, l=99, c=109)
    assert s.type == "bullish"
    # total=11, upper=(110-109)/11 ≈ 0.09
    assert s.upper_wick < 0.1
    assert s.body == 9


def test_classify_bearish_long_upper_wick():
    s = classify_candle(o=110, h=130, l=100, c=105)
    assert s.type == "bearish"
    # total=30, upper=(130-110)/30 ≈ 0.667
    assert 0.65 < s.upper_wick < 0.7


def test_classify_doji():
    s = classify_candle(o=100, h=110, l=90, c=100)
    assert s.type == "doji"


def test_classify_zero_range():
    """high==low (정체봉) — division 안전."""
    s = classify_candle(o=100, h=100, l=100, c=100)
    assert s.type == "doji"
    assert s.upper_wick == 0
    assert s.lower_wick == 0


def test_classify_wicks_clamped():
    """0~1 범위 안에 들어옴."""
    s = classify_candle(o=100, h=110, l=90, c=105)
    assert 0 <= s.upper_wick <= 1
    assert 0 <= s.lower_wick <= 1


# ── latest_completed_candle ──────────────────────────────────────────────────


def test_latest_completed_candle_empty_returns_none():
    assert latest_completed_candle(pd.DataFrame()) is None


def test_latest_completed_candle_missing_columns():
    df = pd.DataFrame({"foo": [1, 2]})
    assert latest_completed_candle(df) is None


def test_latest_completed_candle_picks_last_row():
    df = pd.DataFrame({
        "open":  [100, 105],
        "high":  [110, 112],
        "low":   [99, 104],
        "close": [108, 111],
    })
    s = latest_completed_candle(df)
    assert s is not None
    assert s.type == "bullish"
    assert s.close == 111


def test_latest_completed_candle_case_insensitive_columns():
    df = pd.DataFrame({
        "Open":  [100],
        "HIGH":  [110],
        "Low":   [99],
        "Close": [108],
    })
    s = latest_completed_candle(df)
    assert s is not None
    assert s.type == "bullish"


def test_latest_completed_candle_zero_price_returns_none():
    df = pd.DataFrame({
        "open": [0], "high": [0], "low": [0], "close": [0],
    })
    assert latest_completed_candle(df) is None


# ── R14 / R15 임계 판정 ──────────────────────────────────────────────────────


def test_is_clean_bullish_true():
    s = classify_candle(o=100, h=110, l=99, c=109)  # upper_wick ~ 0.09
    assert is_clean_bullish(s) is True


def test_is_clean_bullish_long_upper_wick_false():
    s = classify_candle(o=100, h=115, l=99, c=105)  # upper_wick ~ 0.625
    assert is_clean_bullish(s) is False


def test_is_clean_bullish_doji_false():
    s = classify_candle(o=100, h=110, l=90, c=100)
    assert is_clean_bullish(s) is False


def test_is_weak_candle_bearish_true():
    s = classify_candle(o=100, h=102, l=90, c=92)
    assert is_weak_candle(s) is True


def test_is_weak_candle_bullish_with_long_upper_wick():
    """양봉이라도 윗꼬리 > 40% 면 weak."""
    s = classify_candle(o=100, h=130, l=99, c=105)  # upper=(130-105)/31 ≈ 0.806
    assert is_weak_candle(s) is True


def test_is_weak_candle_clean_bullish_false():
    s = classify_candle(o=100, h=110, l=99, c=109)
    assert is_weak_candle(s) is False


def test_is_bearish_exit_signal_true():
    """음봉 + 윗꼬리 > 50%."""
    s = classify_candle(o=110, h=130, l=100, c=105)  # upper ~ 0.667
    assert is_bearish_exit_signal(s) is True


def test_is_bearish_exit_signal_bullish_false():
    s = classify_candle(o=100, h=130, l=99, c=105)  # 양봉이라 False
    assert is_bearish_exit_signal(s) is False


def test_is_bearish_exit_signal_short_wick_false():
    s = classify_candle(o=110, h=112, l=100, c=105)  # 음봉이지만 upper ~ 0.166
    assert is_bearish_exit_signal(s) is False
