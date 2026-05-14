"""src.dashboard.render 단위 테스트."""
from __future__ import annotations

from datetime import datetime

from src.dashboard.render import render_monitor_message
from src.dashboard.state import MonitoredStock, Source


def _stock(code: str = "075180", name: str = "제룡전기",
           source: Source = Source.AUTO,
           themes: list[str] | None = None) -> MonitoredStock:
    return MonitoredStock(
        code=code, name=name, source=source,
        added_at=datetime(2026, 5, 11, 9, 0),
        themes=themes or ["전기/전선"],
    )


def test_render_basic_fields():
    snap = {
        "price": 91300, "daily_return": 30.0, "is_limit_up": True,
        "turnover": 18.3, "trading_value": 124_700_000_000,
    }
    msg = render_monitor_message(
        _stock(),
        snap,
        accel_ratio=4.2,
        recent_bar_value=5_000_000_000,
        ccnl={"ccnl_strength": 142.0, "buy_ratio": 60.0},
        asking={"bid_total_volume": 320_000, "ask_total_volume": 45_000, "bid_ask_ratio": 7.1},
        investor={"foreign_net_buy": 1800, "institution_net_buy": 4200, "program_net_buy": 2500},
        sparkline="▁▂▃▅▇█",
        now=datetime(2026, 5, 11, 9, 32, 18),
    )
    assert "제룡전기" in msg
    assert "075180" in msg
    assert "⭐자동" in msg
    assert "🔴상한가" in msg
    assert "+30.0%" in msg
    assert "18.3%" in msg
    assert "4.2배" in msg
    assert "체결강도" in msg
    assert "142" in msg
    assert "외국인" in msg
    # sparkline 라인은 사용자 요청으로 render 에서 제거 (2026-05-13).
    assert "▁▂▃▅▇█" not in msg
    # 시각 + 가격은 한 줄로 합쳐졌고 구분선(─) 제거됨.
    assert "─" not in msg
    assert "09:32:18  91,300원" in msg


def test_render_manual_source():
    msg = render_monitor_message(
        _stock(source=Source.MANUAL),
        snapshot_row=None,
        accel_ratio=None, recent_bar_value=None,
        ccnl=None, asking=None, investor=None,
        sparkline="",
        now=datetime(2026, 5, 11, 9, 0),
    )
    assert "🔵수동" in msg


def test_render_with_grace_label():
    msg = render_monitor_message(
        _stock(),
        snapshot_row=None,
        accel_ratio=None, recent_bar_value=None,
        ccnl=None, asking=None, investor=None,
        sparkline="",
        now=datetime(2026, 5, 11, 9, 0),
        grace_remaining_seconds=272,
    )
    assert "GRACE" in msg
    assert "4:32" in msg


def test_render_acceleration_label():
    msg = render_monitor_message(
        _stock(),
        snapshot_row={"price": 1000, "daily_return": 5.0, "is_limit_up": False,
                      "turnover": 5.0, "trading_value": 1_000_000_000},
        accel_ratio=5.5, recent_bar_value=2_000_000_000,
        ccnl=None, asking=None, investor=None,
        sparkline="",
        now=datetime(2026, 5, 11, 9, 0),
    )
    assert "↑" in msg
    assert "가속" in msg


def test_render_exit_signal_label():
    msg = render_monitor_message(
        _stock(),
        snapshot_row={"price": 1000, "daily_return": -2.0, "is_limit_up": False,
                      "turnover": 5.0, "trading_value": 500_000_000},
        accel_ratio=0.4, recent_bar_value=200_000_000,
        ccnl=None, asking=None, investor=None,
        sparkline="",
        now=datetime(2026, 5, 11, 9, 0),
    )
    assert "↓" in msg
    assert "이탈" in msg


def test_render_handles_none_snapshot():
    """스냅샷 없는 경우에도 깨지지 않음."""
    msg = render_monitor_message(
        _stock(),
        snapshot_row=None,
        accel_ratio=None, recent_bar_value=None,
        ccnl=None, asking=None, investor=None,
        sparkline="",
        now=datetime(2026, 5, 11, 9, 0),
    )
    assert "—" in msg
    assert "제룡전기" in msg


def test_render_themes_slash_join():
    msg = render_monitor_message(
        _stock(themes=["전기/전선", "원자력", "AI데이터센터"]),
        snapshot_row=None,
        accel_ratio=None, recent_bar_value=None,
        ccnl=None, asking=None, investor=None,
        sparkline="",
        now=datetime(2026, 5, 11, 9, 0),
    )
    assert "전기/전선 / 원자력 / AI데이터센터" in msg
