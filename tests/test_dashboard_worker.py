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


def test_rising_funnel_filters_heunga_haewoon():
    """round 21 회귀 — 흥아해운 시나리오는 funnel 통과 X (RISING 카드 X).

    입력: 거래대금 1위 / 회전율 19.4% / 5분봉가속 0.1 / 음봉 윗꼬리 큰 봉 / VP 95.
    round 21~33: Stage 2 hard-fail 로 drop.
    round 37 (현재): hard-fail 폐지 → R14 음수 합산 (vol_accel weak -3 + 음봉 -2 +
    VP_WEAK -2) 으로 RISING_MIN_SCORE=2.0 미달 → 자연 drop. 검증 결과는 동일.
    """
    from src.dashboard.worker import _evaluate_rising_funnel

    snap = {
        "rank": 1, "code": "003280", "name": "흥아해운",
        "price": 2825, "intraday_high": 2900,
        "turnover": 19.4, "trading_value": 131_600_000_000,
        "daily_return": 1.6,
    }
    snap_by_code = {"003280": snap}
    stage1 = [{"code": "003280", "name": "흥아해운", "themes": ["해운"],
               "turnover": 19.4, "rank": 1}]

    # 5분봉 가속 0.8 = 임계 미달 (RISING_STAGE2_VOL_ACCEL_MIN). vol_accel_5m 계산을
    # 직접 임의 값으로 만들려면 mock 필요 — fetch_minute_bars 가 윗꼬리 큰 음봉 +
    # 거래대금 감소 데이터를 반환하도록 패치.
    weak_bars = pd.DataFrame([
        # 30분 baseline 평균을 만들기 위한 더미 (5분당 큰 거래대금)
        *[{"open": 2900, "high": 2920, "low": 2880, "close": 2910,
           "trading_value": 5_000_000_000} for _ in range(6)],
        # 최근 5분 — 거래대금 급감 + 음봉 윗꼬리 50%
        *[{"open": 2900, "high": 2950, "low": 2820, "close": 2825,
           "trading_value": 500_000_000} for _ in range(5)],
    ])
    tick_cache: dict = {}
    with patch("src.dashboard.worker.fetch_minute_bars", return_value=weak_bars), \
         patch("src.dashboard.worker.fetch_ccnl_strength",
               return_value={"ccnl_strength": 95.0}), \
         patch("src.dashboard.worker.fetch_asking_price", return_value=None), \
         patch("src.dashboard.worker.fetch_investor_flow", return_value=None):
        result = _evaluate_rising_funnel(stage1, MagicMock(), snap_by_code, tick_cache)
    # Stage 2 에서 vol_accel_5m ≈ 0.1 (500M / 5G 평균) → 임계 0.8 미달 → drop
    # 또는 윗꼬리 큰 음봉 → is_weak_candle drop. 어떤 단계든 통과 불가.
    assert result == [], f"흥아해운이 funnel 통과해서 RISING 후보로 잡힘: {result}"


