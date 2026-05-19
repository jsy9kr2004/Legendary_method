"""src.data.index 단위 테스트. KIS API 호출 mock."""
from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pandas as pd

from src.data.index import (
    KOSPI_CODE,
    compute_market_stats,
    fetch_index_daily,
    fetch_index_daily_range,
    fetch_index_quote,
    init_index_daily,
    update_index_daily,
)


def _client(payload: dict) -> MagicMock:
    c = MagicMock()
    c.get.return_value = payload
    return c


def _http_status_error(status: int = 500) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "https://openapi.koreainvestment.com:9443/x")
    resp = httpx.Response(status, request=req, text="server error")
    return httpx.HTTPStatusError(f"Server error '{status}'", request=req, response=resp)


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


def test_fetch_index_quote_http_500_returns_none():
    """KIS 서버 5xx (tenacity 재시도 후 reraise) — 모닝/결정 레포트 보호."""
    c = MagicMock()
    c.get.side_effect = _http_status_error(500)
    assert fetch_index_quote(c, KOSPI_CODE) is None


def test_fetch_index_daily_http_500_returns_empty():
    c = MagicMock()
    c.get.side_effect = _http_status_error(502)
    df = fetch_index_daily(c, KOSPI_CODE)
    assert df.empty


def test_fetch_index_quote_handles_list_output():
    """output 이 list 면 첫 행 사용."""
    payload = {"output": [{"bstp_nmix_prpr": "2600", "prdy_ctrt": "1.0"}]}
    q = fetch_index_quote(_client(payload), KOSPI_CODE)
    assert q is not None
    assert q["current"] == 2600.0


def test_compute_change_rate_fallback_uses_kis_value():
    """KIS prdy_ctrt 가 정상이면 그대로 사용."""
    from src.data.index import _compute_change_rate_fallback
    assert _compute_change_rate_fallback({"change_rate": 1.25, "current": 7312.47, "prev_close": 7222.0}) == 1.25


def test_compute_change_rate_fallback_computes_from_prev_close():
    """KIS prdy_ctrt 비었으면 current/prev_close 로 계산. 2026-05-19 사용자 보고
    회귀 — KOSPI 7312.47 (—) → 계산값으로 보강."""
    from src.data.index import _compute_change_rate_fallback
    cr = _compute_change_rate_fallback({
        "change_rate": float("nan"),
        "current": 7312.47,
        "prev_close": 7200.0,
    })
    assert abs(cr - 1.5621) < 0.01  # (7312.47-7200)/7200 * 100


def test_compute_change_rate_fallback_nan_when_no_data():
    """current 도 prev_close 도 비면 NaN."""
    from src.data.index import _compute_change_rate_fallback
    cr = _compute_change_rate_fallback({"change_rate": float("nan")})
    assert cr != cr  # NaN


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


# ── fetch_index_daily_range (페이지네이션) ────────────────────────────────────

from datetime import date, timedelta  # noqa: E402


def _page_payload(records: list[tuple[str, float]]) -> dict:
    """(date_str, close) tuples → KIS output2 payload."""
    return {
        "output2": [
            {"stck_bsop_date": d, "bstp_nmix_prpr": str(c)}
            for d, c in records
        ]
    }


def test_fetch_index_daily_range_single_page():
    payload = _page_payload([("20260504", 2680.0), ("20260506", 2701.0)])
    c = _client(payload)
    # 두 번째 호출은 빈 응답 → 페이지네이션 종료
    c.get.side_effect = [payload, {"output2": []}]

    df = fetch_index_daily_range(c, KOSPI_CODE,
                                 start_date=date(2026, 5, 1),
                                 end_date=date(2026, 5, 6))
    assert len(df) == 2
    assert df.iloc[0]["date"] == date(2026, 5, 4)
    assert df.iloc[1]["close"] == 2701.0


def test_fetch_index_daily_range_pagination():
    """한 호출당 응답 100건 제한 → 여러 페이지 누적."""
    # 페이지 1: 2026-04-29 ~ 2026-05-06 (8건)
    page1 = _page_payload([
        (f"202605{d:02d}", 2700.0 + d) for d in [6, 5, 4]
    ] + [(f"202604{d:02d}", 2680.0 + d) for d in [30, 29]])
    # 페이지 2: 2026-04-25 ~ 2026-04-28 (4건)
    page2 = _page_payload([
        (f"202604{d:02d}", 2670.0 + d) for d in [28, 27, 26, 25]
    ])
    # 페이지 3: 빈 응답 → 종료
    c = MagicMock()
    c.get.side_effect = [page1, page2, {"output2": []}]

    df = fetch_index_daily_range(c, KOSPI_CODE,
                                 start_date=date(2026, 4, 25),
                                 end_date=date(2026, 5, 6))
    assert len(df) == 9
    assert df.iloc[0]["date"] == date(2026, 4, 25)
    assert df.iloc[-1]["date"] == date(2026, 5, 6)


