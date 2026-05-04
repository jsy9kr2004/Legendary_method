"""환경설정 로더.

.env에서 값을 읽어 `Settings` dataclass로 노출한다.
경로는 모두 `pathlib.Path`, 시각은 모두 Asia/Seoul (KST) 기준.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pytz
from dotenv import load_dotenv

KST = pytz.timezone("Asia/Seoul")

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")


def _path_env(name: str, default: str) -> Path:
    raw = os.getenv(name, default)
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = (PROJECT_ROOT / p).resolve()
    return p


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class Settings:
    # 경로
    data_dir: Path
    log_dir: Path

    # 운영
    log_level: str
    dry_run: bool

    # KIS
    kis_app_key: str
    kis_app_secret: str
    kis_account_no: str
    kis_api_mode: str  # "real" | "mock"

    # Telegram
    telegram_bot_token: str
    telegram_chat_id: str

    # Gmail
    gmail_user: str
    gmail_app_password: str
    gmail_to: str


def load_settings() -> Settings:
    return Settings(
        data_dir=_path_env("DATA_DIR", "./data"),
        log_dir=_path_env("LOG_DIR", "./logs"),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        dry_run=_bool_env("DRY_RUN", False),
        kis_app_key=os.getenv("KIS_APP_KEY", ""),
        kis_app_secret=os.getenv("KIS_APP_SECRET", ""),
        kis_account_no=os.getenv("KIS_ACCOUNT_NO", ""),
        kis_api_mode=os.getenv("KIS_API_MODE", "mock"),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        gmail_user=os.getenv("GMAIL_USER", ""),
        gmail_app_password=os.getenv("GMAIL_APP_PASSWORD", ""),
        gmail_to=os.getenv("GMAIL_TO", ""),
    )


def now_kst() -> datetime:
    return datetime.now(KST)


def today_kst() -> date:
    return now_kst().date()
