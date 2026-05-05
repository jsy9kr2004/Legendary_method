"""src.data.daily 테스트. KIS client.get 은 mock."""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pandas as pd

from src.data import daily


def _kis_response(rows: list[dict]) -> dict:
    return {
        "rt_cd": "0",
        "msg_cd": "OK",
        "msg1": "정상",
        "output1": {},
        "output2": rows,
    }


def _bsop(d: str, oprc: int, hgpr: int, lwpr: int, clpr: int, vol: int, val: int) -> dict:
    return {
        "stck_bsop_date": d,
        "stck_oprc": str(oprc),
        "stck_hgpr": str(hgpr),
        "stck_lwpr": str(lwpr),
        "stck_clpr": str(clpr),
        "acml_vol": str(vol),
        "acml_tr_pbmn": str(val),
    }


def test_fetch_one_ticker_single_chunk():
    rows = [
        _bsop("20250505", 70500, 71500, 70000, 71000, 1_100_000, 78_000_000_000),
        _bsop("20250502", 70000, 71000, 69500, 70500, 1_000_000, 71_000_000_000),
    ]
    client = MagicMock()
    # 첫 호출에 두 row, 그 이전 청크는 빈 응답
    client.get.side_effect = [_kis_response(rows), _kis_response([])]

    df = daily.fetch_one_ticker(client, "005930", date(2025, 5, 1), date(2025, 5, 5))

    assert list(df.columns) == [
        "code", "date", "open", "high", "low", "close",
        "volume", "trading_value", "change_rate",
    ]
    assert len(df) == 2
    assert df.iloc[0]["date"] == date(2025, 5, 2)
    assert df.iloc[0]["open"] == 70000
    assert df.iloc[1]["close"] == 71000
    assert (df["code"] == "005930").all()


def test_fetch_one_ticker_empty_terminates():
    client = MagicMock()
    client.get.return_value = _kis_response([])

    df = daily.fetch_one_ticker(client, "000000", date(2025, 5, 1), date(2025, 5, 5))
    assert df.empty
    # 첫 청크 빈응답이면 즉시 종료
    assert client.get.call_count == 1


def test_fetch_one_ticker_chunked():
    """긴 기간은 90일 청크로 분할. 두 청크 후 빈응답으로 종료."""
    chunk1 = [_bsop("20250502", 70000, 71000, 69500, 70500, 1000, 70_000_000)]
    chunk2 = [_bsop("20250102", 65000, 66000, 64500, 65500, 2000, 130_000_000)]
    client = MagicMock()
    client.get.side_effect = [
        _kis_response(chunk1),
        _kis_response(chunk2),
        _kis_response([]),
    ]

    df = daily.fetch_one_ticker(client, "005930", date(2024, 1, 1), date(2025, 5, 5))

    assert len(df) == 2
    assert sorted(df["date"]) == [date(2025, 1, 2), date(2025, 5, 2)]
    assert client.get.call_count >= 2


def test_fetch_one_ticker_invalid_range():
    client = MagicMock()
    df = daily.fetch_one_ticker(client, "005930", date(2025, 5, 5), date(2025, 5, 1))
    assert df.empty
    client.get.assert_not_called()


def test_change_rate_is_na():
    """change_rate 는 적재 시점에 NaN. pct_change 는 분석 단계에서."""
    rows = [_bsop("20250502", 70000, 71000, 69500, 70500, 1_000_000, 71_000_000_000)]
    client = MagicMock()
    client.get.side_effect = [_kis_response(rows), _kis_response([])]

    df = daily.fetch_one_ticker(client, "005930", date(2025, 5, 1), date(2025, 5, 5))
    assert pd.isna(df.iloc[0]["change_rate"])


def test_dtypes_are_integer():
    rows = [_bsop("20250502", 70000, 71000, 69500, 70500, 1_000_000, 71_000_000_000)]
    client = MagicMock()
    client.get.side_effect = [_kis_response(rows), _kis_response([])]

    df = daily.fetch_one_ticker(client, "005930", date(2025, 5, 1), date(2025, 5, 5))
    # nullable Int64
    assert str(df["open"].dtype) == "Int64"
    assert str(df["volume"].dtype) == "Int64"
