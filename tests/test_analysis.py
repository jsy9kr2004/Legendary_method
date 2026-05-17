"""src.analysis.replay / regret 단위 테스트.

stdout capture 로 핵심 키워드 검증. 정밀한 포맷보단 "필요한 정보가 출력에 들어있나".
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

from src.analysis.regret import regret_summary
from src.analysis.replay import replay_stock
from src.data.tick_log import TickLogRow, TradeEvent, append_tick_log, append_trade_event


@pytest.fixture
def tmp_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return tmp_path


def _make_sample_logs(day: date) -> None:
    """샘플 tick_logs + trades 생성."""
    now1 = datetime.combine(day, datetime.min.time()).replace(hour=9, minute=32, second=0)
    now2 = now1.replace(minute=33)
    now3 = now1.replace(minute=34)

    rows = [
        # 091340 STRONG 도달 + 매수
        TickLogRow(ts=now1.isoformat(), code="091340", name="대한광통신",
                   is_auto=True, price=91300, daily_return=30.0, is_limit_up=True,
                   vol_accel_5m=12.4, vol_accel_1m=6.8, vp=142.0,
                   buy_score=6.5, buy_grade="STRONG",
                   buy_reasons=["+1 회전율", "+2 가속", "+2 양봉"],
                   funnel_passed_rising=False),
        TickLogRow(ts=now2.isoformat(), code="091340", name="대한광통신",
                   is_auto=True, price=91500, daily_return=30.3,
                   buy_score=6.5, buy_grade="STRONG"),
        # 075180 WATCH 도달 + 매수 안 함
        TickLogRow(ts=now1.isoformat(), code="075180", name="제룡전기",
                   is_auto=True, price=90300, daily_return=28.6,
                   vol_accel_5m=5.5, vol_accel_1m=4.2, vp=120.0,
                   buy_score=3.5, buy_grade="WATCH",
                   buy_reasons=["+1 회전율", "+0.5 호가"]),
        # 001440 STRONG 도달 + 매수 안 함 (후회 후보)
        TickLogRow(ts=now2.isoformat(), code="001440", name="대한전선",
                   is_auto=False, is_rising=True, price=4410, daily_return=26.0,
                   vol_accel_5m=5.2, vol_accel_1m=4.5, vp=121.0,
                   buy_score=5.2, buy_grade="STRONG",
                   buy_reasons=["+1 회전율 급증", "+2 가속 5.2배"],
                   funnel_passed_rising=True),
        # NEUTRAL 종목 (등급 표 surface 안 됨)
        TickLogRow(ts=now3.isoformat(), code="000660", name="SK하이닉스",
                   is_auto=False, price=165000, daily_return=1.5,
                   buy_score=1.0, buy_grade="NEUTRAL"),
    ]
    # 매수 한 종목: 091340 만
    append_tick_log(rows, now1)
    append_trade_event(
        TradeEvent(ts=now2.isoformat(), code="091340", name="대한광통신",
                   action="buy", price=91500, source="command"),
        now2,
    )


# ── replay ───────────────────────────────────────────────────────────────────


def test_replay_existing_code(tmp_data_dir, capsys):
    day = date(2026, 5, 18)
    _make_sample_logs(day)
    replay_stock("091340", day)
    out = capsys.readouterr().out
    assert "091340 대한광통신" in out
    assert "STRONG" in out
    assert "BUY" in out          # 매수 이벤트 마커
    assert "91300" in out or "91,300" in out
    assert "회전율" in out or "가속" in out or "양봉" in out  # reasons


def test_replay_missing_code(tmp_data_dir, capsys):
    day = date(2026, 5, 18)
    _make_sample_logs(day)
    replay_stock("999999", day)
    out = capsys.readouterr().out
    assert "데이터 없음" in out or "X" in out


def test_replay_no_logs(tmp_data_dir, capsys):
    replay_stock("091340", date(2026, 1, 1))
    out = capsys.readouterr().out
    assert "tick_logs 없음" in out


def test_replay_time_filter(tmp_data_dir, capsys):
    day = date(2026, 5, 18)
    _make_sample_logs(day)
    # 09:33 이후만
    replay_stock("091340", day, since="09:33")
    out = capsys.readouterr().out
    # 09:32:00 tick 은 제외, 09:33:00 만 남음
    assert "09:33:00" in out
    # n=1 표시 (091340 의 09:33 1 tick 만)
    assert "n=1" in out


# ── regret ───────────────────────────────────────────────────────────────────


def test_regret_summary_lists_strong_and_watch(tmp_data_dir, capsys):
    day = date(2026, 5, 18)
    _make_sample_logs(day)
    regret_summary(day)
    out = capsys.readouterr().out
    # STRONG/WATCH 둘 다 도달
    assert "STRONG" in out
    assert "WATCH" in out
    # 091340 + 075180 + 001440 surface
    assert "091340" in out
    assert "075180" in out
    assert "001440" in out


def test_regret_marks_bought_codes(tmp_data_dir, capsys):
    day = date(2026, 5, 18)
    _make_sample_logs(day)
    regret_summary(day)
    out = capsys.readouterr().out
    # 091340 = 매수, 다른 STRONG 은 안 산 종목
    assert "💰 매수" in out
    # STRONG 떴는데 안 산 종목 섹션 — 001440
    assert "안 산 종목" in out or "매수 누락" in out
    # 매수 이벤트 표
    assert "BUY" in out


def test_regret_no_data(tmp_data_dir, capsys):
    regret_summary(date(2026, 1, 1))
    out = capsys.readouterr().out
    assert "없음" in out
