"""src.notify.telegram_bot 단위 테스트."""
from __future__ import annotations

from datetime import datetime

from src.dashboard.state import MonitoringSession, Source
from src.notify.telegram_bot import apply_command, parse_command


# ── parse_command ────────────────────────────────────────────────────────────


def test_parse_pause():
    assert parse_command("/pause").kind == "pause"
    assert parse_command(" /PAUSE ").kind == "pause"
    assert parse_command("/start").kind == "pause"


def test_parse_list():
    assert parse_command("/list").kind == "list"


def test_parse_clear():
    assert parse_command("/clear").kind == "clear"


def test_parse_six_digit_code():
    cmd = parse_command("091340")
    assert cmd.kind == "toggle_code"
    assert cmd.code == "091340"


def test_parse_invalid_code():
    assert parse_command("12345").kind == "unknown"
    assert parse_command("ABCDEF").kind == "unknown"


def test_parse_unknown_text():
    assert parse_command("안녕하세요").kind == "unknown"


def test_parse_empty():
    assert parse_command("").kind == "ignore"


# ── apply_command ────────────────────────────────────────────────────────────


def test_apply_pause_toggles():
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    msg1 = apply_command(parse_command("/pause"), s, now)
    assert s.paused is True
    assert "OFF" in msg1
    msg2 = apply_command(parse_command("/pause"), s, now)
    assert s.paused is False
    assert "ON" in msg2


def test_apply_list():
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    s.add_manual("005930", now)
    msg = apply_command(parse_command("/list"), s, now)
    assert "005930" in msg


def test_apply_clear():
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    s.add_manual("005930", now)
    s.add_manual("000660", now)
    msg = apply_command(parse_command("/clear"), s, now)
    assert "2개" in msg
    assert "005930" not in s.monitored


def test_apply_toggle_code_in_window():
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)  # 평일 09:30
    msg = apply_command(parse_command("005930"), s, now)
    assert "005930" in msg
    assert "005930" in s.monitored
    assert s.monitored["005930"].source == Source.MANUAL


def test_apply_toggle_code_out_of_window_returns_notice():
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 14, 30)  # 평일이지만 14:30
    msg = apply_command(parse_command("005930"), s, now)
    assert "장 시간 외" in msg
    assert "005930" not in s.monitored


def test_apply_toggle_code_weekend_returns_notice():
    s = MonitoringSession()
    now = datetime(2026, 5, 9, 9, 30)  # 토요일
    msg = apply_command(parse_command("005930"), s, now)
    assert "장 시간 외" in msg


def test_apply_unknown_returns_empty():
    s = MonitoringSession()
    msg = apply_command(parse_command("아무 텍스트"), s, datetime.now())
    assert msg == ""


def test_apply_ignore_empty():
    s = MonitoringSession()
    msg = apply_command(parse_command(""), s, datetime.now())
    assert msg == ""


def test_apply_pause_works_outside_window():
    """/pause 는 시간 무관하게 동작."""
    s = MonitoringSession()
    msg = apply_command(parse_command("/pause"), s, datetime(2026, 5, 9, 23, 0))
    assert s.paused is True