def test_rising_funnel_passes_strong_candidate():
    """모멘텀 + VP 강한 종목은 Stage 4 풀스코어까지 통과해 buy_score 가 채워진다."""
    from src.dashboard.worker import _evaluate_rising_funnel

    snap = {
        "rank": 3, "code": "091340", "name": "대한광통신",
        "price": 91300, "intraday_high": 91500,
        "turnover": 12.0, "trading_value": 80_000_000_000,
        "daily_return": 15.0,
    }
    snap_by_code = {"091340": snap}
    stage1 = [{"code": "091340", "name": "대한광통신", "themes": ["AI"],
               "turnover": 12.0, "rank": 3}]

    # 강한 모멘텀 + 깨끗한 양봉 + 거래대금 가속
    strong_bars = pd.DataFrame([
        # baseline (30분 평균 작음)
        *[{"open": 90000, "high": 90100, "low": 89900, "close": 90000,
           "trading_value": 1_000_000_000} for _ in range(6)],
        # 최근 5분 — 양봉 + 거래대금 5배 가속
        *[{"open": 90500, "high": 91500, "low": 90400, "close": 91300,
           "trading_value": 5_000_000_000} for _ in range(5)],
    ])
    tick_cache: dict = {}
    with patch("src.dashboard.worker.fetch_minute_bars", return_value=strong_bars), \
         patch("src.dashboard.worker.fetch_ccnl_strength",
               return_value={"ccnl_strength": 142.0, "buy_ratio": 60.0}), \
         patch("src.dashboard.worker.fetch_asking_price",
               return_value={"bid_ask_ratio": 2.5, "bid_total_volume": 0,
                             "ask_total_volume": 0}), \
         patch("src.dashboard.worker.fetch_investor_flow",
               return_value={"foreign_net_buy": 1000, "institution_net_buy": 500,
                             "program_net_buy": 300}):
        result = _evaluate_rising_funnel(stage1, MagicMock(), snap_by_code, tick_cache)
    assert len(result) == 1
    assert result[0]["code"] == "091340"
    assert result[0]["buy_score"] >= 2.0  # WATCH 이상
    assert result[0]["buy_grade"] in ("STRONG", "WATCH")
    assert "091340" in tick_cache
    assert "bars" in tick_cache["091340"]


def test_dashboard_tick_populates_last_payloads():
    """M7: tick 마다 monitored 종목별 PWA payload 가 session.last_payloads 에 채워짐."""
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
            now=now,
        )
    assert "005930" in s.last_payloads
    payload = s.last_payloads["005930"]
    assert payload["code"] == "005930"
    assert payload["price"]["current"] == 79000
    assert s.last_payload_ts == now


def test_dashboard_tick_cleans_stale_payloads():
    """monitored 에서 빠진 종목은 last_payloads 에서도 즉시 정리."""
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    # 이전 tick 에서 남은 stale 페이로드 시뮬레이션
    s.last_payloads["999999"] = {"code": "999999", "name": "stale"}
    msg_ids: dict = {}

    with patch("src.dashboard.worker.fetch_volume_rank", return_value=_empty_snapshot()):
        dashboard_tick(
            session=s, message_ids=msg_ids,
            client=MagicMock(), master_df=pd.DataFrame(),
            theme_mapping_df=pd.DataFrame(),
            daily_ohlcv=None,
            token="t", chat_id="c",
            now=now,
        )
    # snapshot empty 라 early return — 정리 로직은 못 돈다. 이건 의도적
    # (paused/empty 시엔 last_payloads 보존). 다음 케이스가 실제 cleanup 검증.

    # 실제 cleanup: 모니터링 종목 있을 때 빠진 stale 만 제거
    s.last_payloads = {"999999": {"code": "999999"}, "005930": {"code": "005930"}}
    s.add_manual("005930", now)
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
            now=now,
        )
    assert "999999" not in s.last_payloads
    assert "005930" in s.last_payloads


def test_cleanup_messages_clears_last_payloads():
    """모니터링 종료(cleanup_messages) 시 last_payloads 도 함께 비움."""
    s = MonitoringSession()
    s.last_payloads = {"005930": {"code": "005930"}}
    msg_ids = {"005930": 1}
    with patch("src.dashboard.worker.delete_message", return_value=True):
        cleanup_messages(
            token="t", chat_id="c", session=s, message_ids=msg_ids,
        )
    assert s.last_payloads == {}


