"""src.data.intraday_realtime 단위 테스트.

KIS API 호출은 mock. 응답 필드명은 KIS 추정 — 운영 검증 필요.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from src.data.intraday_realtime import (
    fetch_asking_price,
    fetch_ccnl_strength,
    fetch_investor_flow,
    fetch_minute_bars,
)


def _client(payload: dict) -> MagicMock:
    c = MagicMock()
    c.get.return_value = payload
    return c


# ── fetch_minute_bars ────────────────────────────────────────────────────────


def test_fetch_minute_bars_parses_output2():
    payload = {
        "output2": [
            {
                "stck_bsop_date": "20260510",
                "stck_cntg_hour": "090100",
                "stck_oprc": "10000", "stck_hgpr": "10100",
                "stck_lwpr": "9950", "stck_prpr": "10050",
                "cntg_vol": "1000", "acml_tr_pbmn": "10000000",
            },
            {
                "stck_bsop_date": "20260510",
                "stck_cntg_hour": "090200",
                "stck_oprc": "10050", "stck_hgpr": "10200",
                "stck_lwpr": "10000", "stck_prpr": "10180",
                "cntg_vol": "2000", "acml_tr_pbmn": "20300000",
            },
        ]
    }
    df = fetch_minute_bars(_client(payload), "005930")
    assert len(df) == 2
    assert list(df["time"]) == ["090100", "090200"]  # 오름차순
    assert df.iloc[1]["close"] == 10180
    assert df.iloc[1]["trading_value"] == 20_300_000


def test_fetch_minute_bars_empty_response():
    df = fetch_minute_bars(_client({"output2": []}), "005930")
    assert df.empty


def test_fetch_minute_bars_missing_fields_default_to_zero():
    payload = {"output2": [{"stck_cntg_hour": "090100"}]}
    df = fetch_minute_bars(_client(payload), "005930")
    assert len(df) == 1
    assert df.iloc[0]["close"] == 0
    assert df.iloc[0]["trading_value"] == 0


def test_fetch_minute_bars_api_error_returns_empty():
    from src.kis.client import KISApiError
    c = MagicMock()
    c.get.side_effect = KISApiError("ERR", "ERR", "test", {})
    df = fetch_minute_bars(c, "005930")
    assert df.empty


# ── fetch_ccnl_strength ──────────────────────────────────────────────────────


def test_fetch_ccnl_basic():
    payload = {
        "output1": {
            "cttr": "142.50",
            "shnu_cntg_smtn": "320000",
            "seln_cntg_smtn": "180000",
        }
    }
    result = fetch_ccnl_strength(_client(payload), "005930")
    assert result is not None
    assert result["ccnl_strength"] == 142.5
    assert result["buy_volume"] == 320000
    assert result["sell_volume"] == 180000
    assert result["buy_ratio"] > 60.0  # 매수 우세


def test_fetch_ccnl_balanced():
    payload = {
        "output1": {
            "cttr": "100.00",
            "shnu_cntg_smtn": "100000",
            "seln_cntg_smtn": "100000",
        }
    }
    result = fetch_ccnl_strength(_client(payload), "005930")
    assert result["buy_ratio"] == 50.0


def test_fetch_ccnl_falls_back_to_qty_fields():
    """smtn 필드 없으면 qty 필드로 fallback."""
    payload = {
        "output1": {
            "cttr": "100",
            "shnu_cntg_qty": "5000",
            "seln_cntg_qty": "5000",
        }
    }
    result = fetch_ccnl_strength(_client(payload), "005930")
    assert result is not None
    assert result["buy_volume"] == 5000


def test_fetch_ccnl_empty_response():
    assert fetch_ccnl_strength(_client({}), "005930") is None


# ── fetch_asking_price ───────────────────────────────────────────────────────


def test_fetch_asking_price_basic():
    payload = {
        "output1": {
            "askp1": "10100", "bidp1": "10090",
            "askp_rsqn1": "100", "bidp_rsqn1": "200",
            "total_askp_rsqn": "1500",
            "total_bidp_rsqn": "5000",
        }
    }
    result = fetch_asking_price(_client(payload), "005930")
    assert result is not None
    assert result["ask_total_volume"] == 1500
    assert result["bid_total_volume"] == 5000
    assert result["bid_ask_ratio"] > 3.0  # 매수세 우세


def test_fetch_asking_price_fallback_to_per_level():
    """total_*_rsqn 없으면 1~10단계 합산."""
    payload = {
        "output1": {f"askp_rsqn{i}": "10" for i in range(1, 11)}
    }
    payload["output1"].update({f"bidp_rsqn{i}": "20" for i in range(1, 11)})
    result = fetch_asking_price(_client(payload), "005930")
    assert result is not None
    assert result["ask_total_volume"] == 100
    assert result["bid_total_volume"] == 200


def test_fetch_asking_price_zero_ask_returns_nan_ratio():
    payload = {
        "output1": {"total_askp_rsqn": "0", "total_bidp_rsqn": "100"}
    }
    result = fetch_asking_price(_client(payload), "005930")
    assert result is not None
    # NaN 비교
    assert result["bid_ask_ratio"] != result["bid_ask_ratio"]


def test_fetch_asking_price_empty_response():
    assert fetch_asking_price(_client({}), "005930") is None


# ── fetch_investor_flow ──────────────────────────────────────────────────────


def test_fetch_investor_basic():
    payload = {
        "output": {
            "frgn_ntby_qty": "10000",
            "orgn_ntby_qty": "20000",
            "prsn_ntby_qty": "-30000",
            "pgtr_ntby_qty": "5000",
            "frgn_ntby_tr_pbmn": "1500000000",
        }
    }
    result = fetch_investor_flow(_client(payload), "005930")
    assert result is not None
    assert result["foreign_net_buy"] == 10000
    assert result["institution_net_buy"] == 20000
    assert result["individual_net_buy"] == -30000
    assert result["program_net_buy"] == 5000
    assert result["foreign_net_buy_value"] == 1_500_000_000


def test_fetch_investor_list_response_uses_first_row():
    """output 이 리스트면 첫 행 사용."""
    payload = {
        "output": [
            {"frgn_ntby_qty": "10000", "orgn_ntby_qty": "5000",
             "prsn_ntby_qty": "-15000"},
            {"frgn_ntby_qty": "1", "orgn_ntby_qty": "1", "prsn_ntby_qty": "1"},
        ]
    }
    result = fetch_investor_flow(_client(payload), "005930")
    assert result is not None
    assert result["foreign_net_buy"] == 10000


def test_fetch_investor_empty_response():
    assert fetch_investor_flow(_client({}), "005930") is None
