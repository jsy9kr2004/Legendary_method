"""candidate_trends + 시총 역산 테스트 (2026-05-24 결정 레포트 3일 추이)."""
from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import pytest

from src.data.intraday import compute_turnover, infer_market_cap_eok
from src.data.snapshot import save_snapshot
from src.overnight.candidate_trends import (
    RANK_IN,
    RANK_NA,
    RANK_OUT,
    attach_candidate_trends,
)


# ── 시총 역산 (infer_market_cap_eok) ──────────────────────────────────────────

def test_infer_market_cap_roundtrip():
    """거래대금 + 회전율 → 시총 역산이 compute_turnover 와 자기일관."""
    tv = 10_000_000_000  # 100억
    mc = infer_market_cap_eok(tv, 10.0)  # 10% 회전율
    assert mc == 1000  # 100억 / 0.1 = 1000억
    # 역산 시총으로 다시 회전율 계산하면 원래 값 복원
    assert compute_turnover(tv, mc) == pytest.approx(10.0, abs=0.05)


def test_infer_market_cap_real_case():
    """062970 실제 케이스: 721억 / 39.72% ≈ 1815억."""
    assert infer_market_cap_eok(72_104_736_787, 39.72) == 1815


def test_infer_market_cap_guards():
    assert infer_market_cap_eok(0, 10.0) == 0
    assert infer_market_cap_eok(100, 0.0) == 0
    assert infer_market_cap_eok(100, float("nan")) == 0
    assert infer_market_cap_eok(100, -5.0) == 0


# ── 3일 추이 (attach_candidate_trends) ────────────────────────────────────────

def _daily(code: str = "062970") -> pd.DataFrame:
    """05-20=200억, 05-21=50억 일봉 (시총 1000억 → 회전율 20%, 5%)."""
    rows = [
        {"code": code, "date": date(2026, 5, 20), "open": 1, "high": 1, "low": 1,
         "close": 1, "volume": 1, "trading_value": 20_000_000_000},
        {"code": code, "date": date(2026, 5, 21), "open": 1, "high": 1, "low": 1,
         "close": 1, "volume": 1, "trading_value": 5_000_000_000},
    ]
    return pd.DataFrame(rows)


def _snapshot_df(code: str, rank: int, turnover_rank: int) -> pd.DataFrame:
    return pd.DataFrame([{
        "rank": rank, "turnover_rank": turnover_rank, "volume_rank": rank,
        "code": code, "name": "x", "price": 1, "prev_close": 1,
        "daily_return": 1.0, "intraday_high": 1, "intraday_low": 1,
        "volume": 1, "trading_value": 1, "is_limit_up": False,
        "market_cap": 1000, "turnover": 5.0,
    }])


def _candidate() -> dict:
    return {
        "code": "062970", "name": "한국첨단소재",
        "trading_value": 10_000_000_000,  # 오늘 100억
        "turnover": 10.0, "market_cap": 1000,
        "rank": 43, "turnover_rank": 5,
    }


def test_trends_value_and_rank_states(tmp_path):
    """tv/turnover 값 추이 + 순위 상태 IN/OUT/NA 정확."""
    daily = _daily()
    # 05-20 스냅샷: 062970 없음 → RANK_OUT. 05-21 스냅샷: 062970 rank 30 → RANK_IN.
    save_snapshot(_snapshot_df("999999", 1, 1), tmp_path, datetime(2026, 5, 20, 14, 50))
    save_snapshot(_snapshot_df("062970", 30, 8), tmp_path, datetime(2026, 5, 21, 14, 50))

    c = _candidate()
    attach_candidate_trends([c], daily, tmp_path, date(2026, 5, 22))
    tr = c["trends"]

    tv = tr["trading_value"]
    assert [cell["value"] for cell in tv] == [20_000_000_000, 5_000_000_000, 10_000_000_000]
    assert [cell["rank_state"] for cell in tv] == [RANK_OUT, RANK_IN, RANK_IN]
    assert tv[1]["rank"] == 30   # 05-21 스냅샷에서 발견
    assert tv[2]["rank"] == 43   # 오늘 = 후보 rank

    to = tr["turnover"]
    # 200억/1000억=20%, 50억/1000억=5%, 오늘 10%
    assert [round(cell["value"], 1) for cell in to] == [20.0, 5.0, 10.0]
    assert to[1]["rank"] == 8    # turnover_rank 05-21
    assert to[2]["rank"] == 5    # 오늘 turnover_rank


def test_trends_rank_na_when_no_snapshot(tmp_path):
    """스냅샷 파일 자체가 없으면 RANK_NA."""
    c = _candidate()
    attach_candidate_trends([c], _daily(), tmp_path, date(2026, 5, 22))
    tv = c["trends"]["trading_value"]
    # 과거 2일 스냅샷 없음 → NA, 오늘만 IN
    assert tv[0]["rank_state"] == RANK_NA
    assert tv[1]["rank_state"] == RANK_NA
    assert tv[2]["rank_state"] == RANK_IN


def test_trends_market_cap_zero_turnover_nan(tmp_path):
    """시총 0이면 과거 회전율 계산 불가 → NaN 셀 (오늘은 후보 회전율 유지)."""
    c = _candidate()
    c["market_cap"] = 0
    attach_candidate_trends([c], _daily(), tmp_path, date(2026, 5, 22))
    to = c["trends"]["turnover"]
    assert to[0]["value"] != to[0]["value"]   # NaN
    assert to[1]["value"] != to[1]["value"]   # NaN
    assert to[2]["value"] == 10.0             # 오늘은 후보값


def test_trends_supply_accumulates(tmp_path):
    """investor_daily 2일 누적 → supply 추이 2셀, 1일이면 1셀."""
    inv_dir = tmp_path / "investor_daily"
    inv_dir.mkdir(parents=True)
    df2 = pd.DataFrame([
        {"date": date(2026, 5, 21), "foreign_net_buy": 20_000,
         "institution_net_buy": 0, "program_net_buy": 400_000, "program_net_buy_value": 0},
        {"date": date(2026, 5, 22), "foreign_net_buy": 82_000,
         "institution_net_buy": 0, "program_net_buy": 848_147, "program_net_buy_value": 0},
    ])
    df2.to_parquet(inv_dir / "062970.parquet", index=False)

    c = _candidate()
    attach_candidate_trends([c], _daily(), tmp_path, date(2026, 5, 22))
    supply = c["trends"]["supply"]
    assert len(supply) == 2
    assert supply[0]["foreign"] == 20_000
    assert supply[1]["foreign"] == 82_000
    assert supply[1]["program"] == 848_147


def test_trends_no_supply_file(tmp_path):
    """investor_daily 파일 없으면 supply 빈 리스트 (graceful)."""
    c = _candidate()
    attach_candidate_trends([c], _daily(), tmp_path, date(2026, 5, 22))
    assert c["trends"]["supply"] == []


def test_trends_graceful_empty_daily(tmp_path):
    """daily 비어도 오늘 셀만으로 진행 (예외 X)."""
    c = _candidate()
    attach_candidate_trends([c], pd.DataFrame(), tmp_path, date(2026, 5, 22))
    tv = c["trends"]["trading_value"]
    assert len(tv) == 1
    assert tv[0]["value"] == 10_000_000_000
