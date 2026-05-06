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
    (9, 30, True),     # 사용자 요청 확장 영역
    (9, 59, True),     # 종료 직전
    (10, 0, False),    # 종료
    (10, 30, False),
])
def test_within_early_morning_extended(hh, mm, expected):
    """09:00 ≤ t < 10:00 (사용자 요청에 따라 1시간 확장)."""
    dt = datetime(2026, 5, 6, hh, mm, tzinfo=KST)
    assert scheduler._within_early_morning(dt) is expected


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
        "afterhours", "limit_up_poll",
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
