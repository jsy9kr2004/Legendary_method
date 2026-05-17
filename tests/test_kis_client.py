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


def _multi_settings(tmp_path) -> Settings:
    from src.config import KisCredential

    cred_a = KisCredential("KEY_A", "SECRET_A", "11111111-01", "primary")
    cred_b = KisCredential("KEY_B", "SECRET_B", "22222222-01", "wife")
    return Settings(
        data_dir=tmp_path,
        log_dir=tmp_path / "logs",
        log_level="INFO",
        dry_run=False,
        kis_app_key="KEY_A",
        kis_app_secret="SECRET_A",
        kis_account_no="11111111-01",
        kis_api_mode="mock",
        telegram_bot_token="",
        telegram_chat_id="",
        gmail_user="",
        gmail_app_password="",
        gmail_to="",
        kis_credentials=(cred_a, cred_b),
    )


def test_multi_credentials_round_robin(tmp_path):
    """2개 credential 이면 호출이 번갈아 가며 각 키 사용."""
    from src.kis.rate_limit import reset_default_limiter

    reset_default_limiter()
    s = _multi_settings(tmp_path)
    payload = {"rt_cd": "0", "output": {}}

    with patch.object(auth, "get_token", return_value=_fake_token()):
        with patch.object(httpx.Client, "get", return_value=_ok_resp(payload)) as get_mock:
            with client.KISClient(s) as c:
                c.get("/uapi/foo", tr_id="TR0001")
                c.get("/uapi/foo", tr_id="TR0001")
                c.get("/uapi/foo", tr_id="TR0001")
                c.get("/uapi/foo", tr_id="TR0001")

    # 4번 호출 → A, B, A, B 순서
    headers_list = [call.kwargs["headers"] for call in get_mock.call_args_list]
    assert headers_list[0]["appkey"] == "KEY_A"
    assert headers_list[1]["appkey"] == "KEY_B"
    assert headers_list[2]["appkey"] == "KEY_A"
    assert headers_list[3]["appkey"] == "KEY_B"

    reset_default_limiter()


def test_multi_credentials_have_independent_limiters(tmp_path):
    """credential 마다 별도 limiter 인스턴스를 가져야 함."""
    from src.kis.rate_limit import reset_default_limiter

    reset_default_limiter()
    s = _multi_settings(tmp_path)

    with client.KISClient(s) as c:
        assert len(c._slots) == 2
        cred_a, lim_a = c._slots[0]
        cred_b, lim_b = c._slots[1]
        assert cred_a.label == "primary"
        assert cred_b.label == "wife"
        assert lim_a is not lim_b

    reset_default_limiter()


def test_token_fetched_per_credential(tmp_path):
    """라운드 로빈 시 각 호출에 매칭되는 credential 로 토큰 요청."""
    from src.config import KisCredential
    from src.kis.rate_limit import reset_default_limiter

    reset_default_limiter()
    s = _multi_settings(tmp_path)
    payload = {"rt_cd": "0"}

    called_with: list[KisCredential] = []

    def _capture_token(settings, credential=None, force_refresh=False):
        called_with.append(credential)
        return _fake_token()

    with patch.object(auth, "get_token", side_effect=_capture_token):
        with patch.object(httpx.Client, "get", return_value=_ok_resp(payload)):
            with client.KISClient(s) as c:
                c.get("/uapi/foo", tr_id="TR0001")
                c.get("/uapi/foo", tr_id="TR0001")

    assert len(called_with) == 2
    assert called_with[0].app_key == "KEY_A"
    assert called_with[1].app_key == "KEY_B"

    reset_default_limiter()
