"""src.jongbae.leading_theme 테스트."""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from src.jongbae.leading_theme import (
    codes_in_leading_themes,
    count_themes,
    identify_leading_themes,
)


def _snapshot(codes_with_rank: list[tuple[int, str]]) -> pd.DataFrame:
    """rank, code 만 있는 최소 스냅샷 DF."""
    return pd.DataFrame([
        {
            "rank": r, "code": c, "name": f"종목{c}",
            "price": 1000, "prev_close": 900, "daily_return": 11.0,
            "intraday_high": 1100, "volume": 1, "trading_value": 1, "is_limit_up": False,
        }
        for r, c in codes_with_rank
    ])


def _theme_mapping(rows: list[tuple[str, str]]) -> pd.DataFrame:
    """(code, theme) 튜플 → long format DF."""
    return pd.DataFrame([
        {"code": c, "theme": t, "crawled_at": date(2026, 5, 6)}
        for c, t in rows
    ])


def test_count_themes_basic():
    snap = _snapshot([(1, "001"), (2, "002")])
    mapping = _theme_mapping([
        ("001", "전기/전선"),
        ("001", "원자력"),
        ("002", "전기/전선"),
    ])
    counts = count_themes(snap, mapping, top_n=30)
    assert counts["전기/전선"] == 2
    assert counts["원자력"] == 1


def test_count_themes_respects_top_n():
    snap = _snapshot([(1, "001"), (2, "002"), (3, "003")])
    mapping = _theme_mapping([
        ("001", "T"), ("002", "T"), ("003", "T"),
    ])
    counts = count_themes(snap, mapping, top_n=2)
    assert counts["T"] == 2


def test_count_themes_empty_snapshot():
    counts = count_themes(pd.DataFrame(), pd.DataFrame(), top_n=30)
    assert counts == {}


def test_identify_leading_themes_threshold_3():
    """전기/전선 3종목, 원자력 2종목 → 전기/전선만 주도."""
    snap = _snapshot([(1, "001"), (2, "002"), (3, "003"), (4, "004")])
    mapping = _theme_mapping([
        ("001", "전기/전선"), ("002", "전기/전선"), ("003", "전기/전선"),
        ("001", "원자력"), ("002", "원자력"),
    ])
    leading = identify_leading_themes(snap, mapping, threshold=3)
    assert len(leading) == 1
    assert leading[0]["theme"] == "전기/전선"
    assert leading[0]["count"] == 3
    assert set(leading[0]["codes"]) == {"001", "002", "003"}


def test_identify_leading_themes_no_match():
    snap = _snapshot([(1, "001"), (2, "002")])
    mapping = _theme_mapping([("001", "T1"), ("002", "T2")])
    leading = identify_leading_themes(snap, mapping, threshold=3)
    assert leading == []


def test_identify_leading_themes_empty_mapping():
    snap = _snapshot([(1, "001"), (2, "002"), (3, "003")])
    leading = identify_leading_themes(snap, pd.DataFrame(), threshold=3)
    assert leading == []


def test_identify_leading_themes_codes_ordered_by_rank():
    """codes 는 거래대금 rank 오름차순으로."""
    snap = _snapshot([(1, "A"), (2, "B"), (3, "C")])
    mapping = _theme_mapping([("C", "T"), ("A", "T"), ("B", "T")])
    leading = identify_leading_themes(snap, mapping, threshold=3)
    assert leading[0]["codes"] == ["A", "B", "C"]


def test_identify_leading_themes_multiple_sorted_by_count():
    """주도테마 여러개일 때 count 내림차순."""
    snap = _snapshot([(1, "A"), (2, "B"), (3, "C"), (4, "D")])
    mapping = _theme_mapping([
        ("A", "T1"), ("B", "T1"), ("C", "T1"), ("D", "T1"),
        ("A", "T2"), ("B", "T2"), ("C", "T2"),
    ])
    leading = identify_leading_themes(snap, mapping, threshold=3)
    assert [t["theme"] for t in leading] == ["T1", "T2"]
    assert [t["count"] for t in leading] == [4, 3]


def test_codes_in_leading_themes_dedupe():
    leading = [
        {"theme": "T1", "count": 3, "codes": ["A", "B", "C"]},
        {"theme": "T2", "count": 3, "codes": ["B", "C", "D"]},
    ]
    codes = codes_in_leading_themes(leading)
    assert codes == ["A", "B", "C", "D"]


def test_codes_in_leading_themes_empty():
    assert codes_in_leading_themes([]) == []