def test_rising_funnel_passes_when_vp_data_missing():
    """round 33: KIS cttr 응답이 NaN/None 이어도 후보 drop 하지 않음.

    배경: 사용자가 30분간 RISING 카드 0건. 진단: KIS ccnl 응답에서 cttr 가 빈
    문자열 → _to_float 가 NaN 반환. 이전 Stage 3 는 NaN 도 hard-fail 로 drop.
    fix: NaN/None 은 Stage 4 풀스코어로 통과시키고 VP 가산점만 0 처리.

    여기서는 모멘텀/봉/거래대금이 강해 VP 가산 없이도 R14 ≥ 2.0 통과해야 함.
    """
    from src.dashboard.worker import _evaluate_rising_funnel

    snap = {
        "rank": 3, "code": "091340", "name": "대한광통신",
        "price": 91300, "intraday_high": 91500,
        "turnover": 12.0, "trading_value": 80_000_000_000,
        "daily_return": 15.0,
    }
    snap_by_code = {"091340": snap}
    stage1 = [{"code": "091340", "name": "대한광통신", "themes": ["AI"],
               "turnover": 12.0, "rank": 3}]

    strong_bars = pd.DataFrame([
        *[{"open": 90000, "high": 90100, "low": 89900, "close": 90000,
           "trading_value": 1_000_000_000} for _ in range(6)],
        *[{"open": 90500, "high": 91500, "low": 90400, "close": 91300,
           "trading_value": 5_000_000_000} for _ in range(5)],
    ])
    tick_cache: dict = {}
    # ccnl 응답에 cttr 가 NaN — KIS 빈 응답 시뮬레이션
    with patch("src.dashboard.worker.fetch_minute_bars", return_value=strong_bars), \
         patch("src.dashboard.worker.fetch_ccnl_strength",
               return_value={"ccnl_strength": float("nan")}), \
         patch("src.dashboard.worker.fetch_asking_price",
               return_value={"bid_ask_ratio": 2.5, "bid_total_volume": 0,
                             "ask_total_volume": 0}), \
         patch("src.dashboard.worker.fetch_investor_flow", return_value=None):
        result = _evaluate_rising_funnel(stage1, MagicMock(), snap_by_code, tick_cache)
    assert len(result) == 1, "VP NaN 종목이 Stage 3 에서 drop 됨 — fix 회귀"
    assert result[0]["code"] == "091340"


def test_rising_funnel_low_vp_alone_no_longer_hard_drops():
    """round 37: VP 85 (명시적 낮음) 도 다른 강한 시그널이면 통과 — false negative 회피.

    round 33/34: VP < 100 hard-fail drop.
    round 37: hard-fail 폐지 → VP 약함은 R14 -2 만 음수 가산. 양봉 + 가속 5배 + 회전율
    상위 등 다른 양수 시그널이 충분하면 R14 ≥ 2.0 으로 통과 가능. 사용자(Zeta) 통찰:
    "Stage 2/3 값이 R14 에 이미 있으니 굳이 hard-fail 시킬 필요 없음" → false negative
    축소.
    """
    from src.dashboard.worker import _evaluate_rising_funnel

    snap = {
        "rank": 3, "code": "091340", "name": "대한광통신",
        "price": 91300, "intraday_high": 91500,
        "turnover": 12.0, "trading_value": 80_000_000_000,
        "daily_return": 15.0,
    }
    snap_by_code = {"091340": snap}
    stage1 = [{"code": "091340", "name": "대한광통신", "themes": ["AI"],
               "turnover": 12.0, "rank": 3}]
    strong_bars = pd.DataFrame([
        *[{"open": 90000, "high": 90100, "low": 89900, "close": 90000,
           "trading_value": 1_000_000_000} for _ in range(6)],
        *[{"open": 90500, "high": 91500, "low": 90400, "close": 91300,
           "trading_value": 5_000_000_000} for _ in range(5)],
    ])
    tick_cache: dict = {}
    with patch("src.dashboard.worker.fetch_minute_bars", return_value=strong_bars), \
         patch("src.dashboard.worker.fetch_ccnl_strength",
               return_value={"ccnl_strength": 85.0}), \
         patch("src.dashboard.worker.fetch_asking_price",
               return_value={"bid_ask_ratio": 2.5, "bid_total_volume": 0,
                             "ask_total_volume": 0}), \
         patch("src.dashboard.worker.fetch_investor_flow", return_value=None):
        result = _evaluate_rising_funnel(stage1, MagicMock(), snap_by_code, tick_cache)
    # round 37: VP 85 만으로 hard-drop 안 함. 다른 강한 시그널이 양수 합산으로 통과.
    assert len(result) == 1, (
        f"VP 85 + 강한 다른 시그널 종목이 funnel 에서 drop 됨 — "
        f"round 37 의 false negative 회피 의도와 어긋남: {result}"
    )


