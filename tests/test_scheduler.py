"""src.scheduler 단위 테스트.

스케줄러 자체는 데몬으로 실행해야 하므로 BlockingScheduler.start() 는
호출하지 않는다. 대신 잡 함수의 휴장일 가드, 폴링 시간창, 상태 리셋,
잡 등록 검증에 집중.
"""
from __future__ import annotations

from datetime import date, datetime, time
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
import pytz

from src import scheduler

KST = pytz.timezone("Asia/Seoul")


# ── 글로벌 상태 리셋 ─────────────────────────────────────────────────────────

def test_reset_state_clears_all():
    scheduler._already_limit_up = {"075180", "001440"}
    scheduler._watch_codes = ["A", "B"]
    scheduler._prev_leading_themes = [{"theme": "X"}]
    scheduler._reset_state()
    assert scheduler._already_limit_up == set()
    assert scheduler._watch_codes == []
    assert scheduler._prev_leading_themes == []


# ── 폴링 시간창 ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("hh,mm,expected", [
    (8, 30, False),    # 너무 이름
    (9, 4, False),     # 09:05 직전
    (9, 5, True),      # 시작
    (12, 0, True),     # 점심
    (15, 25, True),    # 종료
    (15, 26, False),   # 종료 직후
    (16, 0, False),    # 장 종료
])
def test_within_polling_window(hh, mm, expected):
    dt = datetime(2026, 5, 6, hh, mm, tzinfo=KST)
    assert scheduler._within_polling_window(dt) is expected


@pytest.mark.parametrize("hh,mm,expected", [
    (8, 59, False),
    (9, 0, True),      # 시작
    (9, 30, True),
    (10, 0, True),     # 사용자 요청에 따라 10:30 까지 확장
    (10, 30, True),    # 종료 시각 포함
    (10, 31, False),
])
def test_in_monitoring_window_m6(hh, mm, expected):
    """M6 dashboard 운영 시간 (09:00 ~ 10:30 평일).

    `_early_morning_check` 폐기 후 `dashboard.state.in_monitoring_window` 가 대체.
    """
    from datetime import datetime as _dt
    from src.dashboard.state import in_monitoring_window
    # 2026-05-11 월요일 (평일)
    dt = _dt(2026, 5, 11, hh, mm)
    assert in_monitoring_window(dt) is expected


# ── 휴장일 가드 데코레이터 ──────────────────────────────────────────────────

def test_business_day_only_skips_weekend():
    """토/일은 잡 함수 본체가 실행되지 않는다."""
    sat = datetime(2026, 5, 9, 14, 50, tzinfo=KST)  # 토요일
    called = []

    @scheduler._business_day_only("테스트")
    def fn():
        called.append(1)

    with patch("src.scheduler.now_kst", return_value=sat):
        fn()
    assert called == []


def test_business_day_only_runs_weekday():
    wed = datetime(2026, 5, 6, 14, 50, tzinfo=KST)  # 수요일
    called = []

    @scheduler._business_day_only("테스트")
    def fn():
        called.append(1)

    with patch("src.scheduler.now_kst", return_value=wed):
        fn()
    assert called == [1]


def test_business_day_only_swallows_exception():
    """잡 내부 예외가 스케줄러 자체를 죽이지 않아야 한다."""
    wed = datetime(2026, 5, 6, 14, 50, tzinfo=KST)

    @scheduler._business_day_only("테스트")
    def fn():
        raise RuntimeError("boom")

    # 예외 raise 안 됨
    with patch("src.scheduler.now_kst", return_value=wed):
        result = fn()
    assert result is None


def test_business_day_only_calls_dispatcher_error_alert_on_exception():
    """잡 인자에 dispatcher 가 있으면 에러 알림 호출."""
    from src.notify.dispatcher import Dispatcher
    wed = datetime(2026, 5, 6, 14, 50, tzinfo=KST)
    # _find_dispatcher 가 isinstance(_, Dispatcher) 로 찾으므로 spec 필요.
    disp = MagicMock(spec=Dispatcher)

    @scheduler._business_day_only("테스트")
    def fn(dispatcher):
        raise RuntimeError("boom")

    with patch("src.scheduler.now_kst", return_value=wed):
        fn(disp)
    disp.telegram_error.assert_called_once()


