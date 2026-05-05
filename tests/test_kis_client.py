"""src.kis.client 테스트. httpx 와 토큰 발급은 mock."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.config import Settings
from src.kis import auth, client
from src.kis.rate_limit import RateLimiter


def _settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        log_dir=tmp_path / "logs",
        log_level="INFO",
        dry_run=False,
        kis_app_key="APPKEY",
        kis_app_secret="APPSECRET",
        kis_account_no="50000000-01",
        kis_api_mode="mock",
        telegram_bot_token="",
        telegram_chat_id="",
        gmail_user="",
        gmail_app_password="",
        gmail_to="",
    )


def _fake_token():
    return auth.Token(
        access_token="TOK",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=23),
        api_mode="mock",
    )


def _ok_resp(payload: dict) -> MagicMock:
    r = MagicMock()
    r.json.return_value = payload
    r.raise_for_status.return_value = None
    return r


def test_get_injects_auth_headers(tmp_path):
    s = _settings(tmp_path)
    payload = {"rt_cd": "0", "msg_cd": "ok", "msg1": "정상", "output": {"x": 1}}

    with patch.object(auth, "get_token", return_value=_fake_token()):
        with patch.object(httpx.Client, "get", return_value=_ok_resp(payload)) as get_mock:
            with client.KISClient(s, limiter=RateLimiter(calls_per_sec=1000)) as c:
                out = c.get("/uapi/foo", tr_id="TR0001", params={"a": "b"})

    assert out == payload
    call = get_mock.call_args
    assert call.args[0].endswith("/uapi/foo")
    headers = call.kwargs["headers"]
    assert headers["authorization"] == "Bearer TOK"
    assert headers["tr_id"] == "TR0001"
    assert headers["appkey"] == "APPKEY"
    assert headers["appsecret"] == "APPSECRET"
    assert call.kwargs["params"] == {"a": "b"}


def test_get_raises_on_kis_error_rt_cd(tmp_path):
    s = _settings(tmp_path)
    payload = {"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "한도 초과"}

    with patch.object(auth, "get_token", return_value=_fake_token()):
        with patch.object(httpx.Client, "get", return_value=_ok_resp(payload)):
            with client.KISClient(s, limiter=RateLimiter(calls_per_sec=1000)) as c:
                with pytest.raises(client.KISApiError) as ei:
                    c.get("/uapi/foo", tr_id="TR0001")

    assert ei.value.rt_cd == "1"
    assert ei.value.msg_cd == "EGW00123"
    assert ei.value.msg == "한도 초과"


def test_get_uses_correct_base_url(tmp_path):
    s = _settings(tmp_path)
    payload = {"rt_cd": "0"}

    with patch.object(auth, "get_token", return_value=_fake_token()):
        with patch.object(httpx.Client, "get", return_value=_ok_resp(payload)) as get_mock:
            with client.KISClient(s, limiter=RateLimiter(calls_per_sec=1000)) as c:
                c.get("/uapi/foo", tr_id="TR0001")

    url = get_mock.call_args.args[0]
    assert "openapivts.koreainvestment.com" in url


def test_get_calls_limiter(tmp_path):
    s = _settings(tmp_path)
    payload = {"rt_cd": "0"}
    limiter = MagicMock()

    with patch.object(auth, "get_token", return_value=_fake_token()):
        with patch.object(httpx.Client, "get", return_value=_ok_resp(payload)):
            with client.KISClient(s, limiter=limiter) as c:
                c.get("/uapi/foo", tr_id="TR0001")
                c.get("/uapi/foo", tr_id="TR0001")

    assert limiter.acquire.call_count == 2


def test_response_without_rt_cd_returns_payload(tmp_path):
    """rt_cd 가 없는 응답(예: 토큰 발급 같은 비표준)도 그대로 반환."""
    s = _settings(tmp_path)
    payload = {"some_field": "value"}

    with patch.object(auth, "get_token", return_value=_fake_token()):
        with patch.object(httpx.Client, "get", return_value=_ok_resp(payload)):
            with client.KISClient(s, limiter=RateLimiter(calls_per_sec=1000)) as c:
                out = c.get("/uapi/foo", tr_id="TR0001")

    assert out == payload
