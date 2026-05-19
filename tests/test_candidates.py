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


# ── classify_priority (R4 v2 (e) — round 41) ────────────────────────────────

def test_classify_excluded_limit_up_above_27():
    """R4 v2 (e) 상한가(+30%≈)는 27% 초과로 제외. 사용자 보고 회귀 케이스 —
    진원생명과학 011000 +29.97% (2026-05-19)."""
    p, reason = classify_priority(_row(daily_return=29.97, intraday_high_pct=29.97, is_limit_up=True))
    assert p == PRIORITY_EXCLUDED
    assert "27" in reason  # 상한 컷 사유 명시


def test_classify_high_pull():
    """일중 +28%↑ 후 종가 +20~25% 영역 (R4 v2 eligible 내 1순위)."""
    p, _ = classify_priority(_row(daily_return=22.0, intraday_high_pct=28.5))
    assert p == PRIORITY_HIGH_PULL


def test_classify_normal_within_range():
    """R4 v2 eligible 10~27% 의 high_pull 외 (예: 일중 +28% 못 넘은 +21%)."""
    p, _ = classify_priority(_row(daily_return=21.0, intraday_high_pct=24.0))
    assert p == PRIORITY_NORMAL


def test_classify_normal_between_10_and_20():
    """R4 v2 (e) 하한 10% 적용 후 — 이전엔 20% 하한에 제외됐던 +15% 도 NORMAL."""
    p, _ = classify_priority(_row(daily_return=15.0, intraday_high_pct=18.0))
    assert p == PRIORITY_NORMAL


def test_classify_excluded_below_10():
    """+10% 미만은 R4 v2 (e) 하한 컷."""
    p, reason = classify_priority(_row(daily_return=4.0, intraday_high_pct=30.0))
    assert p == PRIORITY_EXCLUDED
    assert "10" in reason


def test_classify_excluded_above_27_stuck_at_28():
    """+28% 자리잡힘 케이스도 27% 상한 컷에 자동 제외."""
    p, reason = classify_priority(_row(daily_return=28.5, intraday_high_pct=28.5))
    assert p == PRIORITY_EXCLUDED
    assert "27" in reason


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
        # R4 v2: +30% 상한가는 제외, +25% 만 후보로
        {"rank": 1, "code": "001", "daily_return": 25.0, "is_limit_up": False,
         "prev_close": 1000, "intraday_high": 1290},
        {"rank": 2, "code": "002", "daily_return": 22.0,
         "prev_close": 1000, "intraday_high": 1280},
    ])
    out = extract_candidates(snap, leading_theme_codes=["001"])
    assert len(out) == 1
    assert out.iloc[0]["code"] == "001"


def test_extract_candidates_priority_order():
    """R4 v2 반환 순서: high_pull → normal → excluded
    (limit_up 은 +30%≈로 R4 v2 (e) 상한 컷에 자동 제외 — round 41)."""
    snap = _snapshot_full([
        # rank 1: 일반 +21%
        {"rank": 1, "code": "A", "daily_return": 21.0, "is_limit_up": False,
         "prev_close": 1000, "intraday_high": 1240},
        # rank 2: 상한가 (R4 v2 에서 제외됨)
        {"rank": 2, "code": "B", "daily_return": 30.0, "is_limit_up": True,
         "prev_close": 1000, "intraday_high": 1300},
        # rank 3: high_pull
        {"rank": 3, "code": "C", "daily_return": 22.0, "is_limit_up": False,
         "prev_close": 1000, "intraday_high": 1290},
    ])
    out = extract_candidates(snap, leading_theme_codes=["A", "B", "C"])
    priorities = out["priority"].tolist()
    # 정렬 순서: high_pull → normal → excluded
    assert priorities[0] == PRIORITY_HIGH_PULL  # C
    assert priorities[1] == PRIORITY_NORMAL     # A
    assert priorities[2] == PRIORITY_EXCLUDED   # B (상한가 → 제외)
    assert out.iloc[0]["code"] == "C"
    assert out.iloc[1]["code"] == "A"
    assert out.iloc[2]["code"] == "B"


def test_extract_candidates_excludes_user_reported_regression():
    """사용자 보고 회귀 — 진원생명과학 011000 +29.97% (2026-05-19).
    이전 R4 v1 에서는 limit_up 으로 1순위 진입. R4 v2 (e) 적용 후 제외 확정."""
    snap = _snapshot_full([
        {"rank": 11, "code": "011000", "name": "진원생명과학",
         "daily_return": 29.97, "is_limit_up": True,
         "prev_close": 1051, "intraday_high": 1366, "price": 1366},
    ])
    out = extract_candidates(snap, leading_theme_codes=["011000"])
    assert len(out) == 1
    assert out.iloc[0]["priority"] == PRIORITY_EXCLUDED
    assert "27" in out.iloc[0]["exclusion_reason"]
    # accepted_candidates 로 거르면 빈 DF
    assert accepted_candidates(out).empty


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
