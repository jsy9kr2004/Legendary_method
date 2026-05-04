"""loguru 기본 설정.

stdout + 일별 회전 파일. 30일 보관, gzip 압축.
앱 진입점에서 `setup_logging(load_settings())` 한 번 호출.
"""
from __future__ import annotations

import sys

from loguru import logger

from src.config import Settings

_LOG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
    "<level>{level: <7}</level> | "
    "<cyan>{name}</cyan>:<cyan>{line}</cyan> | "
    "{message}"
)


def setup_logging(settings: Settings) -> None:
    """기존 핸들러를 모두 제거하고 새로 등록한다."""
    settings.log_dir.mkdir(parents=True, exist_ok=True)

    logger.remove()
    logger.add(
        sys.stdout,
        level=settings.log_level,
        format=_LOG_FORMAT,
        colorize=True,
        backtrace=False,
        diagnose=False,
    )
    logger.add(
        settings.log_dir / "trader-{time:YYYY-MM-DD}.log",
        level=settings.log_level,
        rotation="00:00",
        retention="30 days",
        compression="gz",
        encoding="utf-8",
        backtrace=True,
        diagnose=False,
    )
