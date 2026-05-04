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
