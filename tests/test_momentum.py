"""src.jongbae.momentum 단위 테스트."""
from __future__ import annotations

import pandas as pd
import pytest

from src.jongbae.momentum import (
    compute_accel_ratio,
    compute_minute_ma,
    compute_vwap,
    is_exit_signal,
    is_recent_high,
    is_strong_rise,
    is_transition_candidate,
    price_vs_ma_pct,
    price_vs_vwap_pct,
    short_trend_sparkline,
    vol_accel_1m,
    vol_accel_5m,
)


def _bars(values: list[int]) -> pd.DataFrame:
    """1분봉 시퀀스. trading_value 만 사용."""
    return pd.DataFrame([
        {"code": "A", "date": "20260510", "time": f"09{i:02d}00",
         "open": 1000, "high": 1010, "low": 990, "close": 1000,
         "volume": v // 1000, "trading_value": v}
        for i, v in enumerate(values)
    ])


# ── compute_accel_ratio ──────────────────────────────────────────────────────


def test_accel_ratio_basic_5x_acceleration():
    """recent 5분 평균 vs baseline 30분 평균 (=6 windows) 기준."""
    # baseline 30분: 각 분 = 4억 → 윈도우 5분 합 = 20억, 6 windows 합 = 120억
    # baseline_per_window = 120 / 6 = 20 억
    # recent 5분 합 = 100억 (각 분 20억)
    # accel = 100 / 20 = 5배
    bars = _bars([400_000_000] * 30 + [2_000_000_000] * 5)
    ratio = compute_accel_ratio(bars, recent_minutes=5, baseline_minutes=30)
    assert ratio == pytest.approx(5.0, rel=1e-3)


def test_accel_ratio_no_acceleration_returns_one():
    bars = _bars([1_000_000_000] * 35)
    ratio = compute_accel_ratio(bars, recent_minutes=5, baseline_minutes=30)
    assert ratio == pytest.approx(1.0, rel=1e-3)


def test_accel_ratio_decline():
    """직전 평균 대비 거래대금 감소."""
    # baseline 30분: 각 분 10억 → 윈도우 합 50억, 평균 = 50억
    # recent 5분 합 = 25억 → ratio = 0.5
    bars = _bars([1_000_000_000] * 30 + [500_000_000] * 5)
    ratio = compute_accel_ratio(bars, recent_minutes=5, baseline_minutes=30)
    assert ratio == pytest.approx(0.5, rel=1e-3)


def test_accel_ratio_insufficient_data():
    bars = _bars([1] * 5)  # baseline 부족
    assert compute_accel_ratio(bars) != compute_accel_ratio(bars)  # NaN


def test_accel_ratio_empty():
    assert compute_accel_ratio(pd.DataFrame()) != compute_accel_ratio(pd.DataFrame())


def test_accel_ratio_zero_baseline():
    """baseline 거래대금 0 → NaN (분모)."""
    bars = _bars([0] * 30 + [1_000_000_000] * 5)
    ratio = compute_accel_ratio(bars)
    assert ratio != ratio  # NaN


# ── is_strong_rise ───────────────────────────────────────────────────────────


def test_strong_rise_above_threshold():
    assert is_strong_rise(accel_ratio=10.5, recent_bar_value=25_000_000_000) is True


def test_strong_rise_low_accel():
    assert is_strong_rise(accel_ratio=8.0, recent_bar_value=25_000_000_000) is False


def test_strong_rise_low_value():
    assert is_strong_rise(accel_ratio=12.0, recent_bar_value=10_000_000_000) is False


def test_strong_rise_nan_safe():
    assert is_strong_rise(accel_ratio=float("nan"), recent_bar_value=99_000_000_000) is False


# ── is_transition_candidate ──────────────────────────────────────────────────


def test_transition_candidate_all_conditions_pass():
    assert is_transition_candidate(
        accel_ratio=6.0,
        recent_bar_value=25_000_000_000,
        candidate_turnover=12.0,
        incumbent_turnover=18.0,  # 12 / 18 = 0.667 ≥ 0.6
        turnover_ratio_threshold=0.6,
    ) is True


def test_transition_candidate_low_accel():
    assert is_transition_candidate(
        accel_ratio=4.0,
        recent_bar_value=25_000_000_000,
        candidate_turnover=12.0,
        incumbent_turnover=18.0,
        turnover_ratio_threshold=0.6,
    ) is False


def test_transition_candidate_low_value():
    assert is_transition_candidate(
        accel_ratio=6.0,
        recent_bar_value=10_000_000_000,  # 20억 미만
        candidate_turnover=12.0,
        incumbent_turnover=18.0,
        turnover_ratio_threshold=0.6,
    ) is False


def test_transition_candidate_low_turnover_ratio():
    assert is_transition_candidate(
        accel_ratio=6.0,
        recent_bar_value=25_000_000_000,
        candidate_turnover=8.0,
        incumbent_turnover=18.0,  # 8/18 = 0.44 < 0.6
        turnover_ratio_threshold=0.6,
    ) is False


def test_transition_candidate_nan_inputs():
    assert is_transition_candidate(
        accel_ratio=float("nan"),
        recent_bar_value=25_000_000_000,
        candidate_turnover=12.0,
        incumbent_turnover=18.0,
        turnover_ratio_threshold=0.6,
    ) is False


# ── is_exit_signal ───────────────────────────────────────────────────────────


def test_exit_signal_below_threshold():
    """가속배율 0.5 (= 직전 평균 대비 50% 감소) → 이탈 신호."""
    assert is_exit_signal(accel_ratio=0.5) is True


def test_exit_signal_at_baseline():
    """가속배율 1.0 (=평균 유지) → 이탈 X."""
    assert is_exit_signal(accel_ratio=1.0) is False


def test_exit_signal_acceleration():
    """가속배율 3.0 → 가속 중. 이탈 신호 아님."""
    assert is_exit_signal(accel_ratio=3.0) is False


def test_exit_signal_nan():
    assert is_exit_signal(accel_ratio=float("nan")) is False


# ── is_recent_high ───────────────────────────────────────────────────────────


def _ohlcv(rows: list[tuple[str, int]]) -> pd.DataFrame:
    """code, high 만 채운 미니 일봉."""
    return pd.DataFrame([
        {"code": "A", "date": pd.Timestamp(d), "high": h}
        for d, h in rows
    ])


def test_recent_high_breakout():
    df = _ohlcv([("2026-04-01", 1000), ("2026-04-02", 1100), ("2026-04-03", 1050)])
    today = pd.Timestamp("2026-04-04")
    assert is_recent_high(df, today_high=1200, code="A", today=today, lookback_days=20) is True


def test_recent_high_not_breakout():
    df = _ohlcv([("2026-04-01", 1000), ("2026-04-02", 1100), ("2026-04-03", 1050)])
    today = pd.Timestamp("2026-04-04")
    assert is_recent_high(df, today_high=1080, code="A", today=today, lookback_days=20) is False


def test_recent_high_lookback_window_respected():
    """lookback 밖의 더 높은 고가는 무시."""
    df = _ohlcv([
        ("2026-01-01", 5000),  # 매우 오래된 고가 — lookback 밖
        ("2026-04-01", 1000),
        ("2026-04-02", 1100),
    ])
    today = pd.Timestamp("2026-04-04")
    assert is_recent_high(df, today_high=1200, code="A", today=today, lookback_days=2) is True


def test_recent_high_empty_data():
    assert is_recent_high(pd.DataFrame(), today_high=1000, code="A") is False


def test_recent_high_other_code():
    """다른 종목 데이터는 무시."""
    df = pd.DataFrame([
        {"code": "B", "date": pd.Timestamp("2026-04-01"), "high": 5000},
    ])
    assert is_recent_high(df, today_high=1000, code="A") is False


# ── short_trend_sparkline ────────────────────────────────────────────────────


def test_sparkline_length():
    bars = _bars([1, 2, 3, 4, 5, 6, 7, 8])
    spark = short_trend_sparkline(bars, n_recent=6)
    assert len(spark) == 6


def test_sparkline_uses_block_chars():
    bars = _bars([1, 2, 3, 4, 5, 6])
    spark = short_trend_sparkline(bars, n_recent=6)
    # 모든 문자가 block range 안
    for c in spark:
        assert c in " ▁▂▃▄▅▆▇█"


def test_sparkline_constant_values():
    bars = _bars([1000, 1000, 1000, 1000, 1000])
    spark = short_trend_sparkline(bars, n_recent=5)
    assert len(spark) == 5
    # 모두 동일 문자
    assert len(set(spark)) == 1


def test_sparkline_empty():
    assert short_trend_sparkline(pd.DataFrame()) == ""


# ── R11 vol_accel_1m / vol_accel_5m ──────────────────────────────────────────


def test_vol_accel_1m_basic():
    """1분 recent / 5분 baseline 평균."""
    # baseline 5분 = 각 분 1억 → 합 5억, 5 windows 합산 = 5억 / 5 = 1억 per_window
    # recent 1분 = 3억
    # accel = 3억 / 1억 = 3.0
    bars = _bars([100_000_000] * 5 + [300_000_000])
    ratio = vol_accel_1m(bars)
    assert ratio == pytest.approx(3.0, rel=1e-3)


def test_vol_accel_5m_basic():
    """5분 recent / 20분 baseline."""
    # baseline 20분 = 각 분 1억 → 20억, 4 windows → 5억/window
    # recent 5분 = 각 분 4억 = 20억
    # accel = 20 / 5 = 4.0
    bars = _bars([100_000_000] * 20 + [400_000_000] * 5)
    ratio = vol_accel_5m(bars)
    assert ratio == pytest.approx(4.0, rel=1e-3)


def test_vol_accel_1m_drain():
    """자금 고갈 — accel < 0.5."""
    bars = _bars([1_000_000_000] * 5 + [200_000_000])
    ratio = vol_accel_1m(bars)
    # 1분 = 2억 / per_window = 10억 = 0.2배
    assert ratio == pytest.approx(0.2, rel=1e-3)


def test_vol_accel_1m_insufficient_bars():
    """recent + recent 미만이면 NaN (compute_accel_ratio 가드)."""
    bars = _bars([100_000_000])  # 1개만 — recent(1) + recent(1) = 2 미만
    ratio = vol_accel_1m(bars)
    assert ratio != ratio  # NaN


# ── compute_vwap / price_vs_vwap_pct (round 23, P0-1) ────────────────────────


def _ohlcv_bars(rows: list[tuple[float, float, float, float, int]]) -> pd.DataFrame:
    """(open, high, low, close, volume) 시퀀스 → 분봉 DataFrame."""
    return pd.DataFrame([
        {"code": "A", "date": "20260514", "time": f"09{i:02d}00",
         "open": o, "high": h, "low": l, "close": c,
         "volume": v, "trading_value": int(((h + l + c) / 3) * v)}
        for i, (o, h, l, c, v) in enumerate(rows)
    ])


def test_vwap_single_bar_equals_typical_price():
    """단일 봉 → VWAP = (H+L+C)/3."""
    bars = _ohlcv_bars([(100.0, 110.0, 90.0, 100.0, 1000)])
    vwap = compute_vwap(bars)
    assert vwap == pytest.approx(100.0, rel=1e-6)


def test_vwap_volume_weighted():
    """볼륨 큰 봉에 가중치. (H+L+C)/3 = 100 (vol=10), 200 (vol=90).
    → VWAP = (100*10 + 200*90) / 100 = 190.
    """
    bars = _ohlcv_bars([
        (100.0, 100.0, 100.0, 100.0, 10),
        (200.0, 200.0, 200.0, 200.0, 90),
    ])
    vwap = compute_vwap(bars)
    assert vwap == pytest.approx(190.0, rel=1e-6)


def test_vwap_empty_returns_nan():
    bars = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    vwap = compute_vwap(bars)
    assert vwap != vwap  # NaN


def test_vwap_zero_volume_returns_nan():
    bars = _ohlcv_bars([(100.0, 100.0, 100.0, 100.0, 0)])
    vwap = compute_vwap(bars)
    assert vwap != vwap


def test_vwap_missing_columns_returns_nan():
    bars = pd.DataFrame([{"close": 100, "volume": 10}])  # high/low 없음
    vwap = compute_vwap(bars)
    assert vwap != vwap


def test_price_vs_vwap_pct_above():
    """가격 102, VWAP 100 → +2%."""
    assert price_vs_vwap_pct(102.0, 100.0) == pytest.approx(2.0, rel=1e-6)


def test_price_vs_vwap_pct_below():
    """가격 99, VWAP 100 → -1%."""
    assert price_vs_vwap_pct(99.0, 100.0) == pytest.approx(-1.0, rel=1e-6)


def test_price_vs_vwap_pct_nan_vwap():
    assert price_vs_vwap_pct(100.0, float("nan")) != price_vs_vwap_pct(100.0, float("nan"))


def test_price_vs_vwap_pct_zero_or_negative_guards():
    assert price_vs_vwap_pct(100.0, 0.0) != price_vs_vwap_pct(100.0, 0.0)
    assert price_vs_vwap_pct(0.0, 100.0) != price_vs_vwap_pct(0.0, 100.0)
    assert price_vs_vwap_pct(-1.0, 100.0) != price_vs_vwap_pct(-1.0, 100.0)


# ── compute_minute_ma / price_vs_ma_pct (round 24, P0-2) ─────────────────────


def test_minute_ma_5_simple_average():
    """1분봉 close [100, 102, 104, 106, 108] → MA5 = 104."""
    bars = _ohlcv_bars([
        (100, 100, 100, 100, 1),
        (100, 100, 100, 102, 1),
        (100, 100, 100, 104, 1),
        (100, 100, 100, 106, 1),
        (100, 100, 100, 108, 1),
    ])
    ma = compute_minute_ma(bars, window_minutes=5)
    assert ma == pytest.approx(104.0, rel=1e-6)


def test_minute_ma_uses_only_last_window():
    """6개 봉 중 마지막 5개만 사용."""
    bars = _ohlcv_bars([
        (100, 100, 100, 1000, 1),  # 폐기되어야 함
        (100, 100, 100, 100, 1),
        (100, 100, 100, 102, 1),
        (100, 100, 100, 104, 1),
        (100, 100, 100, 106, 1),
        (100, 100, 100, 108, 1),
    ])
    ma = compute_minute_ma(bars, window_minutes=5)
    assert ma == pytest.approx(104.0, rel=1e-6)


def test_minute_ma_insufficient_bars_returns_nan():
    """5개 미만이면 NaN."""
    bars = _ohlcv_bars([
        (100, 100, 100, 100, 1),
        (100, 100, 100, 102, 1),
    ])
    ma = compute_minute_ma(bars, window_minutes=5)
    assert ma != ma


def test_minute_ma_empty_returns_nan():
    bars = pd.DataFrame(columns=["close"])
    ma = compute_minute_ma(bars, window_minutes=5)
    assert ma != ma


def test_minute_ma_missing_close_column_returns_nan():
    bars = pd.DataFrame([{"open": 100, "high": 100, "low": 100, "volume": 1}])
    ma = compute_minute_ma(bars, window_minutes=5)
    assert ma != ma


def test_minute_ma_20_window():
    """MA20 = 20분 close 평균. 일정 가격이면 그 값."""
    bars = _ohlcv_bars([(100, 100, 100, 105, 1)] * 20)
    ma = compute_minute_ma(bars, window_minutes=20)
    assert ma == pytest.approx(105.0, rel=1e-6)


def test_price_vs_ma_pct_above():
    assert price_vs_ma_pct(105.0, 100.0) == pytest.approx(5.0, rel=1e-6)


def test_price_vs_ma_pct_below():
    assert price_vs_ma_pct(98.0, 100.0) == pytest.approx(-2.0, rel=1e-6)


def test_price_vs_ma_pct_guards():
    """NaN / 0 / 음수 가드."""
    assert price_vs_ma_pct(100.0, float("nan")) != price_vs_ma_pct(100.0, float("nan"))
    assert price_vs_ma_pct(100.0, 0.0) != price_vs_ma_pct(100.0, 0.0)
    assert price_vs_ma_pct(-1.0, 100.0) != price_vs_ma_pct(-1.0, 100.0)