def test_grade_assigned_to_manual_stock_outside_top50():
    """round 35: 수동 종목이 거래대금 50위 밖 (snap_by_code 에 없음) 이어도
    bars 데이터만 있으면 monitored.buy_grade 채워야 함. 사용자 보고: "수동
    모니터링해도 등급 안 뜸" 회귀."""
    from src.dashboard.state import MonitoringSession
    s = MonitoringSession()
    # 수동 종목 추가 — snap 에는 안 들어감 (top 50 밖)
    s.add_manual("999000", datetime(2026, 5, 11, 9, 30))

    # snap 에는 다른 종목만 (top 50 안)
    snap = pd.DataFrame([{
        "rank": 1, "code": "075180", "name": "제룡전기", "price": 91300,
        "prev_close": 70200, "daily_return": 30.0, "intraday_high": 91500,
        "intraday_low": 70300, "volume": 1_000_000,
        "trading_value": 100_000_000_000, "is_limit_up": True,
        "market_cap": 5_000, "turnover": 20.0,
    }])
    # bars 강한 양봉 — 수동 종목 999000 에 대해
    strong_bars = pd.DataFrame([
        *[{"open": 5000, "high": 5050, "low": 4990, "close": 5000,
           "trading_value": 500_000_000} for _ in range(6)],
        *[{"open": 5200, "high": 5300, "low": 5180, "close": 5280,
           "trading_value": 2_500_000_000} for _ in range(5)],
    ])

    msg_ids: dict = {}
    with patch("src.dashboard.worker.fetch_volume_rank", return_value=snap), \
         patch("src.dashboard.worker.score_leading_sectors", return_value=[]), \
         patch("src.dashboard.worker.identify_early_morning_leaders", return_value=[]), \
         patch("src.dashboard.worker.identify_rising_candidates", return_value=[]), \
         patch("src.dashboard.worker.fetch_minute_bars", return_value=strong_bars), \
         patch("src.dashboard.worker.fetch_ccnl_strength",
               return_value={"ccnl_strength": 135.0, "buy_ratio": float("nan")}), \
         patch("src.dashboard.worker.fetch_asking_price",
               return_value={"bid_ask_ratio": 1.2, "bid_total_volume": 0,
                             "ask_total_volume": 0,
                             "bid1_price": 5280, "ask1_price": 5290,
                             "bid1_volume": 0, "ask1_volume": 0}), \
         patch("src.dashboard.worker.fetch_investor_flow", return_value=None), \
         patch("src.dashboard.worker.send_message_single",
               return_value={"ok": True, "result": {"message_id": 1}}), \
         patch("src.dashboard.worker.edit_message"):
        dashboard_tick(
            session=s, message_ids=msg_ids,
            client=MagicMock(), master_df=pd.DataFrame(),
            theme_mapping_df=pd.DataFrame(),
            daily_ohlcv=None,
            token="t", chat_id="c",
            now=datetime(2026, 5, 11, 9, 30),
        )
    # 수동 종목 grade 가 채워졌는지
    m = s.monitored["999000"]
    assert m.buy_grade is not None, "snap_row 없는 수동 종목에 등급이 안 채워짐"
    assert m.buy_score is not None
    assert m.buy_grade in ("STRONG", "WATCH", "NEUTRAL", "AVOID")


