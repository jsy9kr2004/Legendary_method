"""src.data.index 단위 테스트. KIS API 호출 mock."""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd

from src.data.index import (
    KOSPI_CODE,
    compute_market_stats,
    fetch_index_daily,
    fetch_index_quote,
)


def _client(payload: dict) -> MagicMock:
    c = MagicMock()
    c.get.return_value = payload
    return c


# ── fetch_index_quote ────────────────────────────────────────────────────────


def test_fetch_index_quote_basic():
    payload = {
        "output": {
            "bstp_nmix_prpr": "2645.32",
            "prdy_nmix": "2620.10",
            "bstp_nmix_prdy_vrss": "25.22",
            "prdy_ctrt": "0.96",
        }
    }
    q = fetch_index_quote(_client(payload), KOSPI_CODE)
    assert q is not None
    assert q["current"] == 2645.32
    assert q["prev_close"] == 2620.10
    assert q["change_rate"] == 0.96


def test_fetch_index_quote_empty_response():
    assert fetch_index_quote(_client({}), KOSPI_CODE) is None


def test_fetch_index_quote_api_error_returns_none():
    from src.kis.client import KISApiError
    c = MagicMock()
    c.get.side_effect = KISApiError("ERR", "ERR", "fail", {})
    assert fetch_index_quote(c, KOSPI_CODE) is None


def test_fetch_index_quote_handles_list_output():
    """output 이 list 면 첫 행 사용."""
    payload = {"output": [{"bstp_nmix_prpr": "2600", "prdy_ctrt": "1.0"}]}
    q = fetch_index_quote(_client(payload), KOSPI_CODE)
    assert q is not None
    assert q["current"] == 2600.0


# ── fetch_index_daily ────────────────────────────────────────────────────────


def test_fetch_index_daily_basic():
    payload = {
        "output2": [
            {"stck_bsop_date": "20260510", "bstp_nmix_prpr": "2645.0"},
            {"stck_bsop_date": "20260509", "bstp_nmix_prpr": "2620.0"},
        ]
    }
    df = fetch_index_daily(_client(payload), KOSPI_CODE)
    assert len(df) == 2
    # 오름차순 정렬
    assert df.iloc[0]["date"] == "20260509"
    assert df.iloc[1]["close"] == 2645.0


def test_fetch_index_daily_empty():
    df = fetch_index_daily(_client({"output2": []}), KOSPI_CODE)
    assert df.empty


def test_fetch_index_daily_skips_invalid_rows():
    payload = {
        "output2": [
            {"stck_bsop_date": "20260510", "bstp_nmix_prpr": "2645.0"},
            {"stck_bsop_date": "", "bstp_nmix_prpr": "100"},  # 날짜 없음 → skip
            {"stck_bsop_date": "20260509", "bstp_nmix_prpr": "abc"},  # NaN → skip
        ]
    }
    df = fetch_index_daily(_client(payload), KOSPI_CODE)
    assert len(df) == 1


# ── compute_market_stats ─────────────────────────────────────────────────────


def test_compute_market_stats_full():
    """quote 와 daily 모두 응답 시 모든 필드 채워짐."""
    # quote 호출 두 번 + daily 호출 한 번.
    closes_kospi = [2400.0 + i * 0.1 for i in range(220)]
    daily_payload = {
        "output2": [
            {"stck_bsop_date": f"20{20+i:04d}", "bstp_nmix_prpr": str(c)}
            for i, c in enumerate(closes_kospi)
        ]
    }
    quote_payload_kospi = {
        "output": {"bstp_nmix_prpr": "2421.9", "prdy_ctrt": "0.5"}
    }
    quote_payload_kosdaq = {
        "output": {"bstp_nmix_prpr": "880.5", "prdy_ctrt": "-0.3"}
    }

    c = MagicMock()
    c.get.side_effect = [quote_payload_kospi, quote_payload_kosdaq, daily_payload]

    stats = compute_market_stats(c)
    assert stats["kospi_current"] == 2421.9
    assert stats["kospi_change_rate"] == 0.5
    assert stats["kosdaq_current"] == 880.5
    assert stats["kosdaq_change_rate"] == -0.3
    assert "kospi_ma200" in stats
    assert "kospi_above_ma200" in stats
    assert "kospi_60d_return" in stats


def test_compute_market_stats_partial_failure():
    """일부 호출 실패해도 가능한 것만 반환."""
    quote_kospi = {"output": {"bstp_nmix_prpr": "2400", "prdy_ctrt": "0.1"}}

    c = MagicMock()
    c.get.side_effect = [quote_kospi, {}, {}]  # KOSDAQ + daily 빈 응답

    stats = compute_market_stats(c)
    assert stats["kospi_current"] == 2400
    assert "kospi_ma200" not in stats   # 일별 응답 비어 있음
    assert "kosdaq_current" not in stats


def test_compute_market_stats_short_history_no_ma200():
    """일별 기간이 200일 미만이면 ma200 미산출, 60일 수익률은 가능."""
    closes = [2400.0 + i * 0.1 for i in range(80)]  # 80일치
    daily_payload = {
        "output2": [
            {"stck_bsop_date": f"20{20+i:04d}", "bstp_nmix_prpr": str(c)}
            for i, c in enumerate(closes)
        ]
    }
    quote_kospi = {"output": {"bstp_nmix_prpr": "2407.9", "prdy_ctrt": "0.0"}}
    quote_kosdaq = {"output": {"bstp_nmix_prpr": "850", "prdy_ctrt": "0.0"}}

    c = MagicMock()
    c.get.side_effect = [quote_kospi, quote_kosdaq, daily_payload]

    stats = compute_market_stats(c)
    assert "kospi_ma200" not in stats
    assert "kospi_60d_return" in stats  # 80 ≥ 60
