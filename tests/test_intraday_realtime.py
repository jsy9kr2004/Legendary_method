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


def test_fetch_investor_list_uses_latest_by_time():
    """round 36: output 이 list 면 시간 필드(stck_cntg_hour) max 행을 채택.

    round 22 까지는 `out[0]` (첫 행)을 잡아서 빈/0 인 행이 나오면 전부 0 으로
    들어왔던 게 카드 라인 제거의 진짜 원인 (round 33/34 체결강도와 동일 패턴).
    """
    payload = {
        "output": [
            {"stck_cntg_hour": "090100", "frgn_ntby_qty": "0",
             "orgn_ntby_qty": "0", "prsn_ntby_qty": "0",
             "pgtr_ntby_qty": "0"},
            {"stck_cntg_hour": "143000", "frgn_ntby_qty": "10000",
             "orgn_ntby_qty": "5000", "prsn_ntby_qty": "-15000",
             "pgtr_ntby_qty": "3000"},
        ]
    }
    result = fetch_investor_flow(_client(payload), "005930")
    assert result is not None
    # 143000 행이 채택돼야 함 (첫 행이 아니라)
    assert result["foreign_net_buy"] == 10000
    assert result["institution_net_buy"] == 5000
    assert result["individual_net_buy"] == -15000
    assert result["program_net_buy"] == 3000


def test_fetch_investor_list_no_time_field_uses_last_row():
    """시간 필드 없는 list 면 마지막 행 채택 (KIS 가 보통 시간 오름차순 반환)."""
    payload = {
        "output": [
            {"frgn_ntby_qty": "0"},
            {"frgn_ntby_qty": "0"},
            {"frgn_ntby_qty": "9999", "orgn_ntby_qty": "1111"},
        ]
    }
    result = fetch_investor_flow(_client(payload), "005930")
    assert result is not None
    assert result["foreign_net_buy"] == 9999
    assert result["institution_net_buy"] == 1111


def test_fetch_investor_output1_fallback():
    """output 키가 없고 output1 으로 오는 경우 — 다른 KIS TR 들과의 호환."""
    payload = {
        "output1": {
            "frgn_ntby_qty": "7777",
            "orgn_ntby_qty": "8888",
            "prsn_ntby_qty": "0",
            "pgtr_ntby_qty": "0",
        }
    }
    result = fetch_investor_flow(_client(payload), "005930")
    assert result is not None
    assert result["foreign_net_buy"] == 7777
    assert result["institution_net_buy"] == 8888


def test_fetch_investor_all_zero_returns_zero_dict():
    """모두 0 응답 — 장 시작 직후 정상 케이스라 None 이 아니라 zero dict 반환.

    DEBUG 진단 로그는 찍히지만 호출자는 dict 형태로 정상 처리해야 함
    (카드 렌더는 모두 0 이면 라인 자체 생략 — 별도 회귀 케이스 참고).
    """
    payload = {
        "output": {
            "frgn_ntby_qty": "0", "orgn_ntby_qty": "0",
            "prsn_ntby_qty": "0", "pgtr_ntby_qty": "0",
        }
    }
    result = fetch_investor_flow(_client(payload), "005930")
    assert result is not None
    assert result["foreign_net_buy"] == 0
    assert result["institution_net_buy"] == 0
    assert result["program_net_buy"] == 0


def test_fetch_investor_empty_response():
    assert fetch_investor_flow(_client({}), "005930") is None


def test_fetch_investor_empty_list_response():
    """output 이 빈 list 면 None."""
    assert fetch_investor_flow(_client({"output": []}), "005930") is None