def test_fetch_index_daily_range_dedup():
    """페이지 경계 중복 제거."""
    page1 = _page_payload([("20260506", 2700.0), ("20260505", 2695.0)])
    page2 = _page_payload([("20260505", 2695.0), ("20260504", 2680.0)])
    c = MagicMock()
    c.get.side_effect = [page1, page2, {"output2": []}]

    df = fetch_index_daily_range(c, KOSPI_CODE,
                                 start_date=date(2026, 5, 1),
                                 end_date=date(2026, 5, 6))
    assert len(df) == 3  # 5/4, 5/5, 5/6 — 5/5 중복 제거


def test_fetch_index_daily_range_max_pages_guard():
    """max_pages 도달 시 안전 종료 (무한 루프 방지)."""
    # 매번 동일 1건 반환 → 진행 없으면 next_end >= cursor_end 체크로도 종료되지만
    # 안전 가드를 명시적으로 검증
    page = _page_payload([("20260506", 2700.0)])
    c = MagicMock()
    c.get.side_effect = [page] * 100  # 무제한 응답

    df = fetch_index_daily_range(c, KOSPI_CODE,
                                 start_date=date(2026, 1, 1),
                                 end_date=date(2026, 5, 6),
                                 max_pages=3)
    assert len(df) == 1
    # call_count <= max_pages+1 (마지막 cursor_end 진행 못해서 break 가능)
    assert c.get.call_count <= 4


def test_fetch_index_daily_range_empty_response_returns_empty():
    df = fetch_index_daily_range(_client({"output2": []}), KOSPI_CODE,
                                 start_date=date(2026, 5, 1),
                                 end_date=date(2026, 5, 6))
    assert df.empty


def test_fetch_index_daily_range_invalid_range():
    """start > end 면 빈 DF."""
    df = fetch_index_daily_range(_client({}), KOSPI_CODE,
                                 start_date=date(2026, 5, 6),
                                 end_date=date(2026, 5, 1))
    assert df.empty


# ── init_index_daily / update_index_daily ────────────────────────────────────

def test_init_index_daily_writes_parquet(tmp_path):
    page = _page_payload([
        (f"202605{d:02d}", 2700.0 + d) for d in [6, 5, 4]
    ])
    c = MagicMock()
    c.get.side_effect = [page, {"output2": []}, page, {"output2": []}]
    # 위 4개 응답: KOSPI 1 page + empty + KOSDAQ 1 page + empty

    result = init_index_daily(c, tmp_path, years=1,
                              today=date(2026, 5, 6))
    assert result[KOSPI_CODE] == 3

    from src.data.index_storage import read_index_daily
    df = read_index_daily(tmp_path, KOSPI_CODE)
    assert len(df) == 3


def test_update_index_daily_skips_when_no_prior(tmp_path):
    """저장된 적이 없으면 update 는 skip (init 필요)."""
    c = MagicMock()
    result = update_index_daily(c, tmp_path, index_codes=(KOSPI_CODE,),
                                today=date(2026, 5, 6))
    assert result[KOSPI_CODE] == 0
    c.get.assert_not_called()


def test_update_index_daily_skips_when_already_today(tmp_path):
    """이미 오늘까지 적재된 경우 skip."""
    from src.data.index_storage import write_index_daily
    write_index_daily(
        pd.DataFrame([{"date": date(2026, 5, 6), "close": 2701.0}]),
        tmp_path, KOSPI_CODE,
    )
    c = MagicMock()
    result = update_index_daily(c, tmp_path, index_codes=(KOSPI_CODE,),
                                today=date(2026, 5, 6))
    assert result[KOSPI_CODE] == 0
    c.get.assert_not_called()


def test_update_index_daily_fetches_gap(tmp_path):
    """마지막 적재일 + 1 ~ 오늘 만 fetch."""
    from src.data.index_storage import read_index_daily, write_index_daily
    write_index_daily(
        pd.DataFrame([{"date": date(2026, 5, 4), "close": 2680.0}]),
        tmp_path, KOSPI_CODE,
    )
    page = _page_payload([("20260506", 2701.0), ("20260505", 2695.0)])
    c = MagicMock()
    c.get.side_effect = [page, {"output2": []}]

    result = update_index_daily(c, tmp_path, index_codes=(KOSPI_CODE,),
                                today=date(2026, 5, 6))
    assert result[KOSPI_CODE] == 2  # 5/5, 5/6 신규
    assert len(read_index_daily(tmp_path, KOSPI_CODE)) == 3