def test_grade_assigned_to_holding_stock_outside_top50():
    """보유 종목이 거래대금 50위 밖이어도 등급 표시."""
    from src.dashboard.state import MonitoringSession
    from src.jongbae.exit_triggers import Holding

    s = MonitoringSession()
    s.add_manual("888000", datetime(2026, 5, 11, 9, 30))

    snap = pd.DataFrame([{
        "rank": 1, "code": "075180", "name": "제룡전기", "price": 91300,
        "prev_close": 70200, "daily_return": 30.0, "intraday_high": 91500,
        "intraday_low": 70300, "volume": 1_000_000,
        "trading_value": 100_000_000_000, "is_limit_up": True,
        "market_cap": 5_000, "turnover": 20.0,
    }])
    bars = pd.DataFrame([
        *[{"open": 3000, "high": 3010, "low": 2990, "close": 3000,
           "trading_value": 200_000_000} for _ in range(6)],
        *[{"open": 3050, "high": 3100, "low": 3030, "close": 3080,
           "trading_value": 600_000_000} for _ in range(5)],
    ])

    holding = Holding(
        code="888000", entry_price=3000,
        entry_time=datetime(2026, 5, 11, 9, 20),
        high_since_entry=3100,
    )
    msg_ids: dict = {}
    with patch("src.dashboard.worker.fetch_volume_rank", return_value=snap), \
         patch("src.dashboard.worker.score_leading_sectors", return_value=[]), \
         patch("src.dashboard.worker.identify_early_morning_leaders", return_value=[]), \
         patch("src.dashboard.worker.identify_rising_candidates", return_value=[]), \
         patch("src.dashboard.worker.fetch_minute_bars", return_value=bars), \
         patch("src.dashboard.worker.fetch_ccnl_strength",
               return_value={"ccnl_strength": 115.0, "buy_ratio": float("nan")}), \
         patch("src.dashboard.worker.fetch_asking_price", return_value=None), \
         patch("src.dashboard.worker.fetch_investor_flow", return_value=None), \
         patch("src.dashboard.worker.load_holdings",
               return_value={"888000": holding}), \
         patch("src.dashboard.worker.send_message_single",
               return_value={"ok": True, "result": {"message_id": 1}}), \
         patch("src.dashboard.worker.edit_message"):
        dashboard_tick(
            session=s, message_ids=msg_ids,
            client=MagicMock(), master_df=pd.DataFrame(),
            theme_mapping_df=pd.DataFrame(),
            daily_ohlcv=None,
            token="t", chat_id="c",
            now=datetime(2026, 5, 11, 9, 30),
        )
    m = s.monitored["888000"]
    assert m.buy_grade is not None, "보유 종목에 등급이 안 채워짐"


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


# ── 수동 모니터링 종목 name 해상 회귀 ────────────────────────────────────────
# 사용자 보고 (2026-05-18): 수동 등록 종목의 종목명이 카드에 안 뜨고 코드 그대로
# 표시됨. add_manual() 가 name=code 로 박은 후 갱신 경로가 없었음.
# fix: 매 tick monitored 루프 전 snap_by_code → master_df fallback 으로 보완.

def _stub_fetches():
    return (
        patch("src.dashboard.worker.fetch_minute_bars", return_value=pd.DataFrame()),
        patch("src.dashboard.worker.fetch_ccnl_strength", return_value=None),
        patch("src.dashboard.worker.fetch_asking_price", return_value=None),
        patch("src.dashboard.worker.fetch_investor_flow", return_value=None),
        patch("src.dashboard.worker.send_message_single",
              return_value={"ok": True, "result": {"message_id": 1}}),
        patch("src.dashboard.worker.edit_message"),
    )


