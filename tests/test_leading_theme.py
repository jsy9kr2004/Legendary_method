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


# ── identify_early_morning_leaders (M5.5: 회전율 1위 정의) ────────────────────

from src.jongbae.leading_theme import identify_early_morning_leaders


def _snap_em(
    rows: list[tuple[int, str, float, bool, float]],
) -> pd.DataFrame:
    """rank, code, daily_return, is_limit_up, turnover(%)."""
    return pd.DataFrame([
        {"rank": r, "code": c, "name": f"종목{c}",
         "price": 1000, "prev_close": 800, "daily_return": ret,
         "intraday_high": 1100, "intraday_low": 900,
         "volume": 1000, "trading_value": int(to * 1e10), "is_limit_up": lup,
         "market_cap": 1000, "turnover": to}
        for r, c, ret, lup, to in rows
    ])


def test_em_leaders_picks_top_turnover():
    """주도섹터 내 회전율 1위가 주도주."""
    snap = _snap_em([
        (1, "A", 5.0,  False, 1.0),    # 거래대금 1위지만 회전율 낮음 (대형주 패턴)
        (2, "B", 10.0, False, 15.0),   # 회전율 최고 → 주도주
        (3, "C", 8.0,  False, 8.0),
    ])
    leading = [{"theme": "T", "count": 3, "codes": ["A", "B", "C"]}]
    leaders = identify_early_morning_leaders(snap, leading, top_per_theme=1)
    assert [l["code"] for l in leaders] == ["B"]


def test_em_leaders_top_per_theme_2():
    """top_per_theme=2 면 회전율 상위 2개."""
    snap = _snap_em([
        (1, "A", 5.0,  False, 1.0),
        (2, "B", 10.0, False, 15.0),
        (3, "C", 8.0,  False, 8.0),
        (4, "D", 12.0, False, 22.0),
    ])
    leading = [{"theme": "T", "count": 4, "codes": ["A", "B", "C", "D"]}]
    leaders = identify_early_morning_leaders(snap, leading, top_per_theme=2)
    assert sorted([l["code"] for l in leaders]) == ["B", "D"]


def test_em_leaders_excludes_megacap_by_turnover():
    """대형주(거래대금 1위 + 시총 거대) 시뮬: 회전율 작아서 자연 누락."""
    snap = _snap_em([
        (1, "HYNIX",    2.0,  False, 0.4),
        (2, "SAMSUNG",  1.5,  False, 0.3),
        (3, "JEPRYUNG", 30.0, True,  18.0),
    ])
    leading = [{"theme": "T", "count": 3, "codes": ["HYNIX", "SAMSUNG", "JEPRYUNG"]}]
    leaders = identify_early_morning_leaders(snap, leading, top_per_theme=1)
    assert [l["code"] for l in leaders] == ["JEPRYUNG"]


def test_em_leaders_multi_theme_stock_dedup():
    """한 종목이 여러 주도섹터에 속하면 themes 리스트 합쳐서 한 번만."""
    snap = _snap_em([
        (1, "A", 25.0, False, 12.0),
        (2, "B", 20.0, False, 8.0),
    ])
    leading = [
        {"theme": "T1", "count": 3, "codes": ["A", "B"]},
        {"theme": "T2", "count": 3, "codes": ["A"]},
    ]
    leaders = identify_early_morning_leaders(snap, leading, top_per_theme=1)
    a_entries = [l for l in leaders if l["code"] == "A"]
    assert len(a_entries) == 1
    assert sorted(a_entries[0]["themes"]) == ["T1", "T2"]


def test_em_leaders_includes_limit_up_flag():
    snap = _snap_em([
        (1, "A", 30.0, True,  18.0),
        (2, "B", 25.0, False, 8.0),
    ])
    leading = [{"theme": "T", "count": 3, "codes": ["A", "B"]}]
    leaders = identify_early_morning_leaders(snap, leading, top_per_theme=1)
    a = next(l for l in leaders if l["code"] == "A")
    assert a["is_limit_up"] is True
    assert a["turnover"] == 18.0


def test_em_leaders_fallback_to_trading_value_when_no_turnover():
    """turnover 컬럼 없으면 거래대금 절대값으로 fallback."""
    snap = pd.DataFrame([
        {"rank": 1, "code": "A", "name": "A",
         "price": 1000, "prev_close": 800, "daily_return": 5.0,
         "intraday_high": 1100, "intraday_low": 900,
         "volume": 1000, "trading_value": 1000, "is_limit_up": False},
        {"rank": 2, "code": "B", "name": "B",
         "price": 1000, "prev_close": 800, "daily_return": 25.0,
         "intraday_high": 1100, "intraday_low": 900,
         "volume": 1000, "trading_value": 9999, "is_limit_up": False},
    ])
    leading = [{"theme": "T", "count": 2, "codes": ["A", "B"]}]
    leaders = identify_early_morning_leaders(snap, leading, top_per_theme=1)
    assert [l["code"] for l in leaders] == ["B"]


def test_em_leaders_sorted_by_turnover_desc():
    snap = _snap_em([
        (1, "A", 5.0, False, 5.0),
        (2, "B", 8.0, False, 18.0),
        (3, "C", 6.0, False, 11.0),
    ])
    leading = [{"theme": "T", "count": 3, "codes": ["A", "B", "C"]}]
    leaders = identify_early_morning_leaders(snap, leading, top_per_theme=3)
    assert [l["code"] for l in leaders] == ["B", "C", "A"]


