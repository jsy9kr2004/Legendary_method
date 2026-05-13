"""src.jongbae.exit_triggers (R15) 단위 테스트.

자동 매매 금지 정책 검증: 모든 트리거는 TriggerEvent (텔레그램 메시지)만 반환.
실주문 코드 없음.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.jongbae.candle import classify_candle
from src.jongbae.divergence import compute_divergence
from src.jongbae.exit_triggers import (
    Holding,
    Mode,
    evaluate_triggers,
    load_holdings,
    save_holdings,
)


def _entry(price: float = 100_000, minutes_ago: int = 1) -> tuple[Holding, datetime]:
    now = datetime(2026, 5, 13, 10, 0, 0)
    entry_time = now - timedelta(minutes=minutes_ago)
    h = Holding(
        code="091340",
        entry_price=price,
        entry_time=entry_time,
        entry_bar_low=price * 0.99,
        time_stop_minutes=10,
    )
    return h, now


# ── 가격 손절 (A1) ───────────────────────────────────────────────────────────


def test_A1_stop_loss_price():
    h, now = _entry(100_000)
    # 손절선 = 98_500
    events = evaluate_triggers(h, now=now, current_price=98_500)
    kinds = [e.kind for e in events]
    assert "A1_stop_price" in kinds
    a1 = next(e for e in events if e.kind == "A1_stop_price")
    assert a1.is_stop_loss is True
    # 카드 안 한 줄 사유 포맷 (round 17): "A1 가격 손절 -1.5% — ..."
    assert "A1" in a1.text or "손절" in a1.text


def test_trigger_text_is_card_format_not_push():
    """정정 round 17: TriggerEvent.text 는 카드 한 줄 사유. 푸시 prefix 없어야."""
    h, now = _entry(100_000)
    events = evaluate_triggers(h, now=now, current_price=98_500)
    a1 = next(e for e in events if e.kind == "A1_stop_price")
    # 폐기된 푸시 prefix
    assert "[손절선 도달]" not in a1.text
    assert "[매도 트리거]" not in a1.text
    # 시각 prefix 도 카드용엔 X (카드 자체에 시각 표시)
    assert now.strftime("%H:%M:%S") not in a1.text
    # 한 줄 (개행 없음) — 카드 안 한 줄로 표시되어야
    assert "\n" not in a1.text


def test_trigger_text_no_push_prefix_for_C():
    """C 시그널 청산도 카드용 한 줄 — 푸시 prefix X."""
    h, now = _entry(100_000)
    events = evaluate_triggers(
        h, now=now, current_price=100_500,
        vp_5ma_prev=105.0, vp_5ma_now=98.0,
    )
    c1 = next(e for e in events if e.kind == "C1_vp_below_100")
    assert "[매도 트리거]" not in c1.text
    assert "\n" not in c1.text
    assert "C1" in c1.text or "VP" in c1.text


def test_A1_no_trigger_above_stop():
    h, now = _entry(100_000)
    events = evaluate_triggers(h, now=now, current_price=99_000)
    assert not any(e.kind == "A1_stop_price" for e in events)


# ── 봉 저점 손절 (A2) ────────────────────────────────────────────────────────


def test_A2_stop_below_entry_bar_low():
    h, now = _entry(100_000)
    # entry_bar_low = 99000. 현재가 98800 → A2 발화 (A1 = 98500 미달이라 안 옴)
    events = evaluate_triggers(h, now=now, current_price=98_800)
    assert any(e.kind == "A2_stop_bar_low" for e in events)


# ── 이평 손절 (A3) ───────────────────────────────────────────────────────────


def test_A3_stop_below_ma5():
    h, now = _entry(100_000)
    events = evaluate_triggers(h, now=now, current_price=99_500, minute_ma_5=99_800)
    assert any(e.kind == "A3_stop_ma" for e in events)


def test_A3_no_ma_skips():
    h, now = _entry(100_000)
    events = evaluate_triggers(h, now=now, current_price=99_500, minute_ma_5=None)
    assert not any(e.kind == "A3_stop_ma" for e in events)


# ── 시간 손절 (A4) ───────────────────────────────────────────────────────────


def test_A4_time_stop_below_required_profit():
    """진입 후 10분, +0.3% — 시간 손절 발화."""
    h, now = _entry(100_000, minutes_ago=10)
    events = evaluate_triggers(h, now=now, current_price=100_300)
    assert any(e.kind == "A4_stop_time" for e in events)


def test_A4_no_trigger_before_time():
    h, now = _entry(100_000, minutes_ago=5)
    events = evaluate_triggers(h, now=now, current_price=100_100)
    assert not any(e.kind == "A4_stop_time" for e in events)


def test_A4_no_trigger_above_required_profit():
    """10분 경과했지만 +0.6% → 충족, 발화 X."""
    h, now = _entry(100_000, minutes_ago=10)
    events = evaluate_triggers(h, now=now, current_price=100_600)
    assert not any(e.kind == "A4_stop_time" for e in events)


# ── 익절 (B1/B2) — 멱등 ──────────────────────────────────────────────────────


def test_B1_take_profit_1_oneshot():
    h, now = _entry(100_000)
    events_1 = evaluate_triggers(h, now=now, current_price=102_500)
    assert any(e.kind == "B1_take_profit_1" for e in events_1)

    # 동일 가격에서 다시 호출 → B1 발화 X (멱등)
    events_2 = evaluate_triggers(h, now=now, current_price=102_500)
    assert not any(e.kind == "B1_take_profit_1" for e in events_2)


def test_B2_take_profit_2_oneshot():
    h, now = _entry(100_000)
    events = evaluate_triggers(h, now=now, current_price=104_000)
    kinds = [e.kind for e in events]
    assert "B1_take_profit_1" in kinds
    assert "B2_take_profit_2" in kinds

    events_2 = evaluate_triggers(h, now=now, current_price=104_000)
    kinds_2 = [e.kind for e in events_2]
    assert "B1_take_profit_1" not in kinds_2
    assert "B2_take_profit_2" not in kinds_2


# ── 트레일링 (B3) — B1 발화 후 활성 ──────────────────────────────────────────


def test_B3_trailing_inactive_before_B1():
    h, now = _entry(100_000)
    # B1 미발화 — 트레일링 X
    events = evaluate_triggers(h, now=now, current_price=101_500)
    assert not any(e.kind == "B3_trailing" for e in events)


def test_B3_trailing_after_B1():
    h, now = _entry(100_000)
    # 1) +2.5% 도달 → B1 발화, high=102_500
    evaluate_triggers(h, now=now, current_price=102_500)
    # 2) 잠시 후 102_000 — high × 0.985 = 100_962.5
    later = now + timedelta(seconds=30)
    events = evaluate_triggers(h, now=later, current_price=100_900)
    assert any(e.kind == "B3_trailing" for e in events)


# ── C1 VP 5MA 100 하향 ───────────────────────────────────────────────────────


def test_C1_vp_below_100_cross():
    h, now = _entry(100_000)
    events = evaluate_triggers(
        h, now=now, current_price=100_500,
        vp_5ma_prev=105.0, vp_5ma_now=98.0,
    )
    assert any(e.kind == "C1_vp_below_100" for e in events)


def test_C1_no_cross():
    h, now = _entry(100_000)
    events = evaluate_triggers(
        h, now=now, current_price=100_500,
        vp_5ma_prev=95.0, vp_5ma_now=92.0,
    )
    assert not any(e.kind == "C1_vp_below_100" for e in events)


def test_C1_oneshot():
    h, now = _entry(100_000)
    evaluate_triggers(h, now=now, current_price=100_500, vp_5ma_prev=105, vp_5ma_now=98)
    events = evaluate_triggers(h, now=now, current_price=100_500, vp_5ma_prev=98, vp_5ma_now=95)
    assert not any(e.kind == "C1_vp_below_100" for e in events)


# ── C2 Bearish Divergence ────────────────────────────────────────────────────


def test_C2_bearish_divergence():
    h, now = _entry(100_000)
    div = compute_divergence(price_now=101_000, price_5m_ago=100_000, vp_5ma_now=98, vp_5ma_5m_ago=110)
    events = evaluate_triggers(h, now=now, current_price=101_000, divergence=div)
    assert any(e.kind == "C2_bearish_divergence" for e in events)


# ── C3 자금 고갈 (2분 지속) ──────────────────────────────────────────────────


def test_C3_vol_drain_requires_persist():
    h, now = _entry(100_000)
    # 첫 tick — vol_accel_1m=0.3
    events_1 = evaluate_triggers(h, now=now, current_price=100_000, vol_accel_1m_value=0.3)
    assert not any(e.kind == "C3_vol_drain" for e in events_1)

    # 1분 후 — 아직 미달
    t2 = now + timedelta(seconds=60)
    events_2 = evaluate_triggers(h, now=t2, current_price=100_000, vol_accel_1m_value=0.3)
    assert not any(e.kind == "C3_vol_drain" for e in events_2)

    # 2분 후 — 발화
    t3 = now + timedelta(seconds=121)
    events_3 = evaluate_triggers(h, now=t3, current_price=100_000, vol_accel_1m_value=0.3)
    assert any(e.kind == "C3_vol_drain" for e in events_3)


def test_C3_recovery_resets_counter():
    h, now = _entry(100_000)
    evaluate_triggers(h, now=now, current_price=100_000, vol_accel_1m_value=0.3)
    # 회복 → 카운터 리셋
    t2 = now + timedelta(seconds=60)
    evaluate_triggers(h, now=t2, current_price=100_000, vol_accel_1m_value=1.5)
    # 다시 하락 → 재시작 (즉시 발화 X)
    t3 = now + timedelta(seconds=130)
    events = evaluate_triggers(h, now=t3, current_price=100_000, vol_accel_1m_value=0.3)
    assert not any(e.kind == "C3_vol_drain" for e in events)


# ── C4 윗꼬리 음봉 ───────────────────────────────────────────────────────────


def test_C4_bearish_long_upper_wick():
    h, now = _entry(100_000)
    # 음봉 + 윗꼬리 > 50%
    candle = classify_candle(o=110, h=130, l=100, c=105)
    events = evaluate_triggers(h, now=now, current_price=100_000, candle=candle)
    assert any(e.kind == "C4_bearish_candle" for e in events)


# ── C5 VI 재상승 실패 ────────────────────────────────────────────────────────


def test_C5_vi_failure_after_5min():
    h, now = _entry(100_000)
    vi_time = now - timedelta(seconds=400)  # 6분 40초 전 발동
    events = evaluate_triggers(
        h, now=now, current_price=100_000,
        vi_triggered_at=vi_time, vi_recovered=False,
    )
    assert any(e.kind == "C5_vi_failure" for e in events)


def test_C5_no_failure_if_recovered():
    h, now = _entry(100_000)
    vi_time = now - timedelta(seconds=400)
    events = evaluate_triggers(
        h, now=now, current_price=100_000,
        vi_triggered_at=vi_time, vi_recovered=True,
    )
    assert not any(e.kind == "C5_vi_failure" for e in events)


# ── 정책 검증 ────────────────────────────────────────────────────────────────


def test_no_order_execution_in_module():
    """exit_triggers 모듈 코드에 KIS 주문 실행 함수가 import 되지 않아야 함."""
    import src.jongbae.exit_triggers as mod
    src_code = Path(mod.__file__).read_text(encoding="utf-8")
    # 금지 키워드 — 실주문 관련
    for kw in ("place_order", "submit_order", "send_order", "execute_order"):
        assert kw not in src_code, f"R15 정책 위반: {kw} 가 exit_triggers.py 에 있음"


# ── 영속화 ───────────────────────────────────────────────────────────────────


def test_save_load_holdings_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    # config 재로딩
    import importlib
    import src.config
    importlib.reload(src.config)
    import src.jongbae.exit_triggers as et
    importlib.reload(et)

    now = datetime(2026, 5, 13, 9, 45, 0)
    h = et.Holding(
        code="091340",
        entry_price=91_300,
        entry_time=now,
        entry_bar_low=90_800,
        time_stop_minutes=10,
        high_since_entry=92_000,
        triggers_fired={"B1_take_profit_1"},
    )
    et.save_holdings({"091340": h})
    loaded = et.load_holdings()
    assert "091340" in loaded
    assert loaded["091340"].entry_price == 91_300
    assert loaded["091340"].entry_bar_low == 90_800
    assert "B1_take_profit_1" in loaded["091340"].triggers_fired


def test_load_holdings_missing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import importlib
    import src.config
    importlib.reload(src.config)
    import src.jongbae.exit_triggers as et
    importlib.reload(et)
    assert et.load_holdings() == {}


def test_load_holdings_corrupt_file(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import importlib
    import src.config
    importlib.reload(src.config)
    import src.jongbae.exit_triggers as et
    importlib.reload(et)

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "holdings.json").write_text("not json", encoding="utf-8")
    assert et.load_holdings() == {}
