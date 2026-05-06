"""src.jongbae.historical 테스트."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from src.jongbae.historical import (
    close_position,
    has_enough_samples,
    historical_4layer,
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
    """Layer 3: +29.5%↑ 중 종가 위치 ±2% 일치만."""
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
