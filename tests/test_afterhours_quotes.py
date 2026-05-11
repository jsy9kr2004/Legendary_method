"""src.data.afterhours_quotes 단위 테스트.

KIS API 호출은 mock. 응답 필드명은 KIS Developer Portal 기준 추정.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from src.data.afterhours_quotes import fetch_afterhours_quote, fetch_afterhours_quotes
from src.kis.client import KISApiError


def _client(payload: dict) -> MagicMock:
    c = MagicMock()
    c.get.return_value = payload
    return c


def test_fetch_afterhours_quote_parses_output():
    payload = {
        "output": {
            "hts_kor_isnm": "제룡전기",
            "stck_prpr": "95000",
            "prdy_vrss": "3700",
            "prdy_ctrt": "4.05",
        }
    }
    q = fetch_afterhours_quote(_client(payload), "075180")
    assert q is not None
    assert q["code"] == "075180"
    assert q["name"] == "제룡전기"
    assert q["price"] == 95000
    assert q["prev_close"] == 91300  # price - change
    assert q["change_pct"] == 4.05


def test_fetch_afterhours_quote_empty_output_returns_none():
    assert fetch_afterhours_quote(_client({"output": {}}), "075180") is None
    assert fetch_afterhours_quote(_client({}), "075180") is None


def test_fetch_afterhours_quote_zero_price_returns_none():
    """가격이 0/누락이면 시간외 미체결로 보고 None."""
    payload = {"output": {"hts_kor_isnm": "X", "stck_prpr": "0"}}
    assert fetch_afterhours_quote(_client(payload), "075180") is None


def test_fetch_afterhours_quote_api_error_returns_none():
    """KIS rt_cd != '0' 시 None (사후 발송은 막지 않음)."""
    c = MagicMock()
    c.get.side_effect = KISApiError("1", "X", "휴장", {})
    assert fetch_afterhours_quote(c, "075180") is None


def test_fetch_afterhours_quotes_skips_failures():
    """일부 종목 실패해도 나머지는 결과에 포함."""
    c = MagicMock()
    c.get.side_effect = [
        {"output": {"hts_kor_isnm": "A", "stck_prpr": "10000",
                    "prdy_vrss": "500", "prdy_ctrt": "5.26"}},
        KISApiError("1", "X", "휴장", {}),
        {"output": {"hts_kor_isnm": "C", "stck_prpr": "20000",
                    "prdy_vrss": "-300", "prdy_ctrt": "-1.48"}},
    ]
    quotes = fetch_afterhours_quotes(c, ["111111", "222222", "333333"])
    assert [q["code"] for q in quotes] == ["111111", "333333"]
    assert quotes[1]["name"] == "C"
    assert quotes[1]["prev_close"] == 20300


def test_fetch_afterhours_quotes_pads_code_to_6_digits():
    """4~5자리 코드는 6자리로 zero-pad."""
    c = MagicMock()
    c.get.return_value = {
        "output": {"hts_kor_isnm": "X", "stck_prpr": "1000",
                   "prdy_vrss": "0", "prdy_ctrt": "0.0"}
    }
    quotes = fetch_afterhours_quotes(c, ["75180"])
    assert quotes[0]["code"] == "075180"
