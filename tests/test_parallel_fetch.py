"""src.dashboard.parallel_fetch 단위 테스트.

- fetch_stock_bundle: API 별 예외 격리.
- fetch_bundles_parallel: 종목 격리, 중복 제거, 응답 → code 매핑 정확성.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.dashboard.parallel_fetch import (
    StockBundle,
    fetch_bundles_parallel,
    fetch_stock_bundle,
)


def test_fetch_stock_bundle_aggregates_4_apis():
    """4 API 응답이 그대로 StockBundle 필드에 담긴다."""
    fake_bars = pd.DataFrame([{"time": "0930", "close": 1000}])
    with patch("src.dashboard.parallel_fetch.fetch_minute_bars",
               return_value=fake_bars) as mb, \
         patch("src.dashboard.parallel_fetch.fetch_ccnl_strength",
               return_value={"ccnl_strength": 120.0}) as cc, \
         patch("src.dashboard.parallel_fetch.fetch_asking_price",
               return_value={"bid_ask_ratio": 1.5}) as ap, \
         patch("src.dashboard.parallel_fetch.fetch_investor_flow",
               return_value={"foreign_net_buy": 100}) as iv:
        bundle = fetch_stock_bundle(MagicMock(), "005930")
    assert bundle.code == "005930"
    assert bundle.bars is fake_bars
    assert bundle.ccnl == {"ccnl_strength": 120.0}
    assert bundle.asking == {"bid_ask_ratio": 1.5}
    assert bundle.investor == {"foreign_net_buy": 100}
    # 종목당 4 API 모두 호출 (이전 직렬 구조와 동일 호출 횟수)
    assert mb.call_count == 1
    assert cc.call_count == 1
    assert ap.call_count == 1
    assert iv.call_count == 1


def test_fetch_stock_bundle_isolates_api_failure():
    """한 API 가 예외 던져도 다른 API 결과는 살아있음."""
    with patch("src.dashboard.parallel_fetch.fetch_minute_bars",
               side_effect=RuntimeError("network")), \
         patch("src.dashboard.parallel_fetch.fetch_ccnl_strength",
               return_value={"ccnl_strength": 100.0}), \
         patch("src.dashboard.parallel_fetch.fetch_asking_price",
               return_value=None), \
         patch("src.dashboard.parallel_fetch.fetch_investor_flow",
               return_value=None):
        bundle = fetch_stock_bundle(MagicMock(), "005930")
    assert bundle.code == "005930"
    assert bundle.bars.empty  # 실패 시 빈 DataFrame
    assert bundle.ccnl == {"ccnl_strength": 100.0}


def test_fetch_bundles_parallel_returns_one_bundle_per_code():
    """codes 입력 N개 → bundle dict 키 N개. 응답이 다른 종목으로 mix-up 되지 않음."""

    def _fake_bars(client, code, target_time=None):
        # 종목별 식별 가능한 marker — race 시 mix-up 회귀 방지
        return pd.DataFrame([{"code": code, "close": int(code[:6])}])

    def _fake_ccnl(client, code):
        return {"ccnl_strength": float(int(code[:6]) % 200)}

    with patch("src.dashboard.parallel_fetch.fetch_minute_bars",
               side_effect=_fake_bars), \
         patch("src.dashboard.parallel_fetch.fetch_ccnl_strength",
               side_effect=_fake_ccnl), \
         patch("src.dashboard.parallel_fetch.fetch_asking_price",
               return_value=None), \
         patch("src.dashboard.parallel_fetch.fetch_investor_flow",
               return_value=None):
        bundles = fetch_bundles_parallel(
            MagicMock(), ["005930", "000660", "091340"], max_workers=4,
        )
    assert set(bundles.keys()) == {"005930", "000660", "091340"}
    for code, bundle in bundles.items():
        # bars 의 marker 가 자기 code 와 일치 → race 시 응답 mix-up 없음
        assert bundle.bars.iloc[0]["code"] == code
        assert bundle.ccnl["ccnl_strength"] == float(int(code) % 200)


def test_fetch_bundles_parallel_isolates_stock_failure():
    """한 종목 전체 fetch 가 죽어도 나머지 종목은 정상 반환."""

    def _fake_bars(client, code, target_time=None):
        if code == "BAD000":
            raise RuntimeError("KIS 5xx")
        return pd.DataFrame([{"code": code, "close": 1000}])

    with patch("src.dashboard.parallel_fetch.fetch_minute_bars",
               side_effect=_fake_bars), \
         patch("src.dashboard.parallel_fetch.fetch_ccnl_strength",
               return_value=None), \
         patch("src.dashboard.parallel_fetch.fetch_asking_price",
               return_value=None), \
         patch("src.dashboard.parallel_fetch.fetch_investor_flow",
               return_value=None):
        bundles = fetch_bundles_parallel(
            MagicMock(), ["005930", "BAD000", "000660"], max_workers=4,
        )
    assert set(bundles.keys()) == {"005930", "BAD000", "000660"}
    assert not bundles["005930"].bars.empty
    assert bundles["BAD000"].bars.empty  # 격리됨, 나머지 영향 X
    assert not bundles["000660"].bars.empty


def test_fetch_bundles_parallel_empty_codes_returns_empty():
    bundles = fetch_bundles_parallel(MagicMock(), [])
    assert bundles == {}


def test_fetch_bundles_parallel_dedupes_codes():
    """중복 입력은 한 번만 fetch."""
    call_count = {"n": 0}

    def _fake_bars(client, code, target_time=None):
        call_count["n"] += 1
        return pd.DataFrame()

    with patch("src.dashboard.parallel_fetch.fetch_minute_bars",
               side_effect=_fake_bars), \
         patch("src.dashboard.parallel_fetch.fetch_ccnl_strength", return_value=None), \
         patch("src.dashboard.parallel_fetch.fetch_asking_price", return_value=None), \
         patch("src.dashboard.parallel_fetch.fetch_investor_flow", return_value=None):
        bundles = fetch_bundles_parallel(
            MagicMock(), ["005930", "005930", "005930"], max_workers=4,
        )
    assert set(bundles.keys()) == {"005930"}
    assert call_count["n"] == 1


def test_fetch_bundles_parallel_total_kis_calls_per_tick():
    """종목 N개 → KIS 호출 4N (병렬화 후에도 호출 횟수는 동일, 시간만 단축)."""

    counters = {"bars": 0, "ccnl": 0, "asking": 0, "investor": 0}

    def _bars(client, code, target_time=None):
        counters["bars"] += 1
        return pd.DataFrame()

    def _ccnl(client, code):
        counters["ccnl"] += 1
        return None

    def _ask(client, code):
        counters["asking"] += 1
        return None

    def _inv(client, code):
        counters["investor"] += 1
        return None

    codes = [f"{i:06d}" for i in range(10)]
    with patch("src.dashboard.parallel_fetch.fetch_minute_bars", side_effect=_bars), \
         patch("src.dashboard.parallel_fetch.fetch_ccnl_strength", side_effect=_ccnl), \
         patch("src.dashboard.parallel_fetch.fetch_asking_price", side_effect=_ask), \
         patch("src.dashboard.parallel_fetch.fetch_investor_flow", side_effect=_inv):
        bundles = fetch_bundles_parallel(MagicMock(), codes, max_workers=8)

    assert len(bundles) == 10
    # 직렬 funnel(round 37) 과 동일 호출 횟수 — 4 × N
    assert counters["bars"] == 10
    assert counters["ccnl"] == 10
    assert counters["asking"] == 10
    assert counters["investor"] == 10
