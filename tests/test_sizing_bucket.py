"""sizing_bucket (거래대금순위 버킷 Kelly) 단위 테스트."""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from src.overnight.sizing_bucket import (
    build_bucket_stats,
    compute_bucket_sizing,
    rank_bucket,
)


def _synthetic_ohlcv(n_days: int = 40, n_codes: int = 12) -> pd.DataFrame:
    """합성 일봉.

    - code_00..09 = 거래대금 rank 1~10 (버킷 1~10위), 진입 다음날 갭 +2% (p=1).
    - code_10,11 = rank 11,12 (버킷 11~25위), 갭 -2% (p=0).
    - 매일 ret +10% (후보풀 5~27% 안), drop=0 (종가=고가).
    """
    dates = [dt.date(2026, 1, 5) + dt.timedelta(days=i) for i in range(n_days)]
    rows = []
    for ci in range(n_codes):
        code = f"{ci:06d}"
        gap = 0.02 if ci < 10 else -0.02
        tv = (n_codes - ci) * 1e10  # ci=0 → 최대 거래대금 → rank1
        prev_close = None
        for d in dates:
            if prev_close is None:
                open_, close = 1000.0, 1100.0
            else:
                open_ = prev_close * (1 + gap)   # 전일 종가 대비 갭
                close = prev_close * 1.10         # ret +10%
            high = max(open_, close)
            low = min(open_, close) * 0.97
            rows.append({"code": code, "date": d, "open": open_, "high": high,
                         "low": low, "close": close, "trading_value": tv})
            prev_close = close
    return pd.DataFrame(rows)


def test_rank_bucket_boundaries():
    assert rank_bucket(1) == "1~10위"
    assert rank_bucket(10) == "1~10위"
    assert rank_bucket(11) == "11~25위"
    assert rank_bucket(25) == "11~25위"
    assert rank_bucket(26) == "26~50위"
    assert rank_bucket(50) == "26~50위"
    assert rank_bucket(51) is None
    assert rank_bucket(None) is None
    assert rank_bucket(float("nan")) is None


def test_bucket_stats_separates_good_from_bad():
    df = _synthetic_ohlcv()
    trad = {f"{i:06d}" for i in range(12)}
    stats = build_bucket_stats(df, as_of=dt.date(2026, 3, 1), tradable_codes=trad)

    assert "1~10위" in stats and "11~25위" in stats
    top = stats["1~10위"]
    assert top["p"] == pytest.approx(1.0)            # 전부 갭상
    assert top["avg_gap_when_up"] == pytest.approx(2.0, abs=0.1)
    assert top["n"] >= 20                              # ×0.8 sample factor 영역

    bot = stats["11~25위"]
    assert bot["p"] == pytest.approx(0.0)             # 전부 갭하


def test_compute_bucket_sizing_loads_top_more_than_bottom():
    df = _synthetic_ohlcv()
    trad = {f"{i:06d}" for i in range(12)}
    stats = build_bucket_stats(df, as_of=dt.date(2026, 3, 1), tradable_codes=trad)

    cands = [{"code": "000000", "rank": 1},
             {"code": "000003", "rank": 3},
             {"code": "000011", "rank": 11}]
    out = compute_bucket_sizing(cands, stats)

    a = out["kelly_abs"]
    assert a[0] is not None and a[1] is not None
    assert a[0] > 0 and a[1] > 0                       # 1~10위 = 베팅
    assert (a[2] in (0.0, None))                       # 11~25위 p=0 → 0
    # 절대 비중 = 계좌 대비, 현금 = 나머지
    assert out["cash"] == pytest.approx(1.0 - out["invested"])
    assert 0 < out["invested"] <= 1.0
    # 상대(강약) 합 = 1
    assert sum(out["kelly_rel"]) == pytest.approx(1.0)
    # 같은 버킷 두 종목은 동률 (엣지 같음)
    assert out["kelly_rel"][0] == pytest.approx(out["kelly_rel"][1])


def test_no_lookahead_empty_before_data():
    df = _synthetic_ohlcv()
    trad = {f"{i:06d}" for i in range(12)}
    # as_of 가 데이터 시작 이전 → 학습 표본 없음
    stats = build_bucket_stats(df, as_of=dt.date(2025, 1, 1), tradable_codes=trad)
    assert stats == {}


def test_as_of_excludes_future_dates():
    df = _synthetic_ohlcv()
    trad = {f"{i:06d}" for i in range(12)}
    early = build_bucket_stats(df, as_of=dt.date(2026, 1, 15), tradable_codes=trad)
    late = build_bucket_stats(df, as_of=dt.date(2026, 2, 20), tradable_codes=trad)
    # 늦은 as_of 가 더 많은 표본 (lookahead 차단돼도 누적은 증가)
    assert late["1~10위"]["n"] > early["1~10위"]["n"]


def test_empty_inputs():
    assert build_bucket_stats(pd.DataFrame(), dt.date(2026, 1, 1), set()) == {}
    out = compute_bucket_sizing([], {})
    assert out["kelly_abs"] == [] and out["cash"] == 1.0
