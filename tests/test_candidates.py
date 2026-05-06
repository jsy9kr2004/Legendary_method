"""src.jongbae.candidates 테스트."""
from __future__ import annotations

import pandas as pd
import pytest

from src.jongbae.candidates import (
    PRIORITY_EXCLUDED,
    PRIORITY_HIGH_PULL,
    PRIORITY_LIMIT_UP,
    PRIORITY_NORMAL,
    accepted_candidates,
    classify_priority,
    extract_candidates,
)


def _row(daily_return, intraday_high_pct=0.0, is_limit_up=False) -> pd.Series:
    return pd.Series({
        "daily_return": daily_return,
        "intraday_high_pct": intraday_high_pct,
        "is_limit_up": is_limit_up,
    })


# ── classify_priority ────────────────────────────────────────────────────────

def test_classify_limit_up():
    p, _ = classify_priority(_row(30.0, 30.0, True))
    assert p == PRIORITY_LIMIT_UP


def test_classify_high_pull():
    """일중 +28%↑ 후 종가 +20~25% 영역."""
    p, _ = classify_priority(_row(daily_return=22.0, intraday_high_pct=28.5))
    assert p == PRIORITY_HIGH_PULL


def test_classify_normal_plus_20():
    """주도테마 + 일봉 +20% 이상이지만 일중 +28% 못 넘었으면 일반."""
    p, _ = classify_priority(_row(daily_return=21.0, intraday_high_pct=24.0))
    assert p == PRIORITY_NORMAL


def test_classify_excluded_dead_pull():
    """일중 +30% 찍고 +5% 떡락."""
    p, reason = classify_priority(_row(daily_return=4.0, intraday_high_pct=30.0))
    assert p == PRIORITY_EXCLUDED
    assert "떡락" in reason


def test_classify_excluded_stuck_at_28():
    """일중 +28% 찍고 +28% 그대로 마감 (상한가 못 감)."""
    p, reason = classify_priority(_row(daily_return=28.5, intraday_high_pct=28.5))
    assert p == PRIORITY_EXCLUDED
    assert "상한가 미도달" in reason


def test_classify_excluded_below_20():
    """+20% 미만은 후보 아님."""
    p, reason = classify_priority(_row(daily_return=15.0, intraday_high_pct=18.0))
    assert p == PRIORITY_EXCLUDED


# ── extract_candidates ───────────────────────────────────────────────────────

def _snapshot_full(rows: list[dict]) -> pd.DataFrame:
    base = {
        "rank": 1, "code": "001", "name": "X",
        "price": 1300, "prev_close": 1000,
        "daily_return": 30.0, "intraday_high": 1300,
        "volume": 1, "trading_value": 1, "is_limit_up": False,
    }
    return pd.DataFrame([{**base, **r} for r in rows])


def test_extract_candidates_filters_to_leading_theme():
    snap = _snapshot_full([
        {"rank": 1, "code": "001", "daily_return": 30.0, "is_limit_up": True,
         "prev_close": 1000, "intraday_high": 1300},
        {"rank": 2, "code": "002", "daily_return": 25.0,
         "prev_close": 1000, "intraday_high": 1290},
    ])
    out = extract_candidates(snap, leading_theme_codes=["001"])
    assert len(out) == 1
    assert out.iloc[0]["code"] == "001"


def test_extract_candidates_priority_order():
    """반환 순서: limit_up → high_pull → normal → excluded"""
    snap = _snapshot_full([
        # rank 1: 일반 +21%
        {"rank": 1, "code": "A", "daily_return": 21.0, "is_limit_up": False,
         "prev_close": 1000, "intraday_high": 1240},
        # rank 2: 상한가
        {"rank": 2, "code": "B", "daily_return": 30.0, "is_limit_up": True,
         "prev_close": 1000, "intraday_high": 1300},
        # rank 3: high_pull
        {"rank": 3, "code": "C", "daily_return": 22.0, "is_limit_up": False,
         "prev_close": 1000, "intraday_high": 1290},
    ])
    out = extract_candidates(snap, leading_theme_codes=["A", "B", "C"])
    priorities = out["priority"].tolist()
    assert priorities[0] == PRIORITY_LIMIT_UP
    assert priorities[1] == PRIORITY_HIGH_PULL
    assert priorities[2] == PRIORITY_NORMAL


def test_extract_candidates_empty_snapshot():
    out = extract_candidates(pd.DataFrame(), leading_theme_codes=["A"])
    assert out.empty


def test_extract_candidates_empty_leading():
    snap = _snapshot_full([{"code": "A"}])
    out = extract_candidates(snap, leading_theme_codes=[])
    assert out.empty


def test_accepted_candidates_drops_excluded():
    df = pd.DataFrame([
        {"code": "A", "priority": PRIORITY_LIMIT_UP},
        {"code": "B", "priority": PRIORITY_EXCLUDED},
        {"code": "C", "priority": PRIORITY_NORMAL},
    ])
    accepted = accepted_candidates(df)
    assert len(accepted) == 2
    assert set(accepted["code"]) == {"A", "C"}
