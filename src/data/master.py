"""종목 마스터 fetcher.

KOSPI + KOSDAQ 전종목의 (코드, 종목명, 시장, 시가총액) 스냅샷을 받아
`{DATA_DIR}/meta/stocks.parquet`에 덮어쓴다.

상장일(`listed_at`)은 pykrx로 직접 조회 불가하여 None 으로 둔다 (TODO).
매일 16:30 cron 호출.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10), reraise=True)
def fetch_stock_master(target: date) -> pd.DataFrame:
    """target 기준 KOSPI+KOSDAQ 종목 마스터.

    반환 컬럼: code, name, market, market_cap, listed_at
    """
    from pykrx import stock

    yyyymmdd = target.strftime("%Y%m%d")
    rows: list[dict] = []

    for market in ("KOSPI", "KOSDAQ"):
        tickers = stock.get_market_ticker_list(yyyymmdd, market=market)
        cap_df = stock.get_market_cap(yyyymmdd, market=market)

        for code in tickers:
            name = stock.get_market_ticker_name(code)
            mc = 0
            if code in cap_df.index and "시가총액" in cap_df.columns:
                mc = int(cap_df.loc[code, "시가총액"])
            rows.append(
                {
                    "code": code,
                    "name": name,
                    "market": market,
                    "market_cap": mc,
                    "listed_at": None,
                }
            )

    return pd.DataFrame(rows, columns=["code", "name", "market", "market_cap", "listed_at"])
