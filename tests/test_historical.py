"""src.overnight.gap_stats 테스트."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from src.overnight.gap_stats import (
    _coerce_date,
    close_position,
    has_enough_samples,
    historical_4layer,
    market_regime_timeline,
    pick_sizing_layer,
)


def test_close_position_at_high():
    assert close_position(open_p=100, high=110, low=95, close=110) == 1.0


def test_close_position_at_low():
    assert close_position(open_p=100, high=110, low=95, close=95) == 0.0


def test_close_position_middle():
    assert close_position(100, 110, 90, 100) == pytest.approx(0.5)


def test_close_position_no_range():
    assert close_position(100, 100, 100, 100) == 0.5


# ── 4-Layer 통계 ─────────────────────────────────────────────────────────────

def _make_daily(records: list[dict]) -> pd.DataFrame:
    """간이 일봉 DF 생성 helper."""
    return pd.DataFrame(records)


def test_historical_4layer_basic_layer1():
    """Layer 1: 종목 X에 +20%↑ 사례 1건, 다음날 갭 +5%."""
    today = date(2026, 5, 6)
    rows = [
        # 2026-04-01: 평범
        {"code": "X", "date": date(2026, 4, 1),
         "open": 1000, "high": 1010, "low": 990, "close": 1000},
        # 2026-04-02: +25% (Layer 1 매칭)
        {"code": "X", "date": date(2026, 4, 2),
         "open": 1000, "high": 1250, "low": 1000, "close": 1250},
        # 2026-04-03: 다음날 시가 1300 (갭 +4%)
        {"code": "X", "date": date(2026, 4, 3),
         "open": 1300, "high": 1310, "low": 1290, "close": 1305},
    ]
    df = _make_daily(rows)
    result = historical_4layer(df, today_close_pos=1.0, today=today)

    assert result["layer1"]["n"] == 1
    assert result["layer1"]["p"] == 1.0  # 갭상 1/1
    assert result["layer1"]["avg_gap"] == pytest.approx(4.0, abs=0.01)


def test_historical_4layer_layer2_filters_below_29_5():
    """+25%는 Layer 2 미달, +30%만 Layer 2 매칭."""
    today = date(2026, 5, 6)
    rows = [
        {"code": "X", "date": date(2026, 4, 1),
         "open": 1000, "high": 1010, "low": 990, "close": 1000},
        {"code": "X", "date": date(2026, 4, 2),
         "open": 1000, "high": 1250, "low": 1000, "close": 1250},  # +25%
        {"code": "X", "date": date(2026, 4, 3),
         "open": 1300, "high": 1310, "low": 1290, "close": 1300},
        {"code": "Y", "date": date(2026, 4, 1),
         "open": 1000, "high": 1010, "low": 990, "close": 1000},
        {"code": "Y", "date": date(2026, 4, 2),
         "open": 1000, "high": 1300, "low": 1000, "close": 1300},  # +30%
        {"code": "Y", "date": date(2026, 4, 3),
         "open": 1320, "high": 1340, "low": 1310, "close": 1335},
    ]
    df = _make_daily(rows)
    result = historical_4layer(df, today_close_pos=1.0, today=today)
    assert result["layer1"]["n"] == 2
    assert result["layer2"]["n"] == 1


def test_historical_4layer_layer3_close_position_match():
    """Layer 3: +29.5%↑ 중 종가 위치 ±5% 일치만 (2026-05-22 ±2%→±5%)."""
    today = date(2026, 5, 6)
    rows = [
        # 종가 위치 1.0 (고가 마감)
        {"code": "A", "date": date(2026, 4, 1),
         "open": 1000, "high": 1010, "low": 990, "close": 1000},
        {"code": "A", "date": date(2026, 4, 2),
         "open": 1000, "high": 1300, "low": 1000, "close": 1300},  # close_pos = 1.0
        {"code": "A", "date": date(2026, 4, 3),
         "open": 1320, "high": 1340, "low": 1310, "close": 1335},
        # 종가 위치 0.5 (중간 마감)
        {"code": "B", "date": date(2026, 4, 1),
         "open": 1000, "high": 1010, "low": 990, "close": 1000},
        {"code": "B", "date": date(2026, 4, 2),
         "open": 1000, "high": 1500, "low": 1000, "close": 1300},  # +30%, close_pos = 0.6
        {"code": "B", "date": date(2026, 4, 3),
         "open": 1280, "high": 1300, "low": 1270, "close": 1290},
    ]
    df = _make_daily(rows)
    # 오늘 close_pos = 1.0 → A만 매칭
    result = historical_4layer(df, today_close_pos=1.0, today=today)
    assert result["layer2"]["n"] == 2
    assert result["layer3"]["n"] == 1


def test_historical_4layer_layer4_marked_v1():
    """Layer 4 는 분봉 미구현 안내."""
    today = date(2026, 5, 6)
    df = _make_daily([
        {"code": "X", "date": date(2026, 4, 1),
         "open": 1000, "high": 1010, "low": 990, "close": 1000},
    ])
    result = historical_4layer(df, today_close_pos=1.0, today=today)
    assert "note" in result["layer4"]
    assert "v1" in result["layer4"]["note"]


def test_historical_4layer_empty():
    today = date(2026, 5, 6)
    result = historical_4layer(pd.DataFrame(), today_close_pos=1.0, today=today)
    assert result["layer1"]["n"] == 0
    assert result["layer2"]["n"] == 0


def test_historical_4layer_excludes_today():
    """today 당일 데이터는 제외 (look-ahead 방지)."""
    today = date(2026, 5, 6)
    rows = [
        {"code": "X", "date": today,  # 오늘 — 제외돼야 함
         "open": 1000, "high": 1300, "low": 1000, "close": 1300},
        {"code": "X", "date": today + timedelta(days=1),
         "open": 1320, "high": 1340, "low": 1310, "close": 1335},
    ]
    df = _make_daily(rows)
    result = historical_4layer(df, today_close_pos=1.0, today=today)
    assert result["layer1"]["n"] == 0


# ── has_enough_samples / pick_sizing_layer ───────────────────────────────────

def test_has_enough_samples_true():
    assert has_enough_samples({"n": 5}) is True


def test_has_enough_samples_false():
    assert has_enough_samples({"n": 4}) is False


def test_pick_sizing_layer_prefers_layer3():
    layers = {
        "layer1": {"n": 100},
        "layer2": {"n": 50},
        "layer3": {"n": 10},
    }
    name, _ = pick_sizing_layer(layers)
    assert name == "layer3"


def test_pick_sizing_layer_fallback_to_layer2():
    layers = {
        "layer1": {"n": 100},
        "layer2": {"n": 50},
        "layer3": {"n": 3},
    }
    name, _ = pick_sizing_layer(layers)
    assert name == "layer2"


def test_pick_sizing_layer_fallback_to_layer1():
    layers = {
        "layer1": {"n": 100},
        "layer2": {"n": 4},
        "layer3": {"n": 0},
    }
    name, _ = pick_sizing_layer(layers)
    assert name == "layer1"


def test_pick_sizing_layer_all_insufficient():
    """전부 부족하면 layer1 반환 (호출부에서 has_enough_samples 재확인)."""
    layers = {
        "layer1": {"n": 2},
        "layer2": {"n": 1},
        "layer3": {"n": 0},
    }
    name, _ = pick_sizing_layer(layers)
    assert name == "layer1"


# ── 시장 국면 매칭 layer (Task C) ────────────────────────────────────────────

def test_coerce_date_python_date():
    assert _coerce_date(date(2026, 5, 6)) == date(2026, 5, 6)


def test_coerce_date_string_yyyymmdd():
    assert _coerce_date("20260506") == date(2026, 5, 6)


def test_coerce_date_string_dashed():
    assert _coerce_date("2026-05-06") == date(2026, 5, 6)


def test_coerce_date_invalid_returns_none():
    assert _coerce_date("garbage") is None
    assert _coerce_date(None) is None


def test_market_regime_timeline_above_below_ma():
    """KOSPI 일봉 시계열 → ma 위/아래 boolean dict."""
    closes = [100.0] * 5 + [110.0] * 4 + [120.0]  # 마지막 1개는 ma 위에 명확히
    rows = [
        {"date": date(2026, 1, 1) + timedelta(days=i), "close": c}
        for i, c in enumerate(closes)
    ]
    timeline = market_regime_timeline(pd.DataFrame(rows), ma_window=5)
    assert len(timeline) == 6
    assert timeline[date(2026, 1, 5)] is False  # close=100, ma=100, 위가 아님
    # 1/6: rolling [100,100,100,100,110]=102, close=110 > 102 → True
    assert timeline[date(2026, 1, 6)] is True
    # 1/10: rolling [110,110,110,110,120]=112, close=120 > 112 → True
    assert timeline[date(2026, 1, 10)] is True


def test_market_regime_timeline_too_short_returns_empty():
    df = pd.DataFrame([{"date": date(2026, 1, 1), "close": 100.0}])
    assert market_regime_timeline(df, ma_window=200) == {}


def test_market_regime_timeline_handles_string_dates():
    """KIS API 응답이 'YYYYMMDD' 문자열일 때도 동작."""
    rows = [
        {"date": f"202601{i+1:02d}", "close": 100.0 + i}
        for i in range(10)
    ]
    timeline = market_regime_timeline(pd.DataFrame(rows), ma_window=3)
    assert date(2026, 1, 10) in timeline


def test_historical_4layer_layer3_strong_mkt_match():
    """layer3 사례 중 매칭 날짜 regime이 오늘과 일치한 사례만."""
    today = date(2026, 5, 6)
    # A: 강세장 날(2026-04-02), B: 약세장 날(2026-04-09) — 두 사례 모두 +30%, close_pos=1.0
    rows = [
        {"code": "A", "date": date(2026, 4, 1), "open": 1000, "high": 1010, "low": 990, "close": 1000, "volume": 100},
        {"code": "A", "date": date(2026, 4, 2), "open": 1000, "high": 1300, "low": 1000, "close": 1300, "volume": 100},
        {"code": "A", "date": date(2026, 4, 3), "open": 1320, "high": 1340, "low": 1310, "close": 1335, "volume": 100},
        {"code": "B", "date": date(2026, 4, 8), "open": 1000, "high": 1010, "low": 990, "close": 1000, "volume": 100},
        {"code": "B", "date": date(2026, 4, 9), "open": 1000, "high": 1300, "low": 1000, "close": 1300, "volume": 100},
        {"code": "B", "date": date(2026, 4, 10), "open": 1320, "high": 1340, "low": 1310, "close": 1335, "volume": 100},
    ]
    regime = {
        date(2026, 4, 2): True,   # 강세장
        date(2026, 4, 9): False,  # 약세장
    }
    result = historical_4layer(
        pd.DataFrame(rows),
        today_close_pos=1.0,
        today=today,
        today_strong_market=True,
        market_regime_by_date=regime,
    )
    assert result["layer3"]["n"] == 2  # 두 사례 모두
    assert result["layer3_strong_mkt"]["n"] == 1  # 강세장 사례 1건만


def test_historical_4layer_skips_strong_mkt_when_no_regime():
    """today_strong_market 또는 regime dict 없으면 layer3_strong_mkt 슬롯 자체 부재."""
    today = date(2026, 5, 6)
    rows = [
        {"code": "A", "date": date(2026, 4, 1), "open": 1000, "high": 1010, "low": 990, "close": 1000, "volume": 100},
        {"code": "A", "date": date(2026, 4, 2), "open": 1000, "high": 1300, "low": 1000, "close": 1300, "volume": 100},
    ]
    result = historical_4layer(pd.DataFrame(rows), today_close_pos=1.0, today=today)
    assert "layer3_strong_mkt" not in result


# ── 거래량 비율 매칭 layer (Task D) ──────────────────────────────────────────

def test_historical_4layer_layer3_high_vol_match():
    """layer3 사례 중 volume_ratio가 오늘 ±tolerance 범위만 매칭."""
    today = date(2026, 5, 6)
    rows: list[dict] = []
    # 30일치 base (volume=100) — volume_ratio ≈ 1.0
    for i in range(30):
        rows.append({"code": "A", "date": date(2026, 3, 1) + timedelta(days=i),
                     "open": 1000, "high": 1010, "low": 990, "close": 1000, "volume": 100})
    # high-volume 사례: 직전 평균 100, 당일 700 → ratio ≈ 7
    rows.append({"code": "A", "date": date(2026, 4, 5),
                 "open": 1000, "high": 1300, "low": 1000, "close": 1300, "volume": 700})
    rows.append({"code": "A", "date": date(2026, 4, 6),  # 다음날 (gap 계산용)
                 "open": 1340, "high": 1360, "low": 1330, "close": 1350, "volume": 100})

    result = historical_4layer(
        pd.DataFrame(rows),
        today_close_pos=1.0,
        today=today,
        today_volume_ratio=7.0,
    )
    assert result["layer3"]["n"] >= 1
    assert result["layer3_high_vol"]["n"] == 1


def test_historical_4layer_skips_high_vol_when_no_ratio():
    today = date(2026, 5, 6)
    rows = [
        {"code": "A", "date": date(2026, 4, 1), "open": 1000, "high": 1010, "low": 990, "close": 1000, "volume": 100},
        {"code": "A", "date": date(2026, 4, 2), "open": 1000, "high": 1300, "low": 1000, "close": 1300, "volume": 100},
    ]
    result = historical_4layer(pd.DataFrame(rows), today_close_pos=1.0, today=today)
    assert "layer3_high_vol" not in result


# ── pick_sizing_layer 우선순위 (새 layer) ────────────────────────────────────

def test_pick_sizing_layer_prefers_strong_mkt_when_enough():
    layers = {
        "layer1": {"n": 100},
        "layer2": {"n": 50},
        "layer3": {"n": 20},
        "layer3_strong_mkt": {"n": 8},
        "layer3_high_vol": {"n": 10},
    }
    name, _ = pick_sizing_layer(layers)
    assert name == "layer3_strong_mkt"


def test_pick_sizing_layer_falls_back_to_high_vol_if_strong_mkt_insufficient():
    layers = {
        "layer1": {"n": 100},
        "layer2": {"n": 50},
        "layer3": {"n": 20},
        "layer3_strong_mkt": {"n": 3},  # 부족
        "layer3_high_vol": {"n": 10},
    }
    name, _ = pick_sizing_layer(layers)
    assert name == "layer3_high_vol"
