"""5년치 일봉 초기 적재 (KIS Open API).

종목 마스터(KIS mst) → 종목별로 90일 청크 단위로 일봉 받아 누적.
이미 적재된 (code, date) 는 storage upsert 가 dedup 처리.

종목별로 last_loaded_date 을 보고 그 다음 영업일부터 받으면 resume 됨.

사용:
    python -m src.data.init_daily                    # 오늘 기준 5년치
    python -m src.data.init_daily --years 1
    python -m src.data.init_daily --from 20240101 --to 20250503
    python -m src.data.init_daily --markets KOSPI    # 시장 한정
    python -m src.data.init_daily --limit 50         # 종목 수 한정 (smoke test)
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta

import pandas as pd
from loguru import logger
from tqdm import tqdm

from src.config import load_settings, today_kst
from src.data import daily, master, storage
from src.kis.client import KISApiError, KISClient
from src.logging_setup import setup_logging


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="KIS API 기반 일봉 초기 적재")
    p.add_argument("--years", type=int, default=5)
    p.add_argument("--from", dest="fromdate", type=str, help="YYYYMMDD")
    p.add_argument("--to", dest="todate", type=str, help="YYYYMMDD")
    p.add_argument(
        "--markets",
        type=str,
        default="KOSPI,KOSDAQ",
        help="콤마 구분 (KOSPI / KOSDAQ / KOSPI,KOSDAQ)",
    )
    p.add_argument("--limit", type=int, default=0, help="종목 수 상한 (0=무제한)")
    p.add_argument(
        "--exclude-preferred",
        action="store_true",
        help="우선주(코드 끝자리 != 0) 제외",
    )
    return p.parse_args()


def _yyyymmdd_to_date(s: str) -> date:
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def _resolve_range(args: argparse.Namespace) -> tuple[date, date]:
    today = today_kst()
    if args.fromdate and args.todate:
        return _yyyymmdd_to_date(args.fromdate), _yyyymmdd_to_date(args.todate)
    fromdate = today.replace(year=today.year - args.years)
    return fromdate, today - timedelta(days=1)


def _last_dates_per_code(data_dir) -> dict[str, date]:
    df = storage.read_daily_ohlcv(data_dir)
    if df.empty:
        return {}
    grp = df.groupby("code")["date"].max()
    out: dict[str, date] = {}
    for code, val in grp.items():
        out[code] = val.date() if hasattr(val, "date") else val
    return out


def _select_tickers(args: argparse.Namespace) -> pd.DataFrame:
    markets = {m.strip().upper() for m in args.markets.split(",") if m.strip()}
    df = master.fetch_stock_master(include_preferred=not args.exclude_preferred)
    df = df[df["market"].isin(markets)].reset_index(drop=True)
    if args.limit > 0:
        df = df.head(args.limit)
    return df


def main() -> None:
    args = _parse_args()
    settings = load_settings()
    setup_logging(settings)

    fromdate, todate = _resolve_range(args)
    logger.info(f"적재 범위: {fromdate} ~ {todate}")

    tickers_df = _select_tickers(args)
    logger.info(f"대상 종목: {len(tickers_df)} (markets={args.markets}, limit={args.limit})")

    last_dates = _last_dates_per_code(settings.data_dir)
    logger.info(f"기존 적재된 종목 수: {len(last_dates)}")

    failed: list[str] = []
    skipped = 0
    with KISClient(settings) as kis:
        for _, row in tqdm(tickers_df.iterrows(), total=len(tickers_df), desc="종목"):
            code = row["code"]
            code_from = fromdate
            if code in last_dates:
                code_from = last_dates[code] + timedelta(days=1)
                if code_from > todate:
                    skipped += 1
                    continue
            try:
                df = daily.fetch_one_ticker(kis, code, code_from, todate, adjusted=True)
                if df.empty:
                    continue
                storage.upsert_daily_ohlcv(df, settings.data_dir)
            except KISApiError as e:
                logger.error(f"{code}: KIS 에러 — {e}")
                failed.append(code)
            except Exception as e:  # noqa: BLE001
                logger.error(f"{code}: 실패 — {e}")
                failed.append(code)

    logger.info(
        f"완료. 처리={len(tickers_df)}, 건너뜀(이미최신)={skipped}, 실패={len(failed)}"
    )
    if failed:
        logger.warning(f"실패 종목 (재실행 시 자동 재시도): {failed[:20]}{'...' if len(failed) > 20 else ''}")


if __name__ == "__main__":
    main()
