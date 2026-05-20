"""src.notify.telegram_bot 단위 테스트."""
from __future__ import annotations

from datetime import datetime

from src.dashboard.state import MonitoringSession, Source
from src.notify.telegram_bot import apply_command, parse_command


# ── parse_command ────────────────────────────────────────────────────────────


def test_parse_on_off_start():
    """round 18: /on, /off 정식 명령. /start = /on, /pause = /off alias."""
    assert parse_command("/on").kind == "on"
    assert parse_command(" /ON ").kind == "on"
    assert parse_command("/start").kind == "on"
    assert parse_command("/off").kind == "off"
    assert parse_command(" /OFF ").kind == "off"
    assert parse_command("/pause").kind == "off"


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


def test_apply_on_off_explicit():
    """/on /off 명시 명령 (round 18)."""
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    # 기본 ON 상태 — /on 은 "이미 ON"
    msg = apply_command(parse_command("/on"), s, now)
    assert s.paused is False
    assert "이미" in msg
    # /off
    msg = apply_command(parse_command("/off"), s, now)
    assert s.paused is True
    assert "OFF" in msg
    # /on 으로 복귀
    msg = apply_command(parse_command("/on"), s, now)
    assert s.paused is False
    assert "ON" in msg


def test_apply_start_is_on_alias():
    """/start 가 /on alias (이전엔 /pause 토글이었음, round 18)."""
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    s.set_off()  # OFF 상태로 만들고
    assert s.paused is True
    msg = apply_command(parse_command("/start"), s, now)
    assert s.paused is False
    assert "ON" in msg


def test_apply_pause_is_off_alias():
    """/pause 는 /off alias 로 흡수."""
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    msg = apply_command(parse_command("/pause"), s, now)
    assert s.paused is True
    assert "OFF" in msg


def test_apply_list():
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    s.add_manual("005930", now)
    msg = apply_command(parse_command("/list"), s, now)
    assert "005930" in msg


def test_apply_clear():
    """round 35: clear 는 manual flag 만 끔. entry 는 prune 단계에서 결정."""
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    s.add_manual("005930", now)
    s.add_manual("000660", now)
    msg = apply_command(parse_command("/clear"), s, now)
    assert "2개" in msg
    # 둘 다 manual flag 끔 — entry 는 남아있지만 prune 대상
    assert s.monitored["005930"].is_manual is False
    assert s.monitored["000660"].is_manual is False
    # prune 후엔 사라짐
    s.prune_empty(set())
    assert "005930" not in s.monitored
    assert "000660" not in s.monitored


def test_apply_toggle_code_in_window():
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    msg = apply_command(parse_command("005930"), s, now)
    assert "005930" in msg
    assert "005930" in s.monitored
    assert s.monitored["005930"].is_manual is True


def test_apply_toggle_code_24h_allowed():
    """24h 허용 (round 18) — 운영시간 외에도 종목 추가 가능."""
    s = MonitoringSession()
    # 평일 14:30
    msg = apply_command(parse_command("005930"), s, datetime(2026, 5, 11, 14, 30))
    assert "005930" in msg
    assert "005930" in s.monitored


def test_apply_toggle_code_weekend_allowed():
    """주말도 24h 허용 — KIS 시세는 변동 없지만 등록은 됨 (round 18)."""
    s = MonitoringSession()
    msg = apply_command(parse_command("000660"), s, datetime(2026, 5, 9, 9, 30))
    assert "000660" in msg
    assert "000660" in s.monitored


def test_apply_unknown_returns_empty():
    s = MonitoringSession()
    msg = apply_command(parse_command("아무 텍스트"), s, datetime.now())
    assert msg == ""


def test_apply_ignore_empty():
    s = MonitoringSession()
    msg = apply_command(parse_command(""), s, datetime.now())
    assert msg == ""


def test_apply_off_works_24h():
    """/off (= /pause alias) 는 시간 무관하게 동작."""
    s = MonitoringSession()
    msg = apply_command(parse_command("/off"), s, datetime(2026, 5, 9, 23, 0))
    assert s.paused is True


def test_apply_on_works_24h_outside_business_hours():
    """/on 도 24h 허용 — 주말/심야 임의 시점 (round 18)."""
    s = MonitoringSession()
    s.set_off()
    msg = apply_command(parse_command("/on"), s, datetime(2026, 5, 10, 3, 0))  # 일요일 새벽
    assert s.paused is False
    assert "ON" in msg