def test_business_day_only_silences_httpx_errors(tmp_path):
    """KIS HTTP 5xx 가 잡 본체에서 propagate 되도 텔레그램 / 누적 로그 둘 다 skip.

    fetcher 레벨 격리(intraday.py 등)가 미래에 깨져도 노이즈가 안 새는 safety net.
    """
    import httpx
    from src.config import Settings
    from src.notify.dispatcher import Dispatcher

    wed = datetime(2026, 5, 6, 14, 50, tzinfo=KST)
    disp = MagicMock(spec=Dispatcher)
    settings = MagicMock(spec=Settings)
    settings.data_dir = tmp_path

    req = httpx.Request("GET", "https://openapi.koreainvestment.com:9443/x")
    resp = httpx.Response(500, request=req, text="server error")
    http_err = httpx.HTTPStatusError("Server error '500'", request=req, response=resp)

    @scheduler._business_day_only("상한가 폴링")
    def fn(client, settings, dispatcher):
        raise http_err

    with patch("src.scheduler.now_kst", return_value=wed):
        result = fn(MagicMock(), settings, disp)

    assert result is None
    # 텔레그램 알림 호출 X (가장 중요)
    disp.telegram_error.assert_not_called()
    # 사후 레포트가 읽는 누적 로그도 X
    err_file = tmp_path / "errors" / "2026-05-06.jsonl"
    assert not err_file.exists()


def test_business_day_only_silences_httpx_transport_error(tmp_path):
    """네트워크 단절 (ConnectError 등 httpx.HTTPError 하위) 도 동일."""
    import httpx
    from src.config import Settings
    from src.notify.dispatcher import Dispatcher

    wed = datetime(2026, 5, 6, 14, 50, tzinfo=KST)
    disp = MagicMock(spec=Dispatcher)
    settings = MagicMock(spec=Settings)
    settings.data_dir = tmp_path

    @scheduler._business_day_only("상한가 폴링")
    def fn(client, settings, dispatcher):
        raise httpx.ConnectError("Connection refused")

    with patch("src.scheduler.now_kst", return_value=wed):
        fn(MagicMock(), settings, disp)

    disp.telegram_error.assert_not_called()


def test_enrich_candidates_fills_missing_ohlcv_from_fetch_quote():
    """KIS volume-rank 가 prev_close=0 / intraday_high=0 / trading_value=0 으로
    줘서 후보 dict 가 비어 있을 때 fetch_quote 결과로 보강. 2026-05-19 사용자 보고
    회귀 — 진원생명과학 011000 결정 레포트 표시 깨짐 ("0 → 1,366").
    """
    candidates = [{
        "code": "011000",
        "name": "진원생명과학",
        "price": 1366,
        "prev_close": 0,         # 누락
        "daily_return": 29.97,
        "intraday_high": 0,      # 누락
        "intraday_low": 0,       # 누락
        "trading_value": 0,      # 누락
        "volume": 0,             # 누락
        "rank": 11,
        "turnover": 12.5,        # snapshot 의 보존 필드
        "market_cap": 1234,      # snapshot 의 보존 필드
    }]
    fetch_result = {
        "code": "011000",
        "name": "진원생명과학",
        "price": 1366,
        "prev_close": 1051,
        "daily_return": 29.97,
        "intraday_high": 1366,
        "intraday_low": 1100,
        "volume": 5_000_000,
        "trading_value": 50_000_000_000,
        "is_limit_up": True,
        "market_cap": 0,         # fetch_quote 기본값
        "turnover": float("nan"),
    }
    with patch("src.data.intraday.fetch_quote", return_value=fetch_result):
        scheduler._enrich_candidates_with_quote(candidates, MagicMock())

    base = candidates[0]
    # 누락됐던 필드는 보강됨
    assert base["prev_close"] == 1051
    assert base["intraday_high"] == 1366
    assert base["intraday_low"] == 1100
    assert base["trading_value"] == 50_000_000_000
    assert base["volume"] == 5_000_000
    assert base["is_limit_up"] is True
    # intraday_high_pct 도 재계산 — (1366-1051)/1051 * 100 ≈ 29.97
    assert abs(base["intraday_high_pct"] - 29.97) < 0.5
    # snapshot 의 rank / turnover / market_cap 은 보존 (fetch_quote 기본값으로
    # 덮어쓰지 않음)
    assert base["rank"] == 11
    assert base["turnover"] == 12.5
    assert base["market_cap"] == 1234


def test_enrich_candidates_preserves_existing_values():
    """snapshot 에 값이 이미 있으면 fetch_quote 결과로 덮어쓰지 않는다."""
    candidates = [{
        "code": "075180",
        "price": 91300,
        "prev_close": 70230,     # 이미 있음
        "intraday_high": 91300,  # 이미 있음
        "trading_value": 400_000_000_000,
        "rank": 1,
    }]
    fetch_result = {
        "code": "075180",
        "prev_close": 99999,     # 다른 값 — snapshot 우선이라 무시돼야 함
        "intraday_high": 99999,
        "price": 91300,
        "trading_value": 1,
        "volume": 100,
        "is_limit_up": True,
    }
    with patch("src.data.intraday.fetch_quote", return_value=fetch_result):
        scheduler._enrich_candidates_with_quote(candidates, MagicMock())

    base = candidates[0]
    assert base["prev_close"] == 70230   # snapshot 유지
    assert base["intraday_high"] == 91300
    assert base["trading_value"] == 400_000_000_000