def test_manual_name_resolved_from_snapshot():
    """수동 등록 종목이 거래대금 50위 안에 있으면 snap 에서 name 끌어옴."""
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    s.add_manual("005930", now)
    assert s.monitored["005930"].name == "005930"  # add_manual 직후엔 code

    snap = pd.DataFrame([{
        "rank": 1, "code": "005930", "name": "삼성전자",
        "price": 79000, "prev_close": 78000, "daily_return": 1.28,
        "intraday_high": 79100, "intraday_low": 78900,
        "volume": 100_000, "trading_value": 50_000_000_000,
        "is_limit_up": False, "market_cap": 4_800_000, "turnover": 0.1,
    }])

    mb, cc, ap, iv, sm, em = _stub_fetches()
    with patch("src.dashboard.worker.fetch_volume_rank", return_value=snap), \
         patch("src.dashboard.worker.score_leading_sectors", return_value=[]), \
         patch("src.dashboard.worker.identify_early_morning_leaders", return_value=[]), \
         mb, cc, ap, iv, sm, em:
        dashboard_tick(
            session=s, message_ids={}, client=MagicMock(),
            master_df=pd.DataFrame(),
            theme_mapping_df=pd.DataFrame(),
            daily_ohlcv=None,
            token="t", chat_id="c", now=now,
        )

    assert s.monitored["005930"].name == "삼성전자"


def test_manual_name_resolved_from_master_df_when_outside_top50():
    """수동 등록 종목이 거래대금 50위 밖이라 snap 에 없어도, master_df 에서 name 끌어옴.

    중소형주 수동 모니터링 시나리오 — snap_by_code 미스 → master_df 의 KRX
    전종목 마스터에서 name 해상. add_manual 의 name=code 가 그대로 표시되던 버그
    회귀 방지.
    """
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    s.add_manual("123456", now)

    # snap 에는 다른 종목만 (50위 안에 등록 종목 없음)
    snap = pd.DataFrame([{
        "rank": 1, "code": "005930", "name": "삼성전자",
        "price": 79000, "prev_close": 78000, "daily_return": 1.28,
        "intraday_high": 79100, "intraday_low": 78900,
        "volume": 100_000, "trading_value": 50_000_000_000,
        "is_limit_up": False, "market_cap": 4_800_000, "turnover": 0.1,
    }])
    # master_df 에 수동 등록 종목 row 있음
    master = pd.DataFrame([
        {"code": "123456", "name": "테스트종목", "market": "KOSDAQ",
         "group_code": "S", "market_cap": 1000, "listed_at": "20200101"},
        {"code": "005930", "name": "삼성전자", "market": "KOSPI",
         "group_code": "S", "market_cap": 4_800_000, "listed_at": "19750101"},
    ])

    mb, cc, ap, iv, sm, em = _stub_fetches()
    with patch("src.dashboard.worker.fetch_volume_rank", return_value=snap), \
         patch("src.dashboard.worker.score_leading_sectors", return_value=[]), \
         patch("src.dashboard.worker.identify_early_morning_leaders", return_value=[]), \
         mb, cc, ap, iv, sm, em:
        dashboard_tick(
            session=s, message_ids={}, client=MagicMock(),
            master_df=master,
            theme_mapping_df=pd.DataFrame(),
            daily_ohlcv=None,
            token="t", chat_id="c", now=now,
        )

    assert s.monitored["123456"].name == "테스트종목"


def test_manual_themes_resolved_from_theme_mapping_df():
    """수동 등록 종목의 테마가 theme_mapping_df 에서 채워짐 (auto/rising 아닌
    종목은 자체 themes 채울 데이터 소스 없으니 매 tick 이 기회).
    """
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    s.add_manual("123456", now)
    assert s.monitored["123456"].themes == []

    snap = pd.DataFrame([{
        "rank": 1, "code": "005930", "name": "삼성전자",
        "price": 79000, "prev_close": 78000, "daily_return": 1.28,
        "intraday_high": 79100, "intraday_low": 78900,
        "volume": 100_000, "trading_value": 50_000_000_000,
        "is_limit_up": False, "market_cap": 4_800_000, "turnover": 0.1,
    }])
    master = pd.DataFrame([
        {"code": "123456", "name": "테스트종목", "market": "KOSDAQ",
         "group_code": "S", "market_cap": 1000, "listed_at": "20200101"},
    ])
    theme_map = pd.DataFrame([
        {"code": "123456", "theme": "전기/전선"},
        {"code": "123456", "theme": "원자력"},
    ])

    mb, cc, ap, iv, sm, em = _stub_fetches()
    with patch("src.dashboard.worker.fetch_volume_rank", return_value=snap), \
         patch("src.dashboard.worker.score_leading_sectors", return_value=[]), \
         patch("src.dashboard.worker.identify_early_morning_leaders", return_value=[]), \
         mb, cc, ap, iv, sm, em:
        dashboard_tick(
            session=s, message_ids={}, client=MagicMock(),
            master_df=master,
            theme_mapping_df=theme_map,
            daily_ohlcv=None,
            token="t", chat_id="c", now=now,
        )

    assert set(s.monitored["123456"].themes) == {"전기/전선", "원자력"}


