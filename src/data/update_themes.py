"""네이버 금융 테마 매핑 업데이트 CLI.

실행:
    python -m src.data.update_themes            # 7일 신선도 체크 후 필요시만 실행
    python -m src.data.update_themes --force    # 신선도 무시하고 강제 재크롤링

동작:
    1. data/meta/naver_themes.parquet 의 마지막 크롤링 날짜 확인
    2. 오늘 기준 7일 이내이면 "신선함" 로그 출력 후 종료
    3. 7일 초과(또는 --force)이면 전체 크롤링 → parquet 저장
    4. 결과 요약 출력

cron 예시 (매일 자정):
    0 0 * * * python -m src.data.update_themes >> /var/log/themes.log 2>&1
"""
from __future__ import annotations

import argparse
import sys

import pandas as pd
from loguru import logger

from src.config import load_settings
from src.data.storage import (
    themes_are_fresh,
    themes_last_crawled,
    write_naver_themes,
)
from src.data.theme_crawler import crawl_all
from src.logging_setup import setup_logging

_MAX_AGE_DAYS = 7


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="네이버 금융 테마 매핑 업데이트")
    parser.add_argument(
        "--force",
        action="store_true",
        help="신선도 무시하고 강제 재크롤링",
    )
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=_MAX_AGE_DAYS,
        metavar="N",
        help=f"재크롤링 기준 일수 (기본: {_MAX_AGE_DAYS}일)",
    )
    args = parser.parse_args(argv)

    settings = load_settings()
    setup_logging(settings)

    last = themes_last_crawled(settings.data_dir)
    if last:
        logger.info(f"마지막 테마 크롤링: {last}")
    else:
        logger.info("저장된 테마 데이터 없음 (최초 실행)")

    if not args.force and themes_are_fresh(settings.data_dir, max_age_days=args.max_age_days):
        logger.info(
            f"테마 데이터가 신선함 (마지막={last}, 기준={args.max_age_days}일). 스킵."
        )
        return 0

    logger.info("네이버 금융 테마 크롤링 시작...")
    records = crawl_all()

    if not records:
        logger.error("크롤링 결과 없음 — 네트워크 또는 파싱 오류 확인 필요")
        return 1

    df = pd.DataFrame(records)
    write_naver_themes(df, settings.data_dir)

    theme_count = df["theme"].nunique()
    code_count = df["code"].nunique()
    logger.info(
        f"저장 완료: {theme_count}개 테마, {code_count}개 종목, "
        f"{len(df)}개 (종목, 테마) 쌍 → {settings.data_dir}/meta/naver_themes.parquet"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