def test_enrich_candidates_handles_fetch_quote_none():
    """fetch_quote 가 None 반환 (HTTP 5xx 등) 해도 후보 dict 그대로 유지 — 크래시 X."""
    candidates = [{"code": "229200", "price": 0, "prev_close": 0}]
    with patch("src.data.intraday.fetch_quote", return_value=None):
        scheduler._enrich_candidates_with_quote(candidates, MagicMock())
    # 변경 없음
    assert candidates[0]["price"] == 0


def test_business_day_only_still_alerts_on_non_http_exception(tmp_path):
    """KISApiError / KeyError 등 non-HTTP 예외는 기존대로 fail-loud 유지."""
    from src.config import Settings
    from src.kis.client import KISApiError
    from src.notify.dispatcher import Dispatcher

    wed = datetime(2026, 5, 6, 14, 50, tzinfo=KST)
    disp = MagicMock(spec=Dispatcher)
    settings = MagicMock(spec=Settings)
    settings.data_dir = tmp_path

    @scheduler._business_day_only("상한가 폴링")
    def fn(client, settings, dispatcher):
        raise KISApiError("1", "ERR", "rt_cd 불량", {})

    with patch("src.scheduler.now_kst", return_value=wed):
        fn(MagicMock(), settings, disp)

    # 진짜 시스템 오류는 여전히 텔레그램 알림
    disp.telegram_error.assert_called_once()


# ── 잡 등록 ─────────────────────────────────────────────────────────────────

def test_run_registers_all_jobs(tmp_path, monkeypatch):
    """run() 이 등록해야 할 잡이 모두 add_job 으로 등록되는지 확인.

    실제 BlockingScheduler.start() 는 호출 안 함 (mock).
    """
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))

    fake_scheduler = MagicMock()
    # start() 가 즉시 리턴해서 run() 이 끝나도록.
    fake_scheduler.start.return_value = None

    with patch("src.scheduler._make_scheduler", return_value=fake_scheduler), \
         patch("src.scheduler.KISClient"), \
         patch("src.scheduler.Dispatcher"), \
         patch("src.scheduler.signal.signal"):
        scheduler.run()

    job_ids = [c.kwargs.get("id") for c in fake_scheduler.add_job.call_args_list]
    expected = {
        "state_reset", "morning",
        "snapshot_1100", "snapshot_1300", "snapshot_1400", "snapshot_1450",
        "afterhours", "index_daily_update", "limit_up_poll",
    }
    assert expected.issubset(set(job_ids)), f"누락된 잡: {expected - set(job_ids)}"


# ── _poll_limit_up: 시간창 밖이면 API 호출 없음 ─────────────────────────────

def test_poll_limit_up_skips_outside_window():
    """16:00 (장 종료 후) 에는 fetch 호출 안 함."""
    out_of_window = datetime(2026, 5, 6, 16, 0, tzinfo=KST)
    client = MagicMock()
    settings = MagicMock()
    dispatcher = MagicMock()
    scheduler._watch_codes = ["075180"]

    with patch("src.scheduler.now_kst", return_value=out_of_window), \
         patch("src.scheduler.detect_new_limit_up") as mock_detect:
        scheduler._poll_limit_up(client, settings, dispatcher)
    mock_detect.assert_not_called()


def test_poll_limit_up_runs_within_window():
    in_window = datetime(2026, 5, 6, 11, 30, tzinfo=KST)
    client = MagicMock()
    settings = MagicMock()
    dispatcher = MagicMock()
    scheduler._watch_codes = ["075180"]
    scheduler._already_limit_up = set()

    with patch("src.scheduler.now_kst", return_value=in_window), \
         patch("src.scheduler.detect_new_limit_up", return_value=([], set())) as mock_detect:
        scheduler._poll_limit_up(client, settings, dispatcher)
    mock_detect.assert_called_once()


def test_poll_limit_up_skips_when_no_watch_codes():
    in_window = datetime(2026, 5, 6, 11, 30, tzinfo=KST)
    scheduler._watch_codes = []

    with patch("src.scheduler.now_kst", return_value=in_window), \
         patch("src.scheduler.detect_new_limit_up") as mock_detect:
        scheduler._poll_limit_up(MagicMock(), MagicMock(), MagicMock())
    mock_detect.assert_not_called()
