"""src.data.tick_log — Phase 1 로깅 인프라 단위 테스트."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from src.data.tick_log import (
    TickLogRow,
    TradeEvent,
    append_tick_log,
    append_trade_event,
    build_tick_log_row,
)
from src.data.tick_log_compact import compact_tick_logs, compact_trades
from src.dashboard.state import MonitoredStock


@pytest.fixture
def tmp_data_dir(monkeypatch, tmp_path):
    """모든 tick_log/trade 파일을 tmp_path 로 격리."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return tmp_path


# ── build_tick_log_row ───────────────────────────────────────────────────────


def _monitored(code="091340", name="대한광통신", is_auto=True):
    return MonitoredStock(
        code=code, name=name, is_auto=is_auto,
        added_at=datetime(2026, 5, 18, 9, 0),
        buy_score=5.5, buy_grade="STRONG",
        buy_reasons=["+1 회전율", "+2 가속"],
    )


def test_build_row_full_signals():
    """모든 시그널이 들어간 monitored 종목."""
    now = datetime(2026, 5, 18, 9, 32, 18)
    row = build_tick_log_row(
        now=now, code="091340", name="대한광통신",
        monitored=_monitored(),
        snap_row={"price": 91300, "prev_close": 70200, "daily_return": 30.0,
                  "is_limit_up": True, "turnover": 18.3,
                  "trading_value": 124_700_000_000, "rank": 1,
                  "intraday_high": 91500},
        bars_present=True,
        accel_5m=5.5, accel_1m=4.2,
        recent_bar_value=5_000_000_000, last_bar_value=1_000_000_000,
        candle=None,
        vp_now=142.0, vp_5ma=138.0, vp_1ma=135.0,
        ccnl={"buy_ratio": 60.0},
        asking={"bid_total_volume": 320_000, "ask_total_volume": 45_000,
                "bid_ask_ratio": 7.1, "bid1_price": 91200, "ask1_price": 91300},
        investor={"foreign_net_buy": 18000, "institution_net_buy": -8000,
                  "individual_net_buy": 0, "program_net_buy": 30000,
                  "foreign_net_buy_value": 1_500_000_000,
                  "institution_net_buy_value": -800_000_000},
        investor_delta={"foreign_value": 300_000_000, "institution_value": -50_000_000,
                        "program_qty": 2_500, "elapsed_sec": 47},
        vwap_pct=1.2, ma5_pct=0.8, ma20_pct=2.5,
        divergence=None,
        volume_ratio=3.5, limit_up_hit_time=None,
        trigger_states={"E1_vp_below_100": False, "E2_bearish_divergence": False,
                        "E3_vol_drain": False, "E4_bearish_candle": False},
        funnel_evaluated=True,
    )
    assert row.code == "091340"
    assert row.ts == "2026-05-18T09:32:18"
    assert row.is_auto is True
    assert row.is_holding is False
    assert row.price == 91300
    assert row.daily_return == 30.0
    assert row.is_limit_up is True
    assert row.vp == 142.0
    assert row.vp_5ma == 138.0
    assert row.foreign_net_buy_value == 1_500_000_000
    assert row.investor_delta_elapsed_sec == 47
    assert row.buy_score == 5.5
    assert row.buy_grade == "STRONG"
    assert row.buy_reasons == ["+1 회전율", "+2 가속"]
    assert row.funnel_evaluated is True
    assert row.funnel_passed_rising is False  # is_rising=False (monitored 는 is_auto)


def test_build_row_nan_safe():
    """NaN/None 인자도 안전하게 None 으로 변환."""
    row = build_tick_log_row(
        now=datetime(2026, 5, 18, 9, 0), code="999999", name="비-monitored",
        monitored=MonitoredStock(code="999999", name="x", added_at=datetime(2026, 5, 18, 9, 0)),
        snap_row=None, bars_present=False,
        accel_5m=float("nan"), accel_1m=float("nan"),
        recent_bar_value=None, last_bar_value=None,
        candle=None,
        vp_now=float("nan"), vp_5ma=float("nan"), vp_1ma=float("nan"),
        ccnl=None, asking=None, investor=None, investor_delta=None,
        vwap_pct=float("nan"), ma5_pct=float("nan"), ma20_pct=float("nan"),
        divergence=None,
        volume_ratio=float("nan"), limit_up_hit_time=None,
        trigger_states={},
        funnel_evaluated=False,
    )
    assert row.price is None
    assert row.vol_accel_5m is None
    assert row.vp is None
    assert row.foreign_net_buy_value is None
    assert row.buy_score is None
    assert row.funnel_evaluated is False
    assert row.funnel_passed_rising is False