def test_manual_price_synthesized_from_bars_when_outside_top50():
    """50위 밖 수동 종목: bars 마지막 close 로 합성된 snap_row 가 message 에 가격
    표시되도록. render 가 snap_row 만 보므로 합성하지 않으면 "가격/회전율: —" 출력.
    """
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    s.add_manual("123456", now)

    snap = pd.DataFrame([{
        "rank": 1, "code": "005930", "name": "삼성전자",
        "price": 79000, "prev_close": 78000, "daily_return": 1.28,
        "intraday_high": 79100, "intraday_low": 78900,
        "volume": 100_000, "trading_value": 50_000_000_000,
        "is_limit_up": False, "market_cap": 4_800_000, "turnover": 0.1,
    }])
    master = pd.DataFrame([
        {"code": "123456", "name": "테스트종목", "market": "KOSDAQ",
         "group_code": "S", "market_cap": 1000, "listed_at": "20200101"},
    ])
    # 분봉 1개 — 5000원 + 거래대금 1억
    fake_bars = pd.DataFrame([{
        "time": "0930", "open": 4900, "high": 5100, "low": 4850,
        "close": 5000, "volume": 20_000, "trading_value": 100_000_000,
    }])

    sm_resp = {"ok": True, "result": {"message_id": 999}}
    with patch("src.dashboard.worker.fetch_volume_rank", return_value=snap), \
         patch("src.dashboard.worker.score_leading_sectors", return_value=[]), \
         patch("src.dashboard.worker.identify_early_morning_leaders", return_value=[]), \
         patch("src.dashboard.worker.identify_rising_candidates", return_value=[]), \
         patch("src.dashboard.worker.fetch_minute_bars", return_value=fake_bars), \
         patch("src.dashboard.worker.fetch_ccnl_strength", return_value=None), \
         patch("src.dashboard.worker.fetch_asking_price", return_value=None), \
         patch("src.dashboard.worker.fetch_investor_flow", return_value=None), \
         patch("src.dashboard.worker.send_message_single", return_value=sm_resp) as sm, \
         patch("src.dashboard.worker.edit_message"):
        dashboard_tick(
            session=s, message_ids={}, client=MagicMock(),
            master_df=master, theme_mapping_df=pd.DataFrame(),
            daily_ohlcv=None,
            token="t", chat_id="c", now=now,
        )

    # send_message_single 호출 시 두 번째 위치 인자(text) 확인.
    assert sm.call_count == 1
    sent_text = sm.call_args.args[2] if len(sm.call_args.args) >= 3 else sm.call_args.kwargs.get("text", "")
    assert "5,000원" in sent_text, f"합성 가격 누락: {sent_text}"
    assert "가격/회전율: —" not in sent_text, f"합성 실패하여 fallback 라인 출력됨: {sent_text}"


