"""매일 장 마감 후 incremental 일봉 적재.

마지막 적재일 다음 영업일부터 어제까지 받아 누적. 휴장일은 자동 skip
(pykrx 응답이 비어있으면 휴장으로 간주).

사용 (cron 매일 16:00):
    python -m src.data.incremental_daily
"""
from __future__ import annotations

import argparse
import time
from datetime import date, timedelta

from loguru import logger

from src.config import load_settings, today_kst
from src.data import daily, storage
from src.logging_setup import setup_logging


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="매일 incremental 일봉 적재")
    p.add_argument("--throttle", type=float, default=1.0)
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    settings = load_settings()
    setup_logging(settings)

    last = storage.latest_loaded_date(settings.data_dir)
    if last is None:
        logger.warning("기존 데이터 없음. `python -m src.data.init_daily` 를 먼저 실행하세요.")
        return

    yesterday = today_kst() - timedelta(days=1)
    fromdate = last + timedelta(days=1)

    if fromdate > yesterday:
        logger.info(f"이미 최신 (last={last}). 추가 적재 없음.")
        return

    logger.info(f"incremental 범위: {fromdate} ~ {yesterday}")

    cur = fromdate
    success = 0
    while cur <= yesterday:
        if cur.weekday() >= 5:
            cur += timedelta(days=1)
            continue
        try:
            df = daily.fetch_all_market_for_date(cur)
            if df.empty:
                logger.info(f"{cur}: 데이터 없음 (휴장일)")
            else:
                storage.upsert_daily_ohlcv(df, settings.data_dir)
                logger.info(f"{cur}: {len(df)} rows 적재")
                success += 1
        except Exception as e:  # noqa: BLE001
            logger.error(f"{cur}: 실패 — {e}")
        time.sleep(args.throttle)
        cur += timedelta(days=1)

    logger.info(f"incremental 완료. 적재일 수={success}")


if __name__ == "__main__":
    main()
