"""src.data.daily 테스트. pykrx 호출은 mock으로 차단."""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest

from src.data import daily


def _fake_by_date_response() -> pd.DataFrame:
    """pykrx.get_market_ohlcv_by_date 응답 모방 (한국어 컬럼, 날짜 index)."""
    df = pd.DataFrame(
        {
            "시가": [70000, 70500],
            "고가": [71000, 71500],
            "저가": [69500, 70000],
            "종가": [70500, 71000],
            "거래량": [1_000_000, 1_100_000],
            "거래대금": [71_000_000_000, 78_000_000_000],
            "등락률": [1.5, 0.7],
        },
        index=pd.to_datetime(["2025-05-02", "2025-05-05"]),
    )
    df.index.name = "날짜"
    return df


def _fake_by_ticker_response() -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "시가": [70000, 200000],
            "고가": [71000, 205000],
            "저가": [69500, 199000],
            "종가": [70500, 204000],
            "거래량": [1_000_000, 500_000],
            "거래대금": [71_000_000_000, 102_000_000_000],
            "등락률": [1.5, 2.0],
        },
        index=["005930", "000660"],
    )
    df.index.name = "티커"
    return df


def test_fetch_one_ticker_normalizes_columns():
    with patch("pykrx.stock.get_market_ohlcv_by_date", return_value=_fake_by_date_response()):
        df = daily.fetch_one_ticker("005930", date(2025, 5, 2), date(2025, 5, 5))

    assert list(df.columns) == [
        "code", "date", "open", "high", "low", "close",
        "volume", "trading_value", "change_rate",
    ]
    assert (df["code"] == "005930").all()
    assert df.iloc[0]["open"] == 70000
    assert df.iloc[0]["date"] == date(2025, 5, 2)
    assert df.iloc[1]["close"] == 71000


def test_fetch_one_ticker_empty_response():
    with patch("pykrx.stock.get_market_ohlcv_by_date", return_value=pd.DataFrame()):
        df = daily.fetch_one_ticker("000000", date(2025, 5, 2), date(2025, 5, 5))
    assert df.empty
    assert list(df.columns) == [
        "code", "date", "open", "high", "low", "close",
        "volume", "trading_value", "change_rate",
    ]


def test_fetch_all_tickers_for_date_normalizes():
    with patch("pykrx.stock.get_market_ohlcv_by_ticker", return_value=_fake_by_ticker_response()):
        df = daily.fetch_all_tickers_for_date(date(2025, 5, 2), "KOSPI")

    assert len(df) == 2
    assert (df["date"] == date(2025, 5, 2)).all()
    assert set(df["code"]) == {"005930", "000660"}
    samsung = df.loc[df["code"] == "005930"].iloc[0]
    assert samsung["open"] == 70000
    assert samsung["volume"] == 1_000_000


def test_fetch_all_tickers_for_date_empty():
    with patch("pykrx.stock.get_market_ohlcv_by_ticker", return_value=pd.DataFrame()):
        df = daily.fetch_all_tickers_for_date(date(2025, 5, 3), "KOSPI")  # 토요일
    assert df.empty


def test_fetch_all_market_concatenates_kospi_kosdaq():
    kospi = _fake_by_ticker_response()
    kosdaq = pd.DataFrame(
        {
            "시가": [50000],
            "고가": [51000],
            "저가": [49000],
            "종가": [50500],
            "거래량": [200_000],
            "거래대금": [10_100_000_000],
            "등락률": [1.0],
        },
        index=["091990"],
    )
    kosdaq.index.name = "티커"

    def side_effect(_target, market):
        return kospi if market == "KOSPI" else kosdaq

    with patch("pykrx.stock.get_market_ohlcv_by_ticker", side_effect=side_effect):
        df = daily.fetch_all_market_for_date(date(2025, 5, 2))

    assert len(df) == 3
    assert set(df["code"]) == {"005930", "000660", "091990"}