# ── /buy /sell /status (Exit.Triggers) ─────────────────────────────────────────────────


def test_parse_buy_basic():
    cmd = parse_command("/buy 091340 91300")
    assert cmd.kind == "buy"
    assert cmd.code == "091340"
    assert cmd.price == 91300.0
    assert cmd.time_stop_minutes is None


def test_parse_buy_code_only():
    """round 20: 가격 생략 시 None 으로 두고 apply 단에서 last_prices 보충."""
    cmd = parse_command("/buy 091340")
    assert cmd.kind == "buy"
    assert cmd.code == "091340"
    assert cmd.price is None
    assert cmd.time_stop_minutes is None


def test_parse_buy_with_time_override():
    cmd = parse_command("/buy 091340 91300 5")
    assert cmd.kind == "buy"
    assert cmd.time_stop_minutes == 5


def test_parse_buy_price_with_comma():
    cmd = parse_command("/buy 091340 91,300")
    assert cmd.kind == "buy"
    assert cmd.price == 91300.0


def test_parse_buy_missing_code():
    assert parse_command("/buy").kind == "unknown"


def test_parse_buy_invalid_code():
    assert parse_command("/buy 12345 100").kind == "unknown"


def test_parse_buy_invalid_price():
    assert parse_command("/buy 091340 abc").kind == "unknown"
    assert parse_command("/buy 091340 -100").kind == "unknown"


def test_parse_sell_basic():
    cmd = parse_command("/sell 091340")
    assert cmd.kind == "sell"
    assert cmd.code == "091340"


def test_parse_sell_invalid_code():
    assert parse_command("/sell 12345").kind == "unknown"
    assert parse_command("/sell").kind == "unknown"


def test_parse_status_basic():
    cmd = parse_command("/status 091340")
    assert cmd.kind == "status"
    assert cmd.code == "091340"


def test_apply_buy_24h_allowed(tmp_path, monkeypatch):
    """/buy 24h 허용 (round 18) — 운영시간 외에도 보유 모드 진입 가능.

    사용자가 NXT/장중 임의 시점에 매수했음을 봇에 알리는 용도.
    """
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import importlib
    import src.config
    importlib.reload(src.config)
    import src.scalping.exit.triggers as et
    importlib.reload(et)
    import src.notify.telegram_bot as bot
    importlib.reload(bot)
    s = MonitoringSession()
    # 평일 14:30 (운영시간 외)
    msg = bot.apply_command(
        bot.parse_command("/buy 091340 91300"),
        s,
        datetime(2026, 5, 11, 14, 30),
    )
    assert "장 시간 외" not in msg
    assert "091340" in msg


