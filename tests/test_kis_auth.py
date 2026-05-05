"""src.kis.auth 테스트. httpx.post 는 mock 으로 차단."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.config import Settings
from src.kis import auth


def _settings(tmp_path, mode: str = "mock") -> Settings:
    return Settings(
        data_dir=tmp_path,
        log_dir=tmp_path / "logs",
        log_level="INFO",
        dry_run=False,
        kis_app_key="APPKEY",
        kis_app_secret="APPSECRET",
        kis_account_no="50000000-01",
        kis_api_mode=mode,
        telegram_bot_token="",
        telegram_chat_id="",
        gmail_user="",
        gmail_app_password="",
        gmail_to="",
    )


def _ok_response(token: str = "ACCESS_TOKEN_X", expires_in: int = 86400) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = {
        "access_token": token,
        "expires_in": expires_in,
        "token_type": "Bearer",
    }
    resp.raise_for_status.return_value = None
    return resp


def test_kis_base_url_real_vs_mock():
    assert auth.kis_base_url("real") == auth.REAL_BASE_URL
    assert auth.kis_base_url("mock") == auth.MOCK_BASE_URL
    assert auth.kis_base_url("anything-else") == auth.MOCK_BASE_URL  # 기본 mock


def test_get_token_first_time_calls_api(tmp_path):
    s = _settings(tmp_path)
    with patch("httpx.post", return_value=_ok_response("TOK1")) as post_mock:
        token = auth.get_token(s)

    assert token.access_token == "TOK1"
    assert token.api_mode == "mock"
    post_mock.assert_called_once()
    # mock 도메인 호출 확인
    called_url = post_mock.call_args.args[0]
    assert "openapivts.koreainvestment.com" in called_url


def test_get_token_uses_cache_on_second_call(tmp_path):
    s = _settings(tmp_path)
    with patch("httpx.post", return_value=_ok_response("TOK1")) as post_mock:
        auth.get_token(s)
        auth.get_token(s)
    assert post_mock.call_count == 1


def test_get_token_force_refresh_bypasses_cache(tmp_path):
    s = _settings(tmp_path)
    with patch("httpx.post", side_effect=[_ok_response("TOK1"), _ok_response("TOK2")]) as post_mock:
        t1 = auth.get_token(s)
        t2 = auth.get_token(s, force_refresh=True)
    assert t1.access_token == "TOK1"
    assert t2.access_token == "TOK2"
    assert post_mock.call_count == 2


def test_cache_invalidated_on_mode_switch(tmp_path):
    mock_settings = _settings(tmp_path, "mock")
    real_settings = _settings(tmp_path, "real")
    with patch("httpx.post", side_effect=[_ok_response("MOCK_TOK"), _ok_response("REAL_TOK")]) as post_mock:
        auth.get_token(mock_settings)
        t = auth.get_token(real_settings)
    assert t.access_token == "REAL_TOK"
    assert post_mock.call_count == 2


def test_expired_token_triggers_refresh(tmp_path):
    s = _settings(tmp_path)
    # 만료된 토큰을 캐시에 직접 심음
    expired = {
        "access_token": "OLD",
        "expires_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        "api_mode": "mock",
    }
    cache_path = tmp_path / "meta" / "kis_token.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(expired))

    with patch("httpx.post", return_value=_ok_response("NEW")) as post_mock:
        token = auth.get_token(s)
    assert token.access_token == "NEW"
    post_mock.assert_called_once()


def test_missing_credentials_raises(tmp_path):
    s = Settings(
        data_dir=tmp_path,
        log_dir=tmp_path / "logs",
        log_level="INFO",
        dry_run=False,
        kis_app_key="",
        kis_app_secret="",
        kis_account_no="",
        kis_api_mode="mock",
        telegram_bot_token="",
        telegram_chat_id="",
        gmail_user="",
        gmail_app_password="",
        gmail_to="",
    )
    with pytest.raises(RuntimeError, match="KIS_APP_KEY"):
        auth.get_token(s)


def test_kis_error_response_raises(tmp_path):
    s = _settings(tmp_path)
    err_resp = MagicMock()
    err_resp.json.return_value = {"error_code": "EGW00121", "error_description": "한도 초과"}
    err_resp.raise_for_status.return_value = None
    with patch("httpx.post", return_value=err_resp):
        with pytest.raises(RuntimeError, match="KIS 토큰 발급 실패"):
            auth.get_token(s)


def test_token_is_valid():
    future = datetime.now(timezone.utc) + timedelta(hours=23)
    t = auth.Token("X", future, "mock")
    assert t.is_valid() is True

    near_expiry = datetime.now(timezone.utc) + timedelta(minutes=2)
    t2 = auth.Token("X", near_expiry, "mock")
    assert t2.is_valid() is False  # 5분 버퍼 안


def test_token_persisted_to_disk(tmp_path):
    s = _settings(tmp_path)
    with patch("httpx.post", return_value=_ok_response("PERSISTED")):
        auth.get_token(s)
    cache_path = tmp_path / "meta" / "kis_token.json"
    assert cache_path.exists()
    data = json.loads(cache_path.read_text())
    assert data["access_token"] == "PERSISTED"
    assert data["api_mode"] == "mock"
