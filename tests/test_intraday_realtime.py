"""src.data.intraday_realtime 단위 테스트.

KIS API 호출은 mock. 응답 필드명은 KIS 추정 — 운영 검증 필요.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import httpx

from src.data.intraday_realtime import (
    fetch_asking_price,
    fetch_ccnl_strength,
    fetch_investor_flow,
    fetch_investor_trend_estimate,
    fetch_minute_bars,
    fetch_program_trade_by_stock,
)


def _http_status_error(status: int = 500) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "https://openapi.koreainvestment.com:9443/x")
    resp = httpx.Response(status, request=req, text="server error")
    return httpx.HTTPStatusError(f"Server error '{status}'", request=req, response=resp)


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
    # fetch_minute_bars 는 KIS 누적 acml_tr_pbmn 을 분봉당 거래대금으로 diff 변환.
    # 첫 봉은 diff NaN → 0, 두번째 봉은 20.3M - 10M = 10.3M.
    assert df.iloc[0]["trading_value"] == 0
    assert df.iloc[1]["trading_value"] == 10_300_000


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


def test_fetch_minute_bars_http_500_returns_empty():
    """KIS 서버 5xx (tenacity 재시도 후 reraise) — 모니터링 tick 보호."""
    c = MagicMock()
    c.get.side_effect = _http_status_error(500)
    df = fetch_minute_bars(c, "229200")
    assert df.empty


def test_fetch_ccnl_http_500_returns_none():
    c = MagicMock()
    c.get.side_effect = _http_status_error(503)
    assert fetch_ccnl_strength(c, "229200") is None


def test_fetch_asking_price_transport_error_returns_none():
    c = MagicMock()
    c.get.side_effect = httpx.ConnectError("Connection refused")
    assert fetch_asking_price(c, "229200") is None


def test_fetch_investor_flow_http_500_returns_none():
    c = MagicMock()
    c.get.side_effect = _http_status_error(500)
    assert fetch_investor_flow(c, "229200") is None


# ── fetch_ccnl_strength ──────────────────────────────────────────────────────


def test_fetch_ccnl_basic():
    """KIS 공식 응답 스키마: output 은 체결 30건 list, 각 행에 tday_rltv (round 34)."""
    payload = {
        "output": [
            {"stck_cntg_hour": "093000", "stck_prpr": "59300", "cntg_vol": "120",
             "tday_rltv": "142.50", "prdy_ctrt": "1.20"},
            {"stck_cntg_hour": "092959", "stck_prpr": "59200", "cntg_vol": "100",
             "tday_rltv": "140.30", "prdy_ctrt": "1.05"},
        ]
    }
    result = fetch_ccnl_strength(_client(payload), "005930")
    assert result is not None
    # 가장 최신 행(stck_cntg_hour 최대)의 체결강도
    assert result["ccnl_strength"] == 142.5
    assert result["cntg_vol"] == 120


def test_fetch_ccnl_balanced():
    payload = {
        "output": [
            {"stck_cntg_hour": "093000", "cntg_vol": "100", "tday_rltv": "100.00"},
        ]
    }
    result = fetch_ccnl_strength(_client(payload), "005930")
    assert result["ccnl_strength"] == 100.0


def test_fetch_ccnl_picks_latest_row():
    """가장 최신 체결(stck_cntg_hour 큰 값)의 tday_rltv 사용 — 응답이 정순/역순
    어떻게 와도 상관없게."""
    payload = {
        "output": [
            {"stck_cntg_hour": "091500", "tday_rltv": "95.0", "cntg_vol": "50"},
            {"stck_cntg_hour": "093015", "tday_rltv": "118.5", "cntg_vol": "200"},
            {"stck_cntg_hour": "092700", "tday_rltv": "108.0", "cntg_vol": "80"},
        ]
    }
    result = fetch_ccnl_strength(_client(payload), "005930")
    assert result["ccnl_strength"] == 118.5
    assert result["cntg_vol"] == 200


def test_fetch_ccnl_legacy_output1_key():
    """일부 응답이 output1 키로 오는 경우 호환 (round 34, 보수적 fallback)."""
    payload = {
        "output1": [
            {"stck_cntg_hour": "093000", "tday_rltv": "130.0", "cntg_vol": "75"},
        ]
    }
    result = fetch_ccnl_strength(_client(payload), "005930")
    assert result is not None
    assert result["ccnl_strength"] == 130.0


def test_fetch_ccnl_empty_response():
    assert fetch_ccnl_strength(_client({}), "005930") is None


def test_fetch_ccnl_missing_strength_field_returns_nan():
    """응답에 tday_rltv 가 빈 문자열 → NaN 반환 (None 아님)."""
    import math
    payload = {
        "output": [
            {"stck_cntg_hour": "093000", "tday_rltv": "", "cntg_vol": "100"},
        ]
    }
    result = fetch_ccnl_strength(_client(payload), "005930")
    assert result is not None
    assert math.isnan(result["ccnl_strength"])


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


# ── fetch_investor_trend_estimate (HHPTJ04160200) ────────────────────────────


def _trend_payload(rows: list[dict]) -> dict:
    return {"output2": rows}


def _program_payload(rows: list[dict]) -> dict:
    return {"output": rows}


def test_trend_estimate_picks_max_bsop_hour_gb():
    """output2 list 의 max bsop_hour_gb row (가장 최신 추정 누계) 채택."""
    payload = _trend_payload([
        {"bsop_hour_gb": "1", "frgn_fake_ntby_qty": "-00000000000112000",
         "orgn_fake_ntby_qty": "000000000000000000",
         "sum_fake_ntby_qty": "-00000000000112000"},
        {"bsop_hour_gb": "5", "frgn_fake_ntby_qty": "000000000004084000",
         "orgn_fake_ntby_qty": "000000000000741000",
         "sum_fake_ntby_qty": "000000000004825000"},
        {"bsop_hour_gb": "3", "frgn_fake_ntby_qty": "000000000001441000",
         "orgn_fake_ntby_qty": "000000000000233000",
         "sum_fake_ntby_qty": "000000000001674000"},
    ])
    result = fetch_investor_trend_estimate(_client(payload), "005930")
    assert result is not None
    assert result["foreign_net_buy"] == 4_084_000  # gb=5 (max) row 채택
    assert result["institution_net_buy"] == 741_000
    assert result["bsop_hour_gb"] == 5


def test_trend_estimate_negative_value_parsed():
    """sign + zero-padded 음수 누계 (장 초반 매도 우세)."""
    payload = _trend_payload([
        {"bsop_hour_gb": "1", "frgn_fake_ntby_qty": "-00000000000500000",
         "orgn_fake_ntby_qty": "-00000000000100000"},
    ])
    result = fetch_investor_trend_estimate(_client(payload), "091340")
    assert result is not None
    assert result["foreign_net_buy"] == -500_000
    assert result["institution_net_buy"] == -100_000


def test_trend_estimate_empty_list_returns_none():
    assert fetch_investor_trend_estimate(_client({"output2": []}), "005930") is None


def test_trend_estimate_missing_output2_returns_none():
    """다른 KIS endpoint 처럼 output / output1 응답이 와도 None (이 endpoint 는 output2 만)."""
    assert fetch_investor_trend_estimate(_client({"output": [{}]}), "005930") is None


def test_trend_estimate_http_500_returns_none():
    c = MagicMock()
    c.get.side_effect = _http_status_error(500)
    assert fetch_investor_trend_estimate(c, "005930") is None


# ── fetch_program_trade_by_stock (FHPPG04650101) ──────────────────────────────


def test_program_trade_picks_first_row_as_latest():
    """output list[0] = 가장 최신 분봉 (KIS 응답 시간 desc 정렬)."""
    payload = _program_payload([
        {"bsop_hour": "143000", "stck_prpr": "299500",
         "whol_smtn_ntby_qty": "5755302",
         "whol_smtn_ntby_tr_pbmn": "1706786691000",
         "whol_ntby_vol_icdc": "-14000"},
        {"bsop_hour": "142900", "stck_prpr": "299000",
         "whol_smtn_ntby_qty": "5700000",
         "whol_smtn_ntby_tr_pbmn": "1690000000000"},
    ])
    result = fetch_program_trade_by_stock(_client(payload), "005930")
    assert result is not None
    assert result["program_net_buy"] == 5_755_302
    assert result["program_net_buy_value"] == 1_706_786_691_000
    assert result["current_price"] == 299_500
    assert result["bsop_hour"] == "143000"


def test_program_trade_empty_list_returns_none():
    assert fetch_program_trade_by_stock(_client({"output": []}), "005930") is None


def test_program_trade_missing_output_returns_none():
    assert fetch_program_trade_by_stock(_client({"output2": [{}]}), "005930") is None


def test_program_trade_http_500_returns_none():
    c = MagicMock()
    c.get.side_effect = _http_status_error(500)
    assert fetch_program_trade_by_stock(c, "005930") is None


# ── fetch_investor_flow (두 endpoint 합산) ────────────────────────────────────


def _dual_client(trend_rows: list[dict] | None, program_rows: list[dict] | None) -> MagicMock:
    """endpoint URL 별 응답 분기. trend_rows=None / program_rows=None 이면 빈 응답."""
    c = MagicMock()

    def _dispatch(path: str, tr_id: str, *args, **kwargs):
        if "investor-trend-estimate" in path:
            return {"output2": trend_rows if trend_rows is not None else []}
        if "program-trade-by-stock" in path:
            return {"output": program_rows if program_rows is not None else []}
        return {}

    c.get.side_effect = _dispatch
    return c


def test_investor_flow_combines_both_endpoints():
    """두 endpoint 모두 정상 응답 — 외인/기관 + 프로그램 통합 + _value 추정."""
    trend = [
        {"bsop_hour_gb": "5", "frgn_fake_ntby_qty": "000000000004084000",
         "orgn_fake_ntby_qty": "000000000000741000"},
    ]
    program = [
        {"bsop_hour": "143000", "stck_prpr": "299500",
         "whol_smtn_ntby_qty": "5755302",
         "whol_smtn_ntby_tr_pbmn": "1706786691000"},
    ]
    result = fetch_investor_flow(_dual_client(trend, program), "005930")
    assert result is not None
    assert result["foreign_net_buy"] == 4_084_000
    assert result["institution_net_buy"] == 741_000
    assert result["individual_net_buy"] is None  # 신규 endpoint 미제공
    assert result["program_net_buy"] == 5_755_302
    assert result["program_net_buy_value"] == 1_706_786_691_000
    # _value 추정: 수량 × stck_prpr (299500)
    assert result["foreign_net_buy_value"] == 4_084_000 * 299_500
    assert result["institution_net_buy_value"] == 741_000 * 299_500
    assert result["bsop_hour_gb"] == 5
    assert result["bsop_hour"] == "143000"


def test_investor_flow_trend_only_program_missing():
    """프로그램 endpoint 실패 — 외인/기관만 채우고 program_value=0."""
    trend = [{"bsop_hour_gb": "3", "frgn_fake_ntby_qty": "1000",
              "orgn_fake_ntby_qty": "500"}]
    result = fetch_investor_flow(_dual_client(trend, None), "005930")
    assert result is not None
    assert result["foreign_net_buy"] == 1000
    assert result["institution_net_buy"] == 500
    assert result["program_net_buy"] == 0
    assert result["program_net_buy_value"] == 0
    # price 0 → _value 0 (현재가 추정 불가)
    assert result["foreign_net_buy_value"] == 0
    assert result["institution_net_buy_value"] == 0


def test_investor_flow_program_only_trend_missing():
    """trend endpoint 실패 — 프로그램만 채우고 외인/기관 0."""
    program = [{"bsop_hour": "143000", "stck_prpr": "10000",
                "whol_smtn_ntby_qty": "100",
                "whol_smtn_ntby_tr_pbmn": "1000000"}]
    result = fetch_investor_flow(_dual_client(None, program), "005930")
    assert result is not None
    assert result["foreign_net_buy"] == 0
    assert result["institution_net_buy"] == 0
    assert result["program_net_buy"] == 100
    assert result["program_net_buy_value"] == 1_000_000
    # 외인/기관 0 × price → 0
    assert result["foreign_net_buy_value"] == 0


def test_investor_flow_both_failed_returns_none():
    """두 endpoint 모두 실패 — None."""
    assert fetch_investor_flow(_dual_client(None, None), "005930") is None


def test_investor_flow_all_zero_returns_dict():
    """KIS 정상 응답이지만 추정 누계가 모두 0 (장 시작 직전) — None 아닌 zero dict.

    카드 렌더가 0 일 때 라인 생략하는 별도 정책 (render.py) 으로 처리.
    """
    trend = [{"bsop_hour_gb": "1", "frgn_fake_ntby_qty": "0",
              "orgn_fake_ntby_qty": "0"}]
    program = [{"bsop_hour": "090100", "stck_prpr": "10000",
                "whol_smtn_ntby_qty": "0",
                "whol_smtn_ntby_tr_pbmn": "0"}]
    result = fetch_investor_flow(_dual_client(trend, program), "005930")
    assert result is not None
    assert result["foreign_net_buy"] == 0
    assert result["institution_net_buy"] == 0
    assert result["program_net_buy"] == 0
