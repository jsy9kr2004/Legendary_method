"""src.config 의 환경 로딩 / KST helper 검증."""
from __future__ import annotations

import pytest

from src import config


def test_kst_timezone_offset():
    """Asia/Seoul = UTC+9 고정."""
    now = config.now_kst()
    offset = now.utcoffset()
    assert offset is not None
    assert offset.total_seconds() == 9 * 3600


def test_today_kst_is_date():
    from datetime import date

    assert isinstance(config.today_kst(), date)


def test_settings_defaults(monkeypatch, tmp_path):
    """env가 비어 있으면 기본값으로 동작."""
    for key in [
        "DATA_DIR",
        "LOG_DIR",
        "LOG_LEVEL",
        "DRY_RUN",
        "KIS_APP_KEY",
        "KIS_APP_SECRET",
        "KIS_ACCOUNT_NO",
        "KIS_API_MODE",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "GMAIL_USER",
        "GMAIL_APP_PASSWORD",
        "GMAIL_TO",
    ]:
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))

    s = config.load_settings()
    assert s.data_dir == tmp_path / "data"
    assert s.log_dir == tmp_path / "logs"
    assert s.log_level == "INFO"
    assert s.dry_run is False
    assert s.kis_api_mode == "mock"
    assert s.telegram_bot_token == ""


def test_data_dir_relative_path_resolves_under_project_root(monkeypatch):
    monkeypatch.setenv("DATA_DIR", "./data")
    s = config.load_settings()
    assert s.data_dir.is_absolute()
    assert s.data_dir == config.PROJECT_ROOT / "data"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("true", True),
        ("True", True),
        ("1", True),
        ("yes", True),
        ("on", True),
        ("false", False),
        ("0", False),
        ("no", False),
        ("", False),
    ],
)
def test_dry_run_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv("DRY_RUN", raw)
    s = config.load_settings()
    assert s.dry_run is expected


def test_log_level_uppercased(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "debug")
    s = config.load_settings()
    assert s.log_level == "DEBUG"


def test_kis_single_credential(monkeypatch):
    """KIS_APP_KEY 하나만 있으면 credentials() 가 1개 반환."""
    for k in ["KIS_APP_KEY_2", "KIS_APP_KEY_3"]:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("KIS_APP_KEY", "PRIMARY_KEY")
    monkeypatch.setenv("KIS_APP_SECRET", "PRIMARY_SECRET")
    monkeypatch.setenv("KIS_ACCOUNT_NO", "11111111-01")

    s = config.load_settings()
    creds = s.credentials()
    assert len(creds) == 1
    assert creds[0].app_key == "PRIMARY_KEY"
    assert creds[0].label == "primary"
    # 하위 호환 필드도 채워짐
    assert s.kis_app_key == "PRIMARY_KEY"


def test_kis_multi_credentials_scanned(monkeypatch):
    """KIS_APP_KEY + KIS_APP_KEY_2 가 있으면 둘 다 로드."""
    for k in ["KIS_APP_KEY_3", "KIS_APP_KEY_4"]:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("KIS_APP_KEY", "KEY_A")
    monkeypatch.setenv("KIS_APP_SECRET", "SEC_A")
    monkeypatch.setenv("KIS_ACCOUNT_NO", "11111111-01")
    monkeypatch.setenv("KIS_APP_KEY_2", "KEY_B")
    monkeypatch.setenv("KIS_APP_SECRET_2", "SEC_B")
    monkeypatch.setenv("KIS_ACCOUNT_NO_2", "22222222-01")
    monkeypatch.setenv("KIS_LABEL_2", "wife")

    s = config.load_settings()
    creds = s.credentials()
    assert len(creds) == 2
    assert creds[0].label == "primary"
    assert creds[1].label == "wife"
    assert creds[1].app_key == "KEY_B"
    # 하위 호환: 첫 번째 credential 이 단일 필드에도 반영
    assert s.kis_app_key == "KEY_A"


def test_kis_scan_stops_at_gap(monkeypatch):
    """KIS_APP_KEY_2 가 비어 있으면 _3 가 있어도 스캔 종료."""
    monkeypatch.setenv("KIS_APP_KEY", "KEY_A")
    monkeypatch.setenv("KIS_APP_SECRET", "SEC_A")
    monkeypatch.delenv("KIS_APP_KEY_2", raising=False)
    monkeypatch.delenv("KIS_APP_SECRET_2", raising=False)
    monkeypatch.setenv("KIS_APP_KEY_3", "KEY_C")
    monkeypatch.setenv("KIS_APP_SECRET_3", "SEC_C")

    s = config.load_settings()
    creds = s.credentials()
    assert len(creds) == 1  # KEY_A 만, KEY_C 는 갭 때문에 무시
    assert creds[0].app_key == "KEY_A"


def test_kis_credential_cache_id_stable():
    """cache_id 가 같은 app_key 에 대해 결정론적이어야 함 (캐시 파일명 안정성)."""
    c1 = config.KisCredential("APPKEY_X", "SEC", "1", "primary")
    c2 = config.KisCredential("APPKEY_X", "OTHER", "2", "different_label")
    c3 = config.KisCredential("APPKEY_Y", "SEC", "1", "primary")
    assert c1.cache_id == c2.cache_id  # app_key 만 영향
    assert c1.cache_id != c3.cache_id
    assert len(c1.cache_id) == 8
