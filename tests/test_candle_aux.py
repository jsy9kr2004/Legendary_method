"""candle_count_aux (양봉/장대양봉 카운트, 표시 전용) 단위 테스트."""
from __future__ import annotations

import datetime as dt

import pandas as pd

from src.overnight.gap_stats import candle_count_aux


def _ohlcv(closes: list[float], code: str = "000001") -> pd.DataFrame:
    base = dt.date(2026, 5, 1)
    return pd.DataFrame([
        {"code": code, "date": base + dt.timedelta(days=i), "close": c}
        for i, c in enumerate(closes)
    ])


def test_counts_big_candles_and_consec_up():
    # rets: nan, +12, +5, +14, -2, +12  → big(≥10): 3, 연속상승(끝): 1, 오늘 장대 3번째
    closes = [1000, 1120, 1176, 1340.64, 1313.8272, 1471.486464]
    out = candle_count_aux(_ohlcv(closes), "000001", dt.date(2026, 5, 6))
    assert out["big_candle_count"] == 3
    assert out["consec_up_days"] == 1
    assert out["today_is_nth_big"] == 3
    assert out["big_threshold"] == 10.0


def test_today_not_big_gives_nth_zero():
    # 마지막 ret +5% (장대 아님), 앞에 +12/+14 = big 2
    closes = [1000, 1120, 1176, 1340.64, 1313.8272, 1379.5186]  # 마지막 +5%
    out = candle_count_aux(_ohlcv(closes), "000001", dt.date(2026, 5, 6))
    assert out["today_is_nth_big"] == 0
    assert out["big_candle_count"] == 2  # +12, +14


def test_consec_up_multiple():
    closes = [1000, 1050, 1100, 1150]  # +5,+4.8,+4.5 모두 양봉
    out = candle_count_aux(_ohlcv(closes), "000001", dt.date(2026, 5, 4))
    assert out["consec_up_days"] == 3


def test_today_filter_excludes_future():
    closes = [1000, 1120, 1300]  # day2 = 5/3 가 +16% 장대
    # today=5/2 이면 day2(5/3) 제외 → 5/2 까지만 (+12%)
    out = candle_count_aux(_ohlcv(closes), "000001", dt.date(2026, 5, 2))
    assert out["big_candle_count"] == 1
    assert out["today_is_nth_big"] == 1


def test_empty_and_unknown_code():
    out = candle_count_aux(pd.DataFrame(), "000001", dt.date(2026, 5, 6))
    assert out["big_candle_count"] == 0 and out["consec_up_days"] == 0
    out2 = candle_count_aux(_ohlcv([1000, 1120]), "999999", dt.date(2026, 5, 6))
    assert out2 == {"consec_up_days": 0, "big_candle_count": 0,
                    "today_is_nth_big": 0, "big_threshold": 10.0}


def test_custom_threshold():
    closes = [1000, 1080, 1170]  # +8%, +8.3%
    out = candle_count_aux(_ohlcv(closes), "000001", dt.date(2026, 5, 3), big_threshold=5.0)
    assert out["big_candle_count"] == 2  # 둘 다 ≥5%
