"""종목 마스터 업데이트 (KIS mst → parquet).

매일 16:30 cron 권장. 시총 / 상장일은 KIS mst 에서 직접 받지 못하므로
0 / None 으로 들어감 (TODO).

사용:
    python -m src.data.update_master
    python -m src.data.update_master --exclude-preferred
"""
from __future__ import annotations

import argparse

from loguru import logger

from src.config import load_settings
from src.data import master, storage
from src.logging_setup import setup_logging


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="종목 마스터 업데이트")
    p.add_argument(
        "--exclude-preferred",
        action="store_true",
        help="우선주(코드 끝자리 != 0) 제외",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    settings = load_settings()
    setup_logging(settings)

    df = master.fetch_stock_master(include_preferred=not args.exclude_preferred)
    storage.write_stock_master(df, settings.data_dir)

    logger.info(
        f"종목 마스터 저장 완료: {len(df)} 종목 "
        f"(KOSPI={(df['market']=='KOSPI').sum()}, "
        f"KOSDAQ={(df['market']=='KOSDAQ').sum()}, "
        f"preferred={'IN' if not args.exclude_preferred else 'OUT'})"
    )


if __name__ == "__main__":
    main()
