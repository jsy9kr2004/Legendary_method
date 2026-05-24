"""src.notify 모듈 테스트. 외부 API/SMTP는 mock."""
from __future__ import annotations

import smtplib
from datetime import date
from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.config import Settings
from src.notify.dispatcher import Dispatcher
from src.notify.email import build_afterhours_subject, send_email
from src.notify.telegram import _split_text, send_error_alert, send_message


# ── helpers ──────────────────────────────────────────────────────────────────

def _settings(tmp_path, dry_run: bool = False) -> Settings:
    return Settings(
        data_dir=tmp_path,
        log_dir=tmp_path / "logs",
        log_level="INFO",
        dry_run=dry_run,
        kis_app_key="K",
        kis_app_secret="S",
        kis_account_no="50000000-01",
        kis_api_mode="mock",
        telegram_bot_token="TEST_TOKEN",
        telegram_chat_id="12345",
        gmail_user="test@example.com",
        gmail_app_password="app_pw",
        gmail_to="zeta@example.com",
        smtp_host="smtp.example.com",
        smtp_port=587,
    )


def _ok_resp(payload: dict) -> MagicMock:
    r = MagicMock()
    r.json.return_value = payload
    r.status_code = 200
    r.raise_for_status.return_value = None
    return r


# ── _split_text ──────────────────────────────────────────────────────────────

def test_dispatcher_routes_eod_reports_to_group(tmp_path):
    """telegram_eod_chat_id 설정 시 종배 레포트는 그룹으로."""
    import dataclasses
    s = dataclasses.replace(_settings(tmp_path), telegram_eod_chat_id="GROUP")
    d = Dispatcher(s)
    with patch("src.notify.dispatcher.send_message", return_value=[{"ok": True}]) as m:
        d.send_decision(["report"])
    assert m.call_args.args[1] == "GROUP"  # chat_id = 그룹


def test_dispatcher_falls_back_to_main_when_no_eod(tmp_path):
    """telegram_eod_chat_id 비면 개인 DM(telegram_chat_id) 사용."""
    d = Dispatcher(_settings(tmp_path))  # eod 기본 ""
    with patch("src.notify.dispatcher.send_message", return_value=[{"ok": True}]) as m:
        d.send_morning("morning")
    assert m.call_args.args[1] == "12345"


def test_dispatcher_error_alert_uses_main_chat(tmp_path):
    """에러 알림은 그룹이 아니라 개인 DM(운영자) 으로."""
    import dataclasses
    s = dataclasses.replace(_settings(tmp_path), telegram_eod_chat_id="GROUP")
    d = Dispatcher(s)
    with patch("src.notify.dispatcher.send_error_alert", return_value={"ok": True}) as m:
        d.telegram_error("boom", context="t")
    assert m.call_args.args[1] == "12345"  # 그룹 아님


def test_split_text_short():
    assert _split_text("hello") == ["hello"]


def test_split_text_exact_limit():
    text = "a" * 4096
    assert _split_text(text) == [text]


def test_split_text_over_limit():
    text = "\n".join(["line"] * 1000)  # ~5000자
    parts = _split_text(text)
    assert len(parts) > 1
    assert all(len(p) <= 4096 for p in parts)


def test_split_text_preserves_content():
    text = "a\nb\nc\nd\ne"
    parts = _split_text(text, max_len=4)
    rejoined = "\n".join(parts)
    # 분할 후 내용 손실 없어야 함
    for line in ["a", "b", "c", "d", "e"]:
        assert line in rejoined


def test_split_text_single_long_line():
    """단일 줄이 max_len 초과 시 강제 분할."""
    text = "x" * 10
    parts = _split_text(text, max_len=3)
    assert all(len(p) <= 3 for p in parts)
    assert "".join(parts) == text


# ── send_message ─────────────────────────────────────────────────────────────

def test_send_message_calls_api(tmp_path):
    payload = {"ok": True, "result": {"message_id": 1}}
    with patch("src.notify.telegram.httpx.Client") as MockClient:
        instance = MockClient.return_value.__enter__.return_value
        instance.post.return_value = _ok_resp(payload)
        results = send_message("TOKEN", "CHAT", "hello")
    assert results[0]["ok"] is True
    assert instance.post.call_count == 1


def test_send_message_splits_long_text(tmp_path):
    """4096자 초과 시 여러 번 post 호출."""
    long_text = "\n".join(["line"] * 1000)
    payload = {"ok": True}
    with patch("src.notify.telegram.httpx.Client") as MockClient:
        instance = MockClient.return_value.__enter__.return_value
        instance.post.return_value = _ok_resp(payload)
        with patch("src.notify.telegram.time.sleep"):
            results = send_message("TOKEN", "CHAT", long_text)
    assert instance.post.call_count > 1
    assert all(r["ok"] is True for r in results)


def test_send_message_empty_token():
    results = send_message("", "CHAT", "text")
    assert results == []


