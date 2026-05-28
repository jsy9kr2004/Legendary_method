"""select_leaders_and_candidates — 단저단고 surface 룰 (2026-05-29).

주도섹터별 주도주(거래대금 1위 ∩ 회전율 1위) + 후보(거래대금 2위 ∩ 회전율 2위).
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.common.theme import select_leaders_and_candidates


def _make_snapshot(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if "daily_return" not in df.columns:
        df["daily_return"] = 10.0
    if "is_limit_up" not in df.columns:
        df["is_limit_up"] = False
    if "price" not in df.columns:
        df["price"] = 10000
    if "trading_value" not in df.columns:
        df["trading_value"] = 100000000
    if "market_cap" not in df.columns:
        df["market_cap"] = 1000000000
    if "name" not in df.columns:
        df["name"] = df["code"].apply(lambda c: f"stock_{c}")
    return df


def test_single_leader_single_candidate():
    """거래대금 1위 == 회전율 1위 + 거래대금 2위 == 회전율 2위 → leader 1, candidate 1."""
    snapshot = _make_snapshot([
        {"code": "100001", "rank": 1, "turnover": 50.0},  # 1위 (거래대금) + 1위 (회전율)
        {"code": "100002", "rank": 2, "turnover": 30.0},  # 2위 (거래대금) + 2위 (회전율)
        {"code": "100003", "rank": 3, "turnover": 20.0},
    ])
    sectors = [{"theme": "테스트섹터", "codes": ["100001", "100002", "100003"]}]
    leaders, candidates = select_leaders_and_candidates(snapshot, sectors)

    assert [l["code"] for l in leaders] == ["100001"]
    assert [c["code"] for c in candidates] == ["100002"]
    assert leaders[0]["sector_role"] == "leader"
    assert candidates[0]["sector_role"] == "candidate"
    assert leaders[0]["surface_sector_name"] == "테스트섹터"
    assert candidates[0]["surface_sector_name"] == "테스트섹터"


def test_co_leader_no_candidate():
    """거래대금 1위 ≠ 회전율 1위 → 공동 주도주 2개, 후보 평가 X."""
    snapshot = _make_snapshot([
        {"code": "100001", "rank": 1, "turnover": 20.0},  # 거래대금 1위
        {"code": "100002", "rank": 5, "turnover": 50.0},  # 회전율 1위
        {"code": "100003", "rank": 2, "turnover": 30.0},  # 후보 후보지만 공동 leader 케이스라 평가 X
    ])
    sectors = [{"theme": "공동테마", "codes": ["100001", "100002", "100003"]}]
    leaders, candidates = select_leaders_and_candidates(snapshot, sectors)

    assert {l["code"] for l in leaders} == {"100001", "100002"}
    assert candidates == []
    assert all(l["sector_role"] == "leader" for l in leaders)


def test_single_leader_candidate_mismatch():
    """주도주 단일 케이스, 후보 2위들 불일치 → leader 1, candidate 0."""
    snapshot = _make_snapshot([
        {"code": "100001", "rank": 1, "turnover": 50.0},  # leader (1위 일치)
        {"code": "100002", "rank": 2, "turnover": 10.0},  # 거래대금 2위
        {"code": "100003", "rank": 5, "turnover": 30.0},  # 회전율 2위
    ])
    sectors = [{"theme": "후보불일치", "codes": ["100001", "100002", "100003"]}]
    leaders, candidates = select_leaders_and_candidates(snapshot, sectors)

    assert [l["code"] for l in leaders] == ["100001"]
    assert candidates == []


def test_top_sector_less_than_three_no_padding():
    """주도섹터 1개만 있어도 surface 자리 강제 padding X — 자연스럽게 줄어듦."""
    snapshot = _make_snapshot([
        {"code": "200001", "rank": 1, "turnover": 60.0},
        {"code": "200002", "rank": 2, "turnover": 40.0},
    ])
    sectors = [{"theme": "단일섹터", "codes": ["200001", "200002"]}]
    leaders, candidates = select_leaders_and_candidates(snapshot, sectors)

    assert len(leaders) == 1
    assert len(candidates) == 1
    assert len(leaders) + len(candidates) == 2  # 6 자리 채우지 X


def test_limit_up_excluded():
    """+29% 이상 또는 is_limit_up True 종목은 매수 불가 → 제외."""
    snapshot = _make_snapshot([
        {"code": "300001", "rank": 1, "turnover": 50.0, "daily_return": 29.5},  # 매수 불가
        {"code": "300002", "rank": 2, "turnover": 40.0, "daily_return": 15.0},  # leader 후보
        {"code": "300003", "rank": 3, "turnover": 30.0, "is_limit_up": True},   # 매수 불가
        {"code": "300004", "rank": 4, "turnover": 20.0, "daily_return": 5.0},
    ])
    sectors = [{"theme": "상한가포함", "codes": ["300001", "300002", "300003", "300004"]}]
    leaders, candidates = select_leaders_and_candidates(snapshot, sectors)

    # 300001 (+29.5%) / 300003 (limit_up) 제외 → 300002 가 새 1위, 300004 가 2위
    assert [l["code"] for l in leaders] == ["300002"]
    assert [c["code"] for c in candidates] == ["300004"]


def test_negative_daily_return_allowed():
    """일봉 음봉도 허용 (단저단고는 모멘텀 반대 진입 가능)."""
    snapshot = _make_snapshot([
        {"code": "400001", "rank": 1, "turnover": 50.0, "daily_return": -5.0},
        {"code": "400002", "rank": 2, "turnover": 30.0, "daily_return": -3.0},
    ])
    sectors = [{"theme": "음봉섹터", "codes": ["400001", "400002"]}]
    leaders, candidates = select_leaders_and_candidates(snapshot, sectors)

    assert [l["code"] for l in leaders] == ["400001"]
    assert [c["code"] for c in candidates] == ["400002"]


def test_cross_sector_dedup_first_sector_wins():
    """한 종목이 여러 섹터에 leader 일 때 첫 출현 섹터로 귀속 — 두번째 섹터의 후보는
    그대로 평가됨 (사용자 룰: 섹터간 leader 중복만 제거, 다른 섹터의 후보 룰은 독립)."""
    snapshot = _make_snapshot([
        {"code": "500001", "rank": 1, "turnover": 50.0},  # A/B 둘 다 1위 일치 종목
        {"code": "500002", "rank": 2, "turnover": 30.0},  # A 의 후보 (2위 일치)
        {"code": "500003", "rank": 3, "turnover": 25.0},  # B 의 후보 (B 안에서 2위 일치)
    ])
    sectors = [
        {"theme": "섹터A", "codes": ["500001", "500002"]},
        {"theme": "섹터B", "codes": ["500001", "500003"]},
    ]
    leaders, candidates = select_leaders_and_candidates(snapshot, sectors)

    # 500001 첫 섹터(A)로만 귀속 — leader 단일
    assert [l["code"] for l in leaders] == ["500001"]
    assert leaders[0]["surface_sector_name"] == "섹터A"
    # 각 섹터의 후보 룰은 독립 평가. 섹터A→500002, 섹터B→500003
    candidate_codes = {c["code"] for c in candidates}
    assert candidate_codes == {"500002", "500003"}
    cand_by_code = {c["code"]: c for c in candidates}
    assert cand_by_code["500002"]["surface_sector_name"] == "섹터A"
    assert cand_by_code["500003"]["surface_sector_name"] == "섹터B"


def test_empty_inputs():
    """입력이 비어있으면 빈 결과."""
    assert select_leaders_and_candidates(pd.DataFrame(), []) == ([], [])
    snap = _make_snapshot([{"code": "100001", "rank": 1, "turnover": 50.0}])
    assert select_leaders_and_candidates(snap, []) == ([], [])
    assert select_leaders_and_candidates(pd.DataFrame(), [{"theme": "X", "codes": ["100001"]}]) == ([], [])
