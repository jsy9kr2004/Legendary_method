"""5년치 일봉 초기 적재 스크립트.

날짜 단위로 KOSPI+KOSDAQ 전종목을 받아 단일 parquet에 누적.
이미 적재된 날짜는 자동 skip → 중간 실패해도 resume 가능.

사용:
    python -m src.data.init_daily                     # 오늘 기준 5년치
    python -m src.data.init_daily --years 3
    python -m src.data.init_daily --from 20200101 --to 20250503
    python -m src.data.init_daily --throttle 1.5      # 호출 간격 조정
"""
from __future__ import annotations

import argparse
import time
from datetime import date, timedelta

from loguru import logger
from tqdm import tqdm

from src.config import load_settings, today_kst
from src.data import daily, storage
from src.logging_setup import setup_logging


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="5년치 일봉 초기 적재")
    p.add_argument("--years", type=int, default=5, help="오늘 기준 N년치 (기본 5)")
    p.add_argument("--from", dest="fromdate", type=str, help="YYYYMMDD")
    p.add_argument("--to", dest="todate", type=str, help="YYYYMMDD")
    p.add_argument("--throttle", type=float, default=1.0, help="요청 간 sleep (초)")
    return p.parse_args()


def _yyyymmdd_to_date(s: str) -> date:
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def _date_range(fromdate: date, todate: date):
    cur = fromdate
    while cur <= todate:
        yield cur
        cur += timedelta(days=1)


def _resolve_range(args: argparse.Namespace) -> tuple[date, date]:
    today = today_kst()
    if args.fromdate and args.todate:
        return _yyyymmdd_to_date(args.fromdate), _yyyymmdd_to_date(args.todate)
    fromdate = today.replace(year=today.year - args.years)
    todate = today - timedelta(days=1)  # 어제까지 (오늘 데이터는 16시 이후)
    return fromdate, todate


def main() -> None:
    args = _parse_args()
    settings = load_settings()
    setup_logging(settings)

    fromdate, todate = _resolve_range(args)
    logger.info(f"초기 적재 범위: {fromdate} ~ {todate}")

    already = storage.loaded_dates(settings.data_dir)
    logger.info(f"기존 적재된 날짜 수: {len(already)}")

    targets = [
        d
        for d in _date_range(fromdate, todate)
        if d.weekday() < 5 and d not in already
    ]
    logger.info(f"적재할 평일 수 (휴장일/기존 제외 전): {len(targets)}")

    failed: list[date] = []
    for d in tqdm(targets, desc="일봉 적재"):
        try:
            df = daily.fetch_all_market_for_date(d)
            if df.empty:
                logger.debug(f"{d}: 데이터 없음 (휴장일 추정)")
            else:
                total = storage.upsert_daily_ohlcv(df, settings.data_dir)
                logger.debug(f"{d}: {len(df)} rows 추가 (누적 {total})")
        except Exception as e:  # noqa: BLE001
            logger.error(f"{d}: 실패 — {e}")
            failed.append(d)
        time.sleep(args.throttle)

    logger.info(f"완료. 성공 {len(targets) - len(failed)} / 실패 {len(failed)}")
    if failed:
        logger.warning(f"실패 일자 (재실행 시 자동 재시도): {failed}")


if __name__ == "__main__":
    main()