def test_build_row_holding_mode():
    """보유 모드 — entry_price / elapsed / pnl 채움."""
    now = datetime(2026, 5, 18, 9, 32, 18)
    entry = datetime(2026, 5, 18, 9, 20, 0)

    class _Holding:
        entry_price = 91300.0
        entry_time = entry
        def pnl_pct(self, cur):
            return (cur - self.entry_price) / self.entry_price * 100.0
    h = _Holding()

    row = build_tick_log_row(
        now=now, code="091340", name="대한광통신",
        monitored=_monitored(),
        snap_row={"price": 92500, "daily_return": 31.8},
        bars_present=True, accel_5m=1.3, accel_1m=0.8,
        recent_bar_value=0, last_bar_value=0,
        candle=None, vp_now=float("nan"), vp_5ma=float("nan"), vp_1ma=float("nan"),
        ccnl=None, asking=None, investor=None, investor_delta=None,
        vwap_pct=float("nan"), ma5_pct=float("nan"), ma20_pct=float("nan"),
        divergence=None, volume_ratio=float("nan"), limit_up_hit_time=None,
        trigger_states={"E1_vp_below_100": True},
        funnel_evaluated=False, holding=h,
    )
    assert row.is_holding is True
    assert row.holding_entry_price == 91300
    assert row.holding_entry_time == entry.isoformat()
    assert row.holding_elapsed_sec == 12 * 60 + 18
    assert row.holding_pnl_pct is not None and row.holding_pnl_pct > 0
    assert row.trigger_e1_vp_below_100 is True


# ── intraday_high_override (2026-05-21, KIS stck_hgpr=0 결함 회피) ────────────


def _base_args_for_override_test():
    """override 테스트용 공통 인자 묶음 — snap_row 의 intraday_high 만 다르게."""
    return dict(
        now=datetime(2026, 5, 21, 9, 30),
        code="017900", name="광전자",
        monitored=MonitoredStock(code="017900", name="광전자", added_at=datetime(2026, 5, 21, 9, 0)),
        bars_present=True,
        accel_5m=1.5, accel_1m=2.0,
        recent_bar_value=0, last_bar_value=0,
        candle=None, vp_now=120.0, vp_5ma=115.0, vp_1ma=118.0,
        ccnl=None, asking=None, investor=None, investor_delta=None,
        vwap_pct=1.0, ma5_pct=0.5, ma20_pct=2.0,
        divergence=None, volume_ratio=1.5, limit_up_hit_time=None,
        trigger_states={},
        funnel_evaluated=True,
    )


def test_intraday_high_override_used_when_snap_zero():
    """KIS stck_hgpr=0 응답 시 worker fallback 값을 override 로 받아서 저장."""
    row = build_tick_log_row(
        snap_row={"price": 15390, "intraday_high": 0},  # KIS 결함 — 0 응답
        intraday_high_override=15450,                    # worker bars fallback
        **_base_args_for_override_test(),
    )
    assert row.intraday_high == 15450


def test_intraday_high_snap_value_kept_when_no_override():
    """기존 동작 — override 미지정 시 snap_row 값 그대로 (회귀 안전망)."""
    row = build_tick_log_row(
        snap_row={"price": 91300, "intraday_high": 91500},
        **_base_args_for_override_test(),
    )
    assert row.intraday_high == 91500


def test_intraday_high_override_none_falls_back_to_snap():
    """override=None 명시 시도 snap_row 값 사용."""
    row = build_tick_log_row(
        snap_row={"price": 91300, "intraday_high": 91500},
        intraday_high_override=None,
        **_base_args_for_override_test(),
    )
    assert row.intraday_high == 91500


def test_intraday_high_override_zero_ignored():
    """override 가 0 (양수 아님) 이면 무시하고 snap_row 값 사용 — 양쪽 다 0 이면 0/None."""
    row_with_snap = build_tick_log_row(
        snap_row={"price": 91300, "intraday_high": 91500},
        intraday_high_override=0,
        **_base_args_for_override_test(),
    )
    assert row_with_snap.intraday_high == 91500


# ── append_tick_log ──────────────────────────────────────────────────────────


def test_append_tick_log_creates_jsonl(tmp_data_dir):
    now = datetime(2026, 5, 18, 9, 32, 18)
    rows = [
        TickLogRow(ts=now.isoformat(), code="091340", name="대한광통신",
                   is_auto=True, price=91300),
        TickLogRow(ts=now.isoformat(), code="075180", name="제룡전기",
                   is_auto=True, price=90000),
    ]
    append_tick_log(rows, now)
    path = tmp_data_dir / "tick_logs" / "raw" / "2026-05-18.jsonl"
    assert path.exists()
    lines = path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    r0 = json.loads(lines[0])
    assert r0["code"] == "091340"
    assert r0["price"] == 91300


