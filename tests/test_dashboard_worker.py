"""src.dashboard.worker 통합 테스트.

KIS 호출 + 텔레그램 호출 모두 mock. tick 핵심 흐름 검증.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from unittest.mock import MagicMock, patch

import pandas as pd

from src.dashboard.state import MonitoringSession, Source
from src.dashboard.worker import (
    cleanup_messages,
    dashboard_tick,
    reset_daily,
    start_command_thread,
)


def _empty_snapshot():
    return pd.DataFrame(columns=[
        "rank", "code", "name", "price", "prev_close", "daily_return",
        "intraday_high", "intraday_low", "volume", "trading_value",
        "is_limit_up", "market_cap", "turnover",
    ])


def test_dashboard_tick_skips_when_paused():
    s = MonitoringSession()
    s.paused = True
    msg_ids: dict = {}
    with patch("src.dashboard.worker.fetch_volume_rank") as fvr:
        dashboard_tick(
            session=s, message_ids=msg_ids,
            client=MagicMock(), master_df=pd.DataFrame(),
            theme_mapping_df=pd.DataFrame(),
            daily_ohlcv=None,
            token="t", chat_id="c",
            now=datetime(2026, 5, 11, 9, 30),
        )
    fvr.assert_not_called()


def test_dashboard_tick_24h_no_window_guard():
    """round 18: in_monitoring_window 가드 폐지. 운영시간 외에도 paused=False면 tick 실행."""
    s = MonitoringSession()
    msg_ids: dict = {}
    with patch("src.dashboard.worker.fetch_volume_rank", return_value=_empty_snapshot()) as fvr:
        dashboard_tick(
            session=s, message_ids=msg_ids,
            client=MagicMock(), master_df=pd.DataFrame(),
            theme_mapping_df=pd.DataFrame(),
            daily_ohlcv=None,
            token="t", chat_id="c",
            now=datetime(2026, 5, 11, 14, 0),  # 14:00 — 이전엔 스킵, 이제는 fetch 호출됨
        )
    fvr.assert_called_once()


def test_dashboard_tick_empty_snapshot_returns_early():
    s = MonitoringSession()
    msg_ids: dict = {}
    with patch("src.dashboard.worker.fetch_volume_rank", return_value=_empty_snapshot()):
        with patch("src.dashboard.worker.score_leading_sectors") as score:
            dashboard_tick(
                session=s, message_ids=msg_ids,
                client=MagicMock(), master_df=pd.DataFrame(),
                theme_mapping_df=pd.DataFrame(),
                daily_ohlcv=None,
                token="t", chat_id="c",
                now=datetime(2026, 5, 11, 9, 30),
            )
            score.assert_not_called()


def test_dashboard_tick_sends_new_monitor_message():
    """주도주 자동 추가 시 send_message_single 으로 새 메시지 — message_id 저장."""
    s = MonitoringSession()
    msg_ids: dict = {}

    snap = pd.DataFrame([{
        "rank": 1, "code": "075180", "name": "제룡전기",
        "price": 91300, "prev_close": 70200, "daily_return": 30.0,
        "intraday_high": 91500, "intraday_low": 70200,
        "volume": 1_000_000, "trading_value": 100_000_000_000,
        "is_limit_up": True, "market_cap": 5_000, "turnover": 20.0,
    }])

    sectors = [{
        "theme": "전기/전선", "score": 3.0, "breadth": 1, "avg_return": 30.0,
        "turnover_sum": 20.0, "member_count": 1, "codes": ["075180"], "count": 1,
    }]
    leaders = [{
        "code": "075180", "name": "제룡전기", "themes": ["전기/전선"],
        "rank": 1, "price": 91300, "daily_return": 30.0, "is_limit_up": True,
        "turnover": 20.0, "trading_value": 100_000_000_000, "market_cap": 5_000,
    }]

    fake_resp = {"ok": True, "result": {"message_id": 4242}}
    with patch("src.dashboard.worker.fetch_volume_rank", return_value=snap), \
         patch("src.dashboard.worker.score_leading_sectors", return_value=sectors), \
         patch("src.dashboard.worker.identify_early_morning_leaders", return_value=leaders), \
         patch("src.dashboard.worker.fetch_minute_bars", return_value=pd.DataFrame()), \
         patch("src.dashboard.worker.fetch_ccnl_strength", return_value=None), \
         patch("src.dashboard.worker.fetch_asking_price", return_value=None), \
         patch("src.dashboard.worker.fetch_investor_flow", return_value=None), \
         patch("src.dashboard.worker.send_message_single", return_value=fake_resp) as send, \
         patch("src.dashboard.worker.edit_message") as edit:
        dashboard_tick(
            session=s, message_ids=msg_ids,
            client=MagicMock(), master_df=pd.DataFrame(),
            theme_mapping_df=pd.DataFrame(),
            daily_ohlcv=None,
            token="t", chat_id="c",
            now=datetime(2026, 5, 11, 9, 30),
        )

    # 종목 추가 + 메시지 ID 저장
    assert "075180" in s.monitored
    assert msg_ids.get("075180") == 4242
    # send 가 호출되었고 (최초이므로) edit 은 호출 안 됨
    assert send.call_count >= 1


def test_dashboard_tick_edits_existing_message():
    """이미 message_id 가 있으면 edit 호출, send 호출 X."""
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    s.add_manual("005930", now)
    msg_ids = {"005930": 100}

    snap = pd.DataFrame([{
        "rank": 1, "code": "005930", "name": "삼성전자",
        "price": 79000, "prev_close": 78000, "daily_return": 1.28,
        "intraday_high": 79100, "intraday_low": 78900,
        "volume": 100_000, "trading_value": 50_000_000_000,
        "is_limit_up": False, "market_cap": 4_800_000, "turnover": 0.1,
    }])

    with patch("src.dashboard.worker.fetch_volume_rank", return_value=snap), \
         patch("src.dashboard.worker.score_leading_sectors", return_value=[]), \
         patch("src.dashboard.worker.identify_early_morning_leaders", return_value=[]), \
         patch("src.dashboard.worker.fetch_minute_bars", return_value=pd.DataFrame()), \
         patch("src.dashboard.worker.fetch_ccnl_strength", return_value=None), \
         patch("src.dashboard.worker.fetch_asking_price", return_value=None), \
         patch("src.dashboard.worker.fetch_investor_flow", return_value=None), \
         patch("src.dashboard.worker.send_message_single") as send, \
         patch("src.dashboard.worker.edit_message") as edit:
        dashboard_tick(
            session=s, message_ids=msg_ids,
            client=MagicMock(), master_df=pd.DataFrame(),
            theme_mapping_df=pd.DataFrame(),
            daily_ohlcv=None,
            token="t", chat_id="c",
            now=now,
        )
    assert edit.called
    assert not send.called


def test_dashboard_tick_updates_last_prices():
    """round 20: 매 tick 마다 session.last_prices 에 snapshot 현재가 채워짐.
    `/buy CODE` (가격 생략) UX 를 위한 inter-thread 시세 공유.
    """
    s = MonitoringSession()
    msg_ids: dict = {}
    snap = pd.DataFrame([{
        "rank": 1, "code": "091340", "name": "대한광통신",
        "price": 91400, "prev_close": 90000, "daily_return": 1.56,
        "intraday_high": 91500, "intraday_low": 89500,
        "volume": 500_000, "trading_value": 30_000_000_000,
        "is_limit_up": False, "market_cap": 1_000_000, "turnover": 5.0,
    }])
    with patch("src.dashboard.worker.fetch_volume_rank", return_value=snap), \
         patch("src.dashboard.worker.score_leading_sectors", return_value=[]), \
         patch("src.dashboard.worker.identify_early_morning_leaders", return_value=[]), \
         patch("src.dashboard.worker.identify_rising_candidates", return_value=[]), \
         patch("src.dashboard.worker.fetch_minute_bars", return_value=pd.DataFrame()), \
         patch("src.dashboard.worker.fetch_ccnl_strength", return_value=None), \
         patch("src.dashboard.worker.fetch_asking_price", return_value=None), \
         patch("src.dashboard.worker.fetch_investor_flow", return_value=None), \
         patch("src.dashboard.worker.send_message_single"), \
         patch("src.dashboard.worker.edit_message"):
        dashboard_tick(
            session=s, message_ids=msg_ids,
            client=MagicMock(), master_df=pd.DataFrame(),
            theme_mapping_df=pd.DataFrame(),
            daily_ohlcv=None,
            token="t", chat_id="c",
            now=datetime(2026, 5, 11, 9, 30),
        )
    assert s.last_prices.get("091340") == 91400.0


def test_cleanup_messages_deletes_all():
    s = MonitoringSession()
    s.add_manual("005930", datetime(2026, 5, 11, 9, 30))
    msg_ids = {"005930": 1, "000660": 2}
    with patch("src.dashboard.worker.delete_message", return_value=True) as dm:
        cleanup_messages(
            token="t", chat_id="c", session=s, message_ids=msg_ids,
        )
    assert dm.call_count == 2
    assert msg_ids == {}
    assert s.monitored == {}


def test_reset_daily_clears_state():
    s = MonitoringSession()
    s.paused = True
    s.add_manual("005930", datetime.now())
    reset_daily(s)
    assert s.paused is False
    assert s.monitored == {}


def test_command_poll_loop_stops_on_event():
    """getUpdates mock 가 빈 결과 반환, stop_event set 시 즉시 종료."""
    s = MonitoringSession()
    with patch("src.dashboard.worker.get_updates", return_value=[]):
        th, stop = start_command_thread(s, "t", "c")
        time.sleep(0.05)
        stop.set()
        th.join(timeout=2.0)
    assert not th.is_alive()
