"""forward_log (종배 14:50 후보 + 다음날 갭 join) 단위 테스트."""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from src.overnight.forward_log import append_outcomes, load_outcomes
from src.report.decision import save_decision_candidates


def _daily() -> pd.DataFrame:
    return pd.DataFrame([
        {"code": "033100", "date": dt.date(2026, 5, 18), "open": 90000,
         "high": 91000, "low": 89000, "close": 90000},
        {"code": "033100", "date": dt.date(2026, 5, 19), "open": 93600,
         "high": 99000, "low": 88200, "close": 95000},
    ])


def test_append_outcomes_joins_next_day_gaps(tmp_path):
    D = dt.date(2026, 5, 18)
    cands = [
        {"code": "033100", "name": "제룡전기", "rank": 1, "priority": "normal",
         "is_top3": True, "daily_return": 12.0, "turnover": 5.0,
         "sizing": {"kelly_bucket": 0.117}, "sizing_bucket": "1~10위",
         "intraday_signals": {"ccnl_strength": {"ccnl_strength": 142.0}},
         "candle_aux": {"big_candle_count": 1}},
        {"code": "000370", "name": "엑스", "rank": 40, "priority": "excluded"},
    ]
    save_decision_candidates(cands, tmp_path, dt.datetime(2026, 5, 18, 14, 50))

    out = append_outcomes(D, _daily(), tmp_path)
    assert out is not None
    recs = load_outcomes(tmp_path, D)
    assert len(recs) == 1  # excluded 제외
    r = recs[0]
    assert r["code"] == "033100"
    assert r["outcome_date"] == "2026-05-19"
    assert r["gap_open"] == pytest.approx(4.0)    # (93600-90000)/90000
    assert r["gap_high"] == pytest.approx(10.0)   # (99000-90000)/90000
    assert r["gap_low"] == pytest.approx(-2.0)    # (88200-90000)/90000
    assert r["is_top3"] is True
    assert r["kelly_bucket"] == pytest.approx(0.117)
    # backtest 불가 신호 보존 (미래 factor_edge 용)
    assert r["intraday_signals"]["ccnl_strength"]["ccnl_strength"] == 142.0


def test_idempotent_overwrite(tmp_path):
    D = dt.date(2026, 5, 18)
    save_decision_candidates(
        [{"code": "033100", "priority": "normal"}], tmp_path,
        dt.datetime(2026, 5, 18, 14, 50))
    append_outcomes(D, _daily(), tmp_path)
    append_outcomes(D, _daily(), tmp_path)  # 두 번째 호출도 안전
    assert len(load_outcomes(tmp_path, D)) == 1


def test_no_next_day_bar_returns_none(tmp_path):
    D = dt.date(2026, 5, 18)
    save_decision_candidates(
        [{"code": "033100", "priority": "normal"}], tmp_path,
        dt.datetime(2026, 5, 18, 14, 50))
    daily = pd.DataFrame([
        {"code": "033100", "date": dt.date(2026, 5, 18), "open": 1, "high": 1,
         "low": 1, "close": 90000},
    ])
    assert append_outcomes(D, daily, tmp_path) is None  # 다음날 바 부재


def test_empty_inputs(tmp_path):
    assert append_outcomes(dt.date(2026, 5, 18), pd.DataFrame(), tmp_path) is None
    assert load_outcomes(tmp_path, dt.date(2026, 5, 18)) == []
