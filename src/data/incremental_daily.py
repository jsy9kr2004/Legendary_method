"""매일 장 마감 후 incremental 일봉 적재 (KIS API).

전종목 마스터 받아서 각 종목 last_loaded_date+1 ~ 어제 까지 받아 upsert.
이미 최신인 종목은 즉시 skip.

cron 매일 16:00:
    python -m src.data.incremental_daily
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta

from loguru import logger
from tqdm import tqdm

from src.config import load_settings, today_kst
from src.data import daily, master, storage
from src.kis.client import KISApiError, KISClient
from src.logging_setup import setup_logging


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="KIS API 기반 일봉 incremental")
    p.add_argument("--markets", type=str, default="KOSPI,KOSDAQ")
    return p.parse_args()


def _last_dates_per_code(data_dir) -> dict[str, date]:
    df = storage.read_daily_ohlcv(data_dir)
    if df.empty:
        return {}
    grp = df.groupby("code")["date"].max()
    out: dict[str, date] = {}
    for code, val in grp.items():
        out[code] = val.date() if hasattr(val, "date") else val
    return out


def main() -> None:
    args = _parse_args()
    settings = load_settings()
    setup_logging(settings)

    yesterday = today_kst() - timedelta(days=1)
    logger.info(f"incremental 종료일: {yesterday}")

    last_dates = _last_dates_per_code(settings.data_dir)
    if not last_dates:
        logger.warning("기존 데이터 없음. `python -m src.data.init_daily` 먼저 실행.")
        return

    markets = {m.strip().upper() for m in args.markets.split(",") if m.strip()}
    tickers_df = master.fetch_stock_master()
    tickers_df = tickers_df[tickers_df["market"].isin(markets)].reset_index(drop=True)
    logger.info(f"대상 종목: {len(tickers_df)}")

    updated = 0
    skipped = 0
    failed: list[str] = []

    with KISClient(settings) as kis:
        for _, row in tqdm(tickers_df.iterrows(), total=len(tickers_df), desc="종목"):
            code = row["code"]
            last = last_dates.get(code)
            if last is None:
                # 새 상장 또는 init 미실행 종목 — incremental 에서는 일단 skip
                skipped += 1
                continue
            code_from = last + timedelta(days=1)
            if code_from > yesterday:
                skipped += 1
                continue
            try:
                df = daily.fetch_one_ticker(kis, code, code_from, yesterday, adjusted=True)
                if df.empty:
                    continue
                storage.upsert_daily_ohlcv(df, settings.data_dir)
                updated += 1
            except KISApiError as e:
                logger.error(f"{code}: KIS 에러 — {e}")
                failed.append(code)
            except Exception as e:  # noqa: BLE001
                logger.error(f"{code}: 실패 — {e}")
                failed.append(code)

    logger.info(f"incremental 완료. 갱신={updated}, 건너뜀={skipped}, 실패={len(failed)}")


if __name__ == "__main__":
    main()