def test_append_tick_log_appends_to_same_file(tmp_data_dir):
    """같은 일자 두 번 append 시 누적."""
    now = datetime(2026, 5, 18, 9, 32, 18)
    append_tick_log(
        [TickLogRow(ts=now.isoformat(), code="A", name="A")],
        now,
    )
    append_tick_log(
        [TickLogRow(ts=now.isoformat(), code="B", name="B")],
        now,
    )
    path = tmp_data_dir / "tick_logs" / "raw" / "2026-05-18.jsonl"
    assert len(path.read_text(encoding="utf-8").strip().split("\n")) == 2


def test_append_tick_log_empty_noop(tmp_data_dir):
    """빈 list 는 파일 생성도 X."""
    append_tick_log([], datetime(2026, 5, 18, 9, 0))
    assert not (tmp_data_dir / "tick_logs" / "raw" / "2026-05-18.jsonl").exists()


# ── trade event ──────────────────────────────────────────────────────────────


def test_append_trade_event(tmp_data_dir):
    now = datetime(2026, 5, 18, 14, 35, 0)
    append_trade_event(
        TradeEvent(ts=now.isoformat(), code="091340", name="대한광통신",
                   action="buy", price=91300, source="command"),
        now,
    )
    path = tmp_data_dir / "trades" / "2026-05-18.jsonl"
    assert path.exists()
    ev = json.loads(path.read_text(encoding="utf-8").strip())
    assert ev["action"] == "buy"
    assert ev["price"] == 91300


def test_append_trade_event_sell_with_trigger(tmp_data_dir):
    now = datetime(2026, 5, 18, 14, 35, 0)
    append_trade_event(
        TradeEvent(ts=now.isoformat(), code="091340", name="x", action="sell",
                   price=92500, source="command",
                   trigger_fired="A1_stop_price,E1_vp_below_100"),
        now,
    )
    path = tmp_data_dir / "trades" / "2026-05-18.jsonl"
    ev = json.loads(path.read_text(encoding="utf-8").strip())
    assert ev["action"] == "sell"
    assert "A1_stop_price" in ev["trigger_fired"]


# ── compact (jsonl → parquet) ────────────────────────────────────────────────


def test_compact_tick_logs_to_parquet(tmp_data_dir):
    now = datetime(2026, 5, 18, 9, 32, 18)
    rows = [
        TickLogRow(ts=now.isoformat(), code="091340", name="대한광통신",
                   is_auto=True, price=91300, buy_score=5.5, buy_grade="STRONG",
                   buy_reasons=["+1 회전율", "+2 가속"]),
        TickLogRow(ts=now.isoformat(), code="075180", name="제룡전기",
                   is_auto=True, price=90000, buy_score=3.0, buy_grade="WATCH"),
    ]
    append_tick_log(rows, now)
    out = compact_tick_logs(now.date(), delete_raw=False)
    assert out is not None
    assert out.exists()
    assert out.name == "2026-05-18.parquet"
    df = pd.read_parquet(out)
    assert len(df) == 2
    assert set(df["code"]) == {"091340", "075180"}
    assert df.loc[df["code"] == "091340", "buy_score"].iloc[0] == 5.5
    # 원본 jsonl 은 그대로 (delete_raw=False)
    assert (tmp_data_dir / "tick_logs" / "raw" / "2026-05-18.jsonl").exists()


def test_compact_with_delete_raw(tmp_data_dir):
    now = datetime(2026, 5, 18, 9, 32, 18)
    append_tick_log(
        [TickLogRow(ts=now.isoformat(), code="A", name="A")],
        now,
    )
    out = compact_tick_logs(now.date(), delete_raw=True)
    assert out is not None and out.exists()
    assert not (tmp_data_dir / "tick_logs" / "raw" / "2026-05-18.jsonl").exists()


def test_compact_missing_jsonl_returns_none(tmp_data_dir):
    from datetime import date as d
    assert compact_tick_logs(d(2026, 1, 1), delete_raw=False) is None


def test_compact_trades(tmp_data_dir):
    now = datetime(2026, 5, 18, 14, 35, 0)
    append_trade_event(
        TradeEvent(ts=now.isoformat(), code="091340", name="x", action="buy",
                   price=91300, source="command"),
        now,
    )
    out = compact_trades(now.date())
    assert out is not None and out.exists()
    df = pd.read_parquet(out)
    assert len(df) == 1
    assert df["action"].iloc[0] == "buy"
