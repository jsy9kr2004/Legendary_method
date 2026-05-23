"""종목 마스터 업데이트 (KIS mst → parquet) + 시총/상장주식수 backfill.

매일 16:30 cron 권장.

시총/상장주식수: KIS mst 의 시총 컬럼이 0 으로 깨져 파싱되므로(char[172:181] 오프셋
결함), 누적 거래대금 순위 스냅샷에서 `시총 = 거래대금 / (회전율/100)` 으로 역산해
backfill 한다 (`master.backfill_market_cap_from_snapshots`). 거래대금 top50 에 등장한
종목만 채워지고, 저유동(종배 무관) 종목은 0 유지. 상장일은 여전히 None (별도 TODO).

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
    # 시총/상장주식수 backfill — KIS mst 시총 컬럼이 0 이라 스냅샷 거래대금/회전율 역산.
    df = master.backfill_market_cap_from_snapshots(df, settings.data_dir)
    storage.write_stock_master(df, settings.data_dir)

    n_mc = int((df["market_cap"] > 0).sum()) if "market_cap" in df.columns else 0
    logger.info(
        f"종목 마스터 저장 완료: {len(df)} 종목 "
        f"(KOSPI={(df['market']=='KOSPI').sum()}, "
        f"KOSDAQ={(df['market']=='KOSDAQ').sum()}, "
        f"시총>0={n_mc}, "
        f"preferred={'IN' if not args.exclude_preferred else 'OUT'})"
    )


if __name__ == "__main__":
    main()
