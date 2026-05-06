"""src.jongbae.leading_theme 테스트."""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from src.jongbae.leading_theme import (
    codes_in_leading_themes,
    count_themes,
    identify_leading_stocks,
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


# ── identify_leading_stocks ──────────────────────────────────────────────────

def _snapshot_with_lup(rows: list[tuple[int, str, bool]]) -> pd.DataFrame:
    """rank, code, is_limit_up 만 채운 미니 스냅샷."""
    return pd.DataFrame([
        {
            "rank": r, "code": c, "name": f"종목{c}",
            "price": 1000, "prev_close": 770, "daily_return": 30.0,
            "intraday_high": 1001, "intraday_low": 990,
            "volume": 1, "trading_value": 1, "is_limit_up": lup,
        }
        for r, c, lup in rows
    ])


def test_identify_leading_stocks_first_mover_per_theme():
    """주도테마 내 rank 가 가장 좋은 상한가 종목 = 주도주."""
    snap = _snapshot_with_lup([
        (1, "A", False),
        (2, "B", True),    # 전기/전선 first-mover
        (3, "C", True),    # 전기/전선 추가 상한가
        (5, "D", True),    # 반도체 first-mover
    ])
    leading = [
        {"theme": "전기/전선", "count": 3, "codes": ["A", "B", "C"]},
        {"theme": "반도체",   "count": 3, "codes": ["D", "E", "F"]},
    ]
    leaders = identify_leading_stocks(snap, leading)
    codes = [l["code"] for l in leaders]
    assert codes == ["B", "D"]
    assert leaders[0]["theme"] == "전기/전선"


def test_identify_leading_stocks_no_limit_up():
    snap = _snapshot_with_lup([(1, "A", False), (2, "B", False)])
    leading = [{"theme": "X", "count": 3, "codes": ["A", "B"]}]
    assert identify_leading_stocks(snap, leading) == []


def test_identify_leading_stocks_dedup_across_themes():
    """한 종목이 여러 주도테마에 속해도 한 번만 등장."""
    snap = _snapshot_with_lup([(1, "A", True)])
    leading = [
        {"theme": "T1", "count": 3, "codes": ["A"]},
        {"theme": "T2", "count": 3, "codes": ["A"]},
    ]
    leaders = identify_leading_stocks(snap, leading)
    assert len(leaders) == 1
    assert leaders[0]["theme"] == "T1"  # 먼저 매칭된 테마


def test_identify_leading_stocks_empty_themes():
    snap = _snapshot_with_lup([(1, "A", True)])
    assert identify_leading_stocks(snap, []) == []


# ── identify_early_morning_leaders ───────────────────────────────────────────

from src.jongbae.leading_theme import identify_early_morning_leaders


def _snap(rows: list[tuple[int, str, float, bool]]) -> pd.DataFrame:
    """rank, code, daily_return, is_limit_up."""
    return pd.DataFrame([
        {"rank": r, "code": c, "name": f"종목{c}",
         "price": 1000, "prev_close": 800, "daily_return": ret,
         "intraday_high": 1100, "intraday_low": 900,
         "volume": 1, "trading_value": 1, "is_limit_up": lup}
        for r, c, ret, lup in rows
    ])


def test_em_leaders_volume_and_return_separately():
    """거래대금 1위와 상승률 1위가 다르면 둘 다 주도주."""
    snap = _snap([
        (1, "A", 5.0,  False),   # 거래대금 1위, 상승률 낮음
        (2, "B", 25.0, False),   # 거래대금 2위, 상승률 1위
        (3, "C", 10.0, False),
        (4, "D", 15.0, False),
    ])
    leading = [{"theme": "T", "count": 3, "codes": ["A", "B", "C", "D"]}]
    leaders = identify_early_morning_leaders(snap, leading, top_per_theme=1)
    codes = sorted([l["code"] for l in leaders])
    assert codes == ["A", "B"]
    a = next(l for l in leaders if l["code"] == "A")
    b = next(l for l in leaders if l["code"] == "B")
    assert a["criterion"] == "volume"
    assert b["criterion"] == "return"


def test_em_leaders_both_criteria():
    """거래대금 + 상승률 1위가 동일 종목이면 criterion='both'."""
    snap = _snap([
        (1, "A", 28.0, False),   # 둘 다 1위
        (2, "B", 5.0,  False),
    ])
    leading = [{"theme": "T", "count": 3, "codes": ["A", "B"]}]
    leaders = identify_early_morning_leaders(snap, leading, top_per_theme=1)
    a = next(l for l in leaders if l["code"] == "A")
    assert a["criterion"] == "both"


def test_em_leaders_multi_theme_stock_dedup():
    """한 종목이 여러 주도섹터에 속하면 themes 리스트에 합쳐서 한 번만."""
    snap = _snap([(1, "A", 25.0, False), (2, "B", 20.0, False), (3, "C", 15.0, False)])
    leading = [
        {"theme": "T1", "count": 3, "codes": ["A", "B", "C"]},
        {"theme": "T2", "count": 3, "codes": ["A", "B"]},
    ]
    leaders = identify_early_morning_leaders(snap, leading, top_per_theme=1)
    a_entries = [l for l in leaders if l["code"] == "A"]
    assert len(a_entries) == 1
    assert sorted(a_entries[0]["themes"]) == ["T1", "T2"]


def test_em_leaders_criterion_promotion_across_themes():
    """T1 에선 volume 만, T2 에선 return 만 → 통합 criterion='both'.

    설계:
        X: rank 5, return 10%
        Y: rank 8, return 30%   (T1 에서 X 보다 rank 크고 return 높음)
        Z: rank 3, return 5%    (T2 에서 X 보다 rank 작고 return 낮음)
        T1 = [X, Y]: X 는 volume top, Y 는 return top → X criterion='volume'
        T2 = [X, Z]: Z 는 volume top, X 는 return top → X criterion='return'
        → X 가 두 테마 통합 시 'both' 로 격상되어야 함
    """
    snap = _snap([
        (3, "Z", 5.0,  False),
        (5, "X", 10.0, False),
        (8, "Y", 30.0, False),
    ])
    leading = [
        {"theme": "T1", "count": 3, "codes": ["X", "Y"]},
        {"theme": "T2", "count": 3, "codes": ["X", "Z"]},
    ]
    leaders = identify_early_morning_leaders(snap, leading, top_per_theme=1)
    x = next(l for l in leaders if l["code"] == "X")
    assert x["criterion"] == "both"
    assert sorted(x["themes"]) == ["T1", "T2"]


def test_em_leaders_includes_limit_up_flag():
    snap = _snap([(1, "A", 30.0, True), (2, "B", 25.0, False)])
    leading = [{"theme": "T", "count": 3, "codes": ["A", "B"]}]
    leaders = identify_early_morning_leaders(snap, leading, top_per_theme=1)
    a = next(l for l in leaders if l["code"] == "A")
    assert a["is_limit_up"] is True


def test_em_leaders_empty_inputs():
    assert identify_early_morning_leaders(_snap([]), [{"theme": "T", "count": 3, "codes": ["A"]}]) == []
    assert identify_early_morning_leaders(_snap([(1, "A", 10.0, False)]), []) == []