def test_apply_buy_creates_holding(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import importlib
    import src.config
    importlib.reload(src.config)
    import src.scalping.exit.triggers as et
    importlib.reload(et)
    import src.notify.telegram_bot as bot
    importlib.reload(bot)

    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    msg = bot.apply_command(bot.parse_command("/buy 091340 91300"), s, now)
    assert "보유 모드" in msg
    assert "91,300" in msg or "91300" in msg

    holdings = et.load_holdings()
    assert "091340" in holdings
    assert holdings["091340"].entry_price == 91_300
    assert holdings["091340"].time_stop_minutes == 10


def test_apply_buy_with_time_override(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import importlib
    import src.config
    importlib.reload(src.config)
    import src.scalping.exit.triggers as et
    importlib.reload(et)
    import src.notify.telegram_bot as bot
    importlib.reload(bot)

    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    bot.apply_command(bot.parse_command("/buy 091340 91300 5"), s, now)
    holdings = et.load_holdings()
    assert holdings["091340"].time_stop_minutes == 5


def test_apply_buy_code_only_uses_last_prices(tmp_path, monkeypatch):
    """round 20: `/buy CODE` 만 입력해도 session.last_prices 에서 매수가 자동 보충."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import importlib
    import src.config
    importlib.reload(src.config)
    import src.scalping.exit.triggers as et
    importlib.reload(et)
    import src.notify.telegram_bot as bot
    importlib.reload(bot)

    s = MonitoringSession()
    s.last_prices["091340"] = 91_400.0  # worker tick 이 채워둔 상태로 시뮬레이션
    now = datetime(2026, 5, 11, 9, 30)
    msg = bot.apply_command(bot.parse_command("/buy 091340"), s, now)
    assert "보유 모드" in msg
    holdings = et.load_holdings()
    assert "091340" in holdings
    assert holdings["091340"].entry_price == 91_400.0


def test_apply_buy_code_only_without_last_price_registers_zero(tmp_path, monkeypatch):
    """round 35 정책: last_prices/last_payloads 둘 다 비어도 보유 등록 진행.

    entry_price=0 으로 등록 — Exit.Triggers 트리거는 평가 skip (안전). 사용자가
    `/buy CODE PRICE` 로 매수가 갱신 가능.
    """
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import importlib
    import src.config
    importlib.reload(src.config)
    import src.scalping.exit.triggers as et
    importlib.reload(et)
    import src.notify.telegram_bot as bot
    importlib.reload(bot)

    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    msg = bot.apply_command(bot.parse_command("/buy 091340"), s, now)
    assert "보유 모드 진입" in msg
    assert "매수가 미입력" in msg
    holdings = et.load_holdings()
    assert "091340" in holdings
    assert holdings["091340"].entry_price == 0.0  # placeholder — 트리거 평가 skip


def test_apply_buy_off_hours_note_appended(tmp_path, monkeypatch):
    """장 시간 외 buy 는 등록 진행 + '장 시간 외' 안내 한 줄 추가."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import importlib
    import src.config
    importlib.reload(src.config)
    import src.scalping.exit.triggers as et
    importlib.reload(et)
    import src.notify.telegram_bot as bot
    importlib.reload(bot)

    s = MonitoringSession()
    # 평일 16:30 — 정규장 (09:00~15:30) 외
    now = datetime(2026, 5, 11, 16, 30)
    msg = bot.apply_command(
        bot.parse_command("/buy 091340 91300"), s, now,
    )
    assert "장 시간 외" in msg
    assert "보유 모드 진입" in msg
    # 등록은 진행됨
    assert "091340" in et.load_holdings()


def test_apply_buy_weekend_off_hours_note(tmp_path, monkeypatch):
    """주말 buy 도 장 시간 외 안내."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import importlib
    import src.config
    importlib.reload(src.config)
    import src.scalping.exit.triggers as et
    importlib.reload(et)
    import src.notify.telegram_bot as bot
    importlib.reload(bot)

    s = MonitoringSession()
    # 토요일
    now = datetime(2026, 5, 16, 10, 0)
    msg = bot.apply_command(
        bot.parse_command("/buy 091340 91300"), s, now,
    )
    assert "장 시간 외" in msg


def test_apply_buy_in_regular_session_no_note(tmp_path, monkeypatch):
    """정규장 시간 안에선 장 시간 외 안내 X."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import importlib
    import src.config
    importlib.reload(src.config)
    import src.scalping.exit.triggers as et
    importlib.reload(et)
    import src.notify.telegram_bot as bot
    importlib.reload(bot)

    s = MonitoringSession()
    # 평일 11:30 — 정규장
    now = datetime(2026, 5, 11, 11, 30)
    msg = bot.apply_command(
        bot.parse_command("/buy 091340 91300"), s, now,
    )
    assert "장 시간 외" not in msg
    assert "보유 모드 진입" in msg


def test_apply_sell_removes_holding(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import importlib
    import src.config
    importlib.reload(src.config)
    import src.scalping.exit.triggers as et
    importlib.reload(et)
    import src.notify.telegram_bot as bot
    importlib.reload(bot)

    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    bot.apply_command(bot.parse_command("/buy 091340 91300"), s, now)
    msg = bot.apply_command(bot.parse_command("/sell 091340"), s, now)
    assert "청산 처리" in msg
    assert "091340" not in et.load_holdings()


def test_apply_sell_no_holding():
    s = MonitoringSession()
    msg = apply_command(parse_command("/sell 091340"), s, datetime(2026, 5, 11, 9, 30))
    assert "보유 모드 아님" in msg


def test_apply_status_resets_message_id():
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    s.add_manual("091340", now)
    s.monitored["091340"].message_id = 12345
    msg = apply_command(parse_command("/status 091340"), s, now)
    assert "재발송" in msg
    assert s.monitored["091340"].message_id is None


def test_apply_status_unknown_code():
    s = MonitoringSession()
    msg = apply_command(parse_command("/status 091340"), s, datetime(2026, 5, 11, 9, 30))
    assert "모니터링 중이 아님" in msg