def test_em_leaders_empty_inputs():
    empty_snap = pd.DataFrame(columns=[
        "rank", "code", "name", "price", "prev_close", "daily_return",
        "intraday_high", "intraday_low", "volume", "trading_value",
        "is_limit_up", "market_cap", "turnover",
    ])
    assert identify_early_morning_leaders(empty_snap, [{"theme": "T", "count": 3, "codes": ["A"]}]) == []
    snap = _snap_em([(1, "A", 10.0, False, 5.0)])
    assert identify_early_morning_leaders(snap, []) == []


# ── score_leading_sectors (M5.5 v1) ───────────────────────────────────────────

from src.jongbae.leading_theme import score_leading_sectors


def _full_snap(
    rows: list[tuple[int, str, float, float, int]],
) -> pd.DataFrame:
    """rank, code, daily_return, turnover, market_cap."""
    return pd.DataFrame([
        {"rank": r, "code": c, "name": f"종목{c}",
         "price": 1000, "prev_close": 800, "daily_return": ret,
         "intraday_high": 1100, "intraday_low": 900,
         "volume": 1000, "trading_value": int(to * mc * 1e8 / 100),
         "is_limit_up": False, "market_cap": mc, "turnover": to}
        for r, c, ret, to, mc in rows
    ])


def _theme_map(rows: list[tuple[str, str]]) -> pd.DataFrame:
    return pd.DataFrame([{"code": c, "theme": t} for c, t in rows])


def test_score_sectors_picks_higher_breadth_and_return():
    """T1: breadth 큼 + 평균상승률 큼 + 회전율 큼 → 1위."""
    snap = _full_snap([
        (1, "A", 20.0, 15.0, 1000),
        (2, "B", 18.0, 12.0, 1000),
        (3, "C", 22.0, 18.0, 1000),
        (4, "D",  3.0,  1.0, 1000),
        (5, "E",  2.0,  0.8, 1000),
        (6, "F",  4.0,  1.5, 1000),
    ])
    mapping = _theme_map([
        ("A", "T1"), ("B", "T1"), ("C", "T1"),
        ("D", "T2"), ("E", "T2"), ("F", "T2"),
    ])
    leading = score_leading_sectors(snap, mapping, top_n=10, sector_count=2)
    assert [l["theme"] for l in leading] == ["T1", "T2"]
    assert leading[0]["score"] > leading[1]["score"]


def test_score_sectors_min_member_count():
    """구성종목 < 3 인 테마는 후보 X."""
    snap = _full_snap([
        (1, "A", 20.0, 15.0, 1000),
        (2, "B", 18.0, 12.0, 1000),
        (3, "C", 22.0, 18.0, 1000),
        (4, "D", 25.0, 30.0, 1000),  # T2 단일 종목
    ])
    mapping = _theme_map([
        ("A", "T1"), ("B", "T1"), ("C", "T1"),
        ("D", "T2"),
    ])
    leading = score_leading_sectors(snap, mapping, top_n=10, sector_count=5)
    assert [l["theme"] for l in leading] == ["T1"]


def test_score_sectors_breadth_counts_above_threshold():
    """breadth = 테마 내 daily_return >= 5% 종목 수."""
    snap = _full_snap([
        (1, "A", 10.0, 5.0, 1000),
        (2, "B",  3.0, 4.0, 1000),  # 5% 미만
        (3, "C",  8.0, 6.0, 1000),
        (4, "D",  2.0, 3.0, 1000),  # 5% 미만
    ])
    mapping = _theme_map([
        ("A", "T"), ("B", "T"), ("C", "T"), ("D", "T"),
    ])
    leading = score_leading_sectors(snap, mapping, top_n=10, sector_count=1)
    assert leading[0]["breadth"] == 2  # A, C 만
    assert leading[0]["member_count"] == 4


def test_score_sectors_codes_ordered_by_rank():
    snap = _full_snap([
        (5, "C", 8.0, 6.0, 1000),
        (1, "A", 10.0, 5.0, 1000),
        (3, "B", 9.0, 7.0, 1000),
    ])
    mapping = _theme_map([("A", "T"), ("B", "T"), ("C", "T")])
    leading = score_leading_sectors(snap, mapping, top_n=10, sector_count=1)
    assert leading[0]["codes"] == ["A", "B", "C"]


def test_score_sectors_empty_inputs():
    empty_snap = pd.DataFrame(columns=[
        "rank", "code", "name", "price", "prev_close", "daily_return",
        "intraday_high", "intraday_low", "volume", "trading_value",
        "is_limit_up", "market_cap", "turnover",
    ])
    assert score_leading_sectors(empty_snap, _theme_map([("A", "T")])) == []
    snap = _full_snap([(1, "A", 10.0, 5.0, 1000)])
    assert score_leading_sectors(snap, pd.DataFrame()) == []


def test_score_sectors_top_n_caps_universe():
    """top_n 이 작으면 그 안의 종목만 집계 — 테마 멤버수 줄어들 수 있음."""
    snap = _full_snap([
        (1, "A", 20.0, 15.0, 1000),
        (2, "B", 18.0, 12.0, 1000),
        (3, "C", 22.0, 18.0, 1000),
        (4, "D", 19.0, 14.0, 1000),  # top_n=3 일 땐 미포함
    ])
    mapping = _theme_map([
        ("A", "T"), ("B", "T"), ("C", "T"), ("D", "T"),
    ])
    leading = score_leading_sectors(snap, mapping, top_n=3, sector_count=1)
    assert leading[0]["member_count"] == 3
    assert "D" not in leading[0]["codes"]