def test_manual_turnover_computed_from_market_cap():
    """50위 밖 수동 종목: 회전율 = bars 일일 거래대금 / master_df market_cap.
    회전율 계산식: (trading_value 원) / (market_cap 억원 × 1e8) × 100.
    bars 합계 1억원 / 시총 1000억 = 0.1% 회전율.
    """
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    s.add_manual("123456", now)

    snap = pd.DataFrame([{
        "rank": 1, "code": "005930", "name": "삼성전자",
        "price": 79000, "prev_close": 78000, "daily_return": 1.28,
        "intraday_high": 79100, "intraday_low": 78900,
        "volume": 100_000, "trading_value": 50_000_000_000,
        "is_limit_up": False, "market_cap": 4_800_000, "turnover": 0.1,
    }])
    master = pd.DataFrame([
        {"code": "123456", "name": "테스트종목", "market": "KOSDAQ",
         "group_code": "S", "market_cap": 1000, "listed_at": "20200101"},
    ])
    fake_bars = pd.DataFrame([{
        "time": "0930", "open": 4900, "high": 5100, "low": 4850,
        "close": 5000, "volume": 20_000, "trading_value": 100_000_000,
    }])

    sm_resp = {"ok": True, "result": {"message_id": 999}}
    with patch("src.dashboard.worker.fetch_volume_rank", return_value=snap), \
         patch("src.dashboard.worker.score_leading_sectors", return_value=[]), \
         patch("src.dashboard.worker.identify_early_morning_leaders", return_value=[]), \
         patch("src.dashboard.worker.identify_rising_candidates", return_value=[]), \
         patch("src.dashboard.worker.fetch_minute_bars", return_value=fake_bars), \
         patch("src.dashboard.worker.fetch_ccnl_strength", return_value=None), \
         patch("src.dashboard.worker.fetch_asking_price", return_value=None), \
         patch("src.dashboard.worker.fetch_investor_flow", return_value=None), \
         patch("src.dashboard.worker.send_message_single", return_value=sm_resp) as sm, \
         patch("src.dashboard.worker.edit_message"):
        dashboard_tick(
            session=s, message_ids={}, client=MagicMock(),
            master_df=master, theme_mapping_df=pd.DataFrame(),
            daily_ohlcv=None,
            token="t", chat_id="c", now=now,
        )

    sent_text = sm.call_args.args[2] if len(sm.call_args.args) >= 3 else sm.call_args.kwargs.get("text", "")
    # 회전율 0.10% (= 1e8 / (1000 * 1e8) * 100)
    assert "회전율" in sent_text and "0.1" in sent_text, f"회전율 누락: {sent_text}"
    # 거래대금 1억 → 0.01억원 표시? _fmt_billion 동작 확인
    assert "거래대금" in sent_text


def test_manual_name_keeps_code_when_unknown_everywhere():
    """snap / master 모두 모르는 종목코드 → code 그대로 (fail-safe)."""
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    s.add_manual("999999", now)

    snap = pd.DataFrame([{
        "rank": 1, "code": "005930", "name": "삼성전자",
        "price": 79000, "prev_close": 78000, "daily_return": 1.28,
        "intraday_high": 79100, "intraday_low": 78900,
        "volume": 100_000, "trading_value": 50_000_000_000,
        "is_limit_up": False, "market_cap": 4_800_000, "turnover": 0.1,
    }])
    master = pd.DataFrame([
        {"code": "005930", "name": "삼성전자", "market": "KOSPI",
         "group_code": "S", "market_cap": 4_800_000, "listed_at": "19750101"},
    ])

    mb, cc, ap, iv, sm, em = _stub_fetches()
    with patch("src.dashboard.worker.fetch_volume_rank", return_value=snap), \
         patch("src.dashboard.worker.score_leading_sectors", return_value=[]), \
         patch("src.dashboard.worker.identify_early_morning_leaders", return_value=[]), \
         mb, cc, ap, iv, sm, em:
        dashboard_tick(
            session=s, message_ids={}, client=MagicMock(),
            master_df=master,
            theme_mapping_df=pd.DataFrame(),
            daily_ohlcv=None,
            token="t", chat_id="c", now=now,
        )

    assert s.monitored["999999"].name == "999999"
