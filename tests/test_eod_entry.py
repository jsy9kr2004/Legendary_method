"""eod_entry (종배 막판 진입 점검, 표시 전용) 단위 테스트."""
from __future__ import annotations

import pytest

from src.overnight.eod_entry import build_eod_entry_context, format_eod_entry_line


def test_limit_up_held():
    ctx = build_eod_entry_context(
        prev_close=70000, price=91000, intraday_high=91000, is_limit_up=True,
    )
    assert ctx.is_limit_up
    assert "상한가 유지" in ctx.summary
    assert ctx.gap_now_pct == pytest.approx(30.0)


def test_pullback_and_vp_arrow_up():
    ctx = build_eod_entry_context(
        prev_close=100000, price=112000, intraday_high=115000, is_limit_up=False,
        vp=130.0, vp_5ma=110.0,
    )
    assert ctx.pullback_from_high_pct == pytest.approx((112000 - 115000) / 115000 * 100)
    assert "VP 130↑" in ctx.summary
    assert "고점대비" in ctx.summary


def test_vp_arrow_down_and_weak_candle():
    ctx = build_eod_entry_context(
        prev_close=100000, price=108000, intraday_high=113000, is_limit_up=False,
        vp=95.0, vp_5ma=105.0, vol_accel=0.6, weak_candle=True,
    )
    assert "VP 95↓" in ctx.summary
    assert "가속 0.6x" in ctx.summary
    assert "⚠윗꼬리음봉" in ctx.summary


def test_no_optional_signals():
    ctx = build_eod_entry_context(
        prev_close=100000, price=105000, intraday_high=106000, is_limit_up=False,
    )
    assert "VP" not in ctx.summary
    assert "현재 +5.0%" in ctx.summary


def test_raises_on_nonpositive():
    with pytest.raises(ValueError):
        build_eod_entry_context(prev_close=0, price=1, intraday_high=1, is_limit_up=False)
    with pytest.raises(ValueError):
        build_eod_entry_context(prev_close=1, price=0, intraday_high=1, is_limit_up=False)


def test_format_line_top3_star():
    ctx = build_eod_entry_context(
        prev_close=100000, price=112000, intraday_high=112000, is_limit_up=False, vp=120.0,
    )
    line = format_eod_entry_line("제룡전기", "033100", ctx, is_top3=True)
    assert line.startswith("⭐")
    assert "제룡전기(033100)" in line
    line2 = format_eod_entry_line("LS", "006260", ctx, is_top3=False)
    assert line2.startswith("▸")
