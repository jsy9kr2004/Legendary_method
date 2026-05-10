"""WICS 섹터 매핑 업데이트 CLI (월 1회).

실행:
    python -m src.data.update_wics            # 35일 신선도 체크 후 필요시만 실행
    python -m src.data.update_wics --force    # 강제 재크롤링

cron 예시 (매월 1일 새벽):
    0 3 1 * * python -m src.data.update_wics >> /var/log/wics.log 2>&1
"""
from __future__ import annotations

import argparse
import sys

from loguru import logger

from src.config import load_settings
from src.data.storage import wics_is_fresh, wics_last_crawled, write_wics_sectors
from src.data.wics_crawler import fetch_wics_sectors
from src.logging_setup import setup_logging

_MAX_AGE_DAYS = 35  # 월 1회 + 여유


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="WICS 섹터 매핑 업데이트")
    parser.add_argument(
        "--force", action="store_true", help="신선도 무시하고 강제 재크롤링",
    )
    parser.add_argument(
        "--max-age-days", type=int, default=_MAX_AGE_DAYS, metavar="N",
        help=f"재크롤링 기준 일수 (기본: {_MAX_AGE_DAYS}일)",
    )
    args = parser.parse_args(argv)

    settings = load_settings()
    setup_logging(settings)

    if not args.force and wics_is_fresh(settings.data_dir, args.max_age_days):
        last = wics_last_crawled(settings.data_dir)
        logger.info(f"WICS 데이터 신선함 ({last}, ≤ {args.max_age_days}일) — 크롤링 스킵")
        return 0

    logger.info("WICS 크롤링 시작")
    df = fetch_wics_sectors()
    if df.empty:
        logger.error("WICS 크롤링 결과 비어 있음 — 저장 스킵")
        return 1

    write_wics_sectors(df, settings.data_dir)
    n_codes = df["code"].nunique()
    n_sectors = df["sector_code"].nunique()
    logger.info(f"WICS 저장 완료: {n_codes}종목 × {n_sectors}개 대분류")
    return 0


if __name__ == "__main__":
    sys.exit(main())