def test_send_message_empty_chat_id():
    results = send_message("TOKEN", "", "text")
    assert results == []


def test_send_message_400_returns_error():
    bad_resp = MagicMock()
    bad_resp.status_code = 400
    bad_resp.text = "Bad Request: can't parse entities"
    bad_resp.raise_for_status.return_value = None
    with patch("src.notify.telegram.httpx.Client") as MockClient:
        instance = MockClient.return_value.__enter__.return_value
        instance.post.return_value = bad_resp
        results = send_message("TOKEN", "CHAT", "bad *markdown")
    assert results[0]["ok"] is False


def test_send_error_alert_uses_plain_text():
    """에러 알림은 parse_mode=None으로 발송."""
    payload = {"ok": True}
    with patch("src.notify.telegram.httpx.Client") as MockClient:
        instance = MockClient.return_value.__enter__.return_value
        instance.post.return_value = _ok_resp(payload)
        send_error_alert("TOKEN", "CHAT", "오류 발생", context="스케줄러")
    call_kwargs = instance.post.call_args.kwargs
    body = call_kwargs.get("json", {})
    assert "parse_mode" not in body


def test_send_error_alert_empty_token():
    result = send_error_alert("", "CHAT", "오류")
    assert result == {}


# ── send_email ────────────────────────────────────────────────────────────────

def test_send_email_calls_smtp():
    with patch("src.notify.email.smtplib.SMTP") as MockSMTP:
        smtp_instance = MockSMTP.return_value.__enter__.return_value
        result = send_email("user@example.com", "pw", "to@example.com", "제목", "본문",
                            host="smtp.example.com", port=587)
    assert result["ok"] is True
    smtp_instance.sendmail.assert_called_once()


def test_send_email_auth_error():
    with patch("src.notify.email.smtplib.SMTP") as MockSMTP:
        instance = MockSMTP.return_value.__enter__.return_value
        instance.login.side_effect = smtplib.SMTPAuthenticationError(535, b"auth failed")
        result = send_email("user@example.com", "wrong_pw", "to@example.com", "제목", "본문",
                            host="smtp.example.com", port=587)
    assert result["ok"] is False
    assert "인증 실패" in result["error"]


def test_send_email_empty_settings():
    result = send_email("", "", "", "제목", "본문")
    assert result["ok"] is False


def test_send_email_missing_host_skipped():
    """host/port 미설정 시 발송 스킵."""
    with patch("src.notify.email.smtplib.SMTP") as MockSMTP:
        result = send_email("u@example.com", "pw", "to@example.com", "제목", "본문")
    MockSMTP.assert_not_called()
    assert result["ok"] is False


def test_build_afterhours_subject():
    assert build_afterhours_subject("2026-05-06") == "[종배] 2026-05-06 사후 리뷰"


# ── Dispatcher ────────────────────────────────────────────────────────────────

def test_dispatcher_dry_run_telegram(tmp_path):
    """DRY_RUN이면 실제 발송 없이 ok 반환."""
    d = Dispatcher(_settings(tmp_path, dry_run=True))
    with patch("src.notify.dispatcher.send_message") as mock_send:
        results = d.telegram("test message")
    mock_send.assert_not_called()
    assert results[0]["dry_run"] is True


def test_dispatcher_dry_run_email(tmp_path):
    d = Dispatcher(_settings(tmp_path, dry_run=True))
    with patch("src.notify.dispatcher.send_email") as mock_send:
        result = d.email("제목", "본문")
    mock_send.assert_not_called()
    assert result["dry_run"] is True


def test_dispatcher_dry_run_error_alert_still_logs(tmp_path):
    """에러 알림은 DRY_RUN이어도 로그 출력 (실제 발송은 스킵)."""
    d = Dispatcher(_settings(tmp_path, dry_run=True))
    with patch("src.notify.dispatcher.send_error_alert") as mock_send:
        result = d.telegram_error("오류", context="테스트")
    mock_send.assert_not_called()
    assert result["dry_run"] is True


def test_dispatcher_telegram_live(tmp_path):
    d = Dispatcher(_settings(tmp_path, dry_run=False))
    payload = {"ok": True}
    with patch("src.notify.dispatcher.send_message", return_value=[payload]) as mock_send:
        results = d.telegram("hello")
    mock_send.assert_called_once_with("TEST_TOKEN", "12345", "hello", parse_mode="Markdown")
    assert results == [payload]


def test_dispatcher_send_decision_multiple_parts(tmp_path):
    d = Dispatcher(_settings(tmp_path, dry_run=True))
    parts = ["part1", "part2", "part3"]
    # dry_run이라 실제 발송 없이 3번 호출되는지 확인
    results_list = []
    original_telegram = d.telegram
    calls = []
    d.telegram = lambda text, **kw: calls.append(text) or [{"ok": True, "dry_run": True}]
    d.send_decision(parts)
    assert len(calls) == 3
