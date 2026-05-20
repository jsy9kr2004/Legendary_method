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
    compute_c_signal_states,
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
    # 손절선 = 98_000 (-2%, 사용자 룰 통일 2026-05-21)
    events = evaluate_triggers(h, now=now, current_price=98_000)
    kinds = [e.kind for e in events]
    assert "A1_stop_price" in kinds
    a1 = next(e for e in events if e.kind == "A1_stop_price")
    assert a1.is_stop_loss is True
    # 카드 안 한 줄 사유 포맷 (round 17): "A1 가격 손절 -2% — ..."
    assert "A1" in a1.text or "손절" in a1.text


def test_trigger_text_is_card_format_not_push():
    """정정 round 17: TriggerEvent.text 는 카드 한 줄 사유. 푸시 prefix 없어야."""
    h, now = _entry(100_000)
    events = evaluate_triggers(h, now=now, current_price=98_000)
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
    # entry_bar_low = 99000. 현재가 98800 → A2 발화 (A1 = 98000 미달이라 안 옴)
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


# ── EOD 컷오프 (A5, round 26, P1-2) ──────────────────────────────────────────
#
# 통설: "14:45 이평선 밑 음봉이면 목숨 걸고 팔아라"
# AND 조건: 시각 ≥ 14:45 + 가격 < MA5 + 직전 분봉 음봉


def _entry_at(price: float, hh: int, mm: int) -> tuple[Holding, datetime]:
    now = datetime(2026, 5, 13, hh, mm, 0)
    h = Holding(
        code="091340",
        entry_price=price,
        entry_time=now - timedelta(minutes=1),
        entry_bar_low=price * 0.99,
        time_stop_minutes=10,
    )
    return h, now


def test_A5_eod_fires_at_1445_below_ma_with_bearish_candle():
    """14:45 + 가격<MA + 음봉 → A5 발화."""
    h, now = _entry_at(100_000, 14, 45)
    candle = classify_candle(o=100_100, h=100_150, l=99_400, c=99_500)  # bearish
    events = evaluate_triggers(
        h, now=now, current_price=99_500, minute_ma_5=99_800, candle=candle,
    )
    assert any(e.kind == "A5_eod_ma_break" for e in events)
    a5 = next(e for e in events if e.kind == "A5_eod_ma_break")
    assert a5.is_stop_loss is True
    assert "EOD" in a5.text or "14:45" in a5.text


def test_A5_no_fire_before_1445():
    """14:44 시점에는 동일 조건이라도 A5 발화 X."""
    h, now = _entry_at(100_000, 14, 44)
    candle = classify_candle(o=100_100, h=100_150, l=99_400, c=99_500)
    events = evaluate_triggers(
        h, now=now, current_price=99_500, minute_ma_5=99_800, candle=candle,
    )
    assert not any(e.kind == "A5_eod_ma_break" for e in events)


def test_A5_no_fire_if_bullish_candle():
    """14:45 + 가격<MA 인데 직전 봉 양봉이면 A5 발화 X (A3 만 발화)."""
    h, now = _entry_at(100_000, 14, 50)
    candle = classify_candle(o=99_400, h=99_700, l=99_300, c=99_600)  # bullish
    events = evaluate_triggers(
        h, now=now, current_price=99_500, minute_ma_5=99_800, candle=candle,
    )
    kinds = [e.kind for e in events]
    assert "A5_eod_ma_break" not in kinds
    # A3 은 발화 (가격 < MA5)
    assert "A3_stop_ma" in kinds


def test_A5_no_fire_if_above_ma():
    """14:45 + 음봉 인데 가격이 MA5 위면 A5 발화 X."""
    h, now = _entry_at(100_000, 14, 50)
    candle = classify_candle(o=100_400, h=100_500, l=99_800, c=99_900)  # bearish
    events = evaluate_triggers(
        h, now=now, current_price=99_900, minute_ma_5=99_500, candle=candle,
    )
    assert not any(e.kind == "A5_eod_ma_break" for e in events)


def test_A5_no_fire_without_ma_input():
    """minute_ma_5 None 이면 A3 스킵과 동일하게 A5 도 스킵."""
    h, now = _entry_at(100_000, 14, 50)
    candle = classify_candle(o=100_100, h=100_150, l=99_400, c=99_500)
    events = evaluate_triggers(
        h, now=now, current_price=99_500, minute_ma_5=None, candle=candle,
    )
    assert not any(e.kind == "A5_eod_ma_break" for e in events)


def test_A5_text_is_card_format():
    """A5 도 카드용 한 줄 — 푸시 prefix X (round 17 정책)."""
    h, now = _entry_at(100_000, 14, 50)
    candle = classify_candle(o=100_100, h=100_150, l=99_400, c=99_500)
    events = evaluate_triggers(
        h, now=now, current_price=99_500, minute_ma_5=99_800, candle=candle,
    )
    a5 = next(e for e in events if e.kind == "A5_eod_ma_break")
    assert "[" not in a5.text or "[매도" not in a5.text
    assert "\n" not in a5.text


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


# ── compute_c_signal_states — 감시/보유 모드 분기 ────────────────────────────


def test_c_states_watch_all_off_when_quiet():
    """감시 모드 — 모든 입력 정상/긍정이면 C1~C5 모두 ❌."""
    states = compute_c_signal_states(
        vp_5ma_prev=140.0, vp_5ma_now=135.0,  # 100 위 → C1=False
        divergence=compute_divergence(  # bearish=False
            price_now=100.0, price_5m_ago=98.0,
            vp_5ma_now=135.0, vp_5ma_5m_ago=130.0,
        ),
        vol_accel_1m=2.0,                # 0.5 위 → C3=False
        candle=classify_candle(o=100, h=105, l=99, c=104),  # 양봉
        holding=None,
    )
    assert states == {
        "C1_vp_below_100": False,
        "C2_bearish_divergence": False,
        "C3_vol_drain": False,
        "C4_bearish_candle": False,
        "C5_vi_failure": False,
    }


def test_c_states_watch_each_signal_lit():
    """감시 모드 — 각 시그널이 instantaneous 켜지면 ✅ (C5 제외)."""
    # C1: VP 5MA 현재값이 100 미만
    s1 = compute_c_signal_states(
        vp_5ma_prev=120.0, vp_5ma_now=95.0,
        divergence=None, vol_accel_1m=None, candle=None, holding=None,
    )
    assert s1["C1_vp_below_100"] is True

    # C2: Bearish Divergence (가격↑ / VP_5MA↓)
    div = compute_divergence(
        price_now=102.0, price_5m_ago=100.0,
        vp_5ma_now=120.0, vp_5ma_5m_ago=140.0,
    )
    assert div.bearish is True
    s2 = compute_c_signal_states(
        vp_5ma_prev=140.0, vp_5ma_now=120.0,
        divergence=div, vol_accel_1m=None, candle=None, holding=None,
    )
    assert s2["C2_bearish_divergence"] is True

    # C3: 1분 가속 < 0.5 (instantaneous, 지속 시간 무시)
    s3 = compute_c_signal_states(
        vp_5ma_prev=None, vp_5ma_now=None,
        divergence=None, vol_accel_1m=0.3, candle=None, holding=None,
    )
    assert s3["C3_vol_drain"] is True

    # C4: 윗꼬리 50%↑ 음봉
    bearish_candle = classify_candle(o=110, h=130, l=100, c=105)
    s4 = compute_c_signal_states(
        vp_5ma_prev=None, vp_5ma_now=None,
        divergence=None, vol_accel_1m=None, candle=bearish_candle, holding=None,
    )
    assert s4["C4_bearish_candle"] is True


def test_c_states_watch_c5_always_off():
    """감시 모드에서 C5 는 VI 인프라 부재로 항상 False (render 가 행 숨김)."""
    states = compute_c_signal_states(
        vp_5ma_prev=50.0, vp_5ma_now=30.0,   # 다른 시그널 다 켜도
        divergence=None, vol_accel_1m=0.1,
        candle=classify_candle(o=110, h=130, l=100, c=105),
        holding=None,
    )
    assert states["C5_vi_failure"] is False


def test_c_states_hold_uses_triggers_fired():
    """보유 모드 — holding.triggers_fired set 그대로 반영 (sticky).

    감시 모드와 달리 시장 메트릭 인자는 무시 — 이미 evaluate_triggers 가
    holding 갱신했고 본 함수는 표시용 dict 만 만들어 줌.
    """
    h, _ = _entry(100_000)
    h.triggers_fired.add("C1_vp_below_100")
    h.triggers_fired.add("C4_bearish_candle")
    states = compute_c_signal_states(
        # instantaneous 입력은 다 False 상태로 줘도, holding 분기는 무시.
        vp_5ma_prev=200.0, vp_5ma_now=150.0,
        divergence=None, vol_accel_1m=5.0,
        candle=classify_candle(o=100, h=105, l=99, c=104),
        holding=h,
    )
    assert states["C1_vp_below_100"] is True
    assert states["C4_bearish_candle"] is True
    assert states["C2_bearish_divergence"] is False
    assert states["C3_vol_drain"] is False
    assert states["C5_vi_failure"] is False


def test_c_states_watch_nan_inputs_safe():
    """NaN/None 입력은 보수적으로 False — fail-loud 보다 카드가 망가지지 않게."""
    states = compute_c_signal_states(
        vp_5ma_prev=float("nan"), vp_5ma_now=float("nan"),
        divergence=None, vol_accel_1m=float("nan"),
        candle=None, holding=None,
    )
    assert all(v is False for v in states.values())


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


# ── 일일 reset (round 40) ────────────────────────────────────────────────────


def _reload_exit_triggers(tmp_path, monkeypatch):
    """tmp DATA_DIR 격리 + config + exit_triggers 모듈 reload."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import importlib
    import src.config
    importlib.reload(src.config)
    import src.jongbae.exit_triggers as et
    importlib.reload(et)
    return et


def test_maybe_reset_holdings_first_call_archives_and_clears(tmp_path, monkeypatch):
    """평일 첫 호출 — 기존 holdings 가 archive 로 백업 + holdings.json 비워짐 + last_reset 갱신."""
    et = _reload_exit_triggers(tmp_path, monkeypatch)
    # 평일 (2026-05-18 월). is_business_day 가 True 인 일자.
    now = datetime(2026, 5, 18, 9, 0, 0)
    et.save_holdings({"005930": et.Holding(
        code="005930", entry_price=70000, entry_time=now,
    )})
    assert et.load_holdings()

    did = et.maybe_reset_holdings(now)
    assert did is True
    assert et.load_holdings() == {}  # 비워짐
    # archive 파일 존재 + 005930 데이터 보존
    archive = tmp_path / "state" / "holdings.archive" / "2026-05-18.json"
    assert archive.exists()
    import json
    archived = json.loads(archive.read_text(encoding="utf-8"))
    assert "005930" in archived
    assert archived["005930"]["entry_price"] == 70000
    # last_reset 갱신
    assert (tmp_path / "state" / "last_reset.txt").read_text().strip() == "2026-05-18"


def test_maybe_reset_holdings_same_day_idempotent(tmp_path, monkeypatch):
    """같은 날 두 번째 호출 — skip. 장중 재기동 시 보유 안전."""
    et = _reload_exit_triggers(tmp_path, monkeypatch)
    now = datetime(2026, 5, 18, 8, 30, 0)
    assert et.maybe_reset_holdings(now) is True

    # 사용자가 09:30 매수 → holdings 생김
    later = datetime(2026, 5, 18, 9, 30, 0)
    et.save_holdings({"091340": et.Holding(
        code="091340", entry_price=91300, entry_time=later,
    )})
    # 13:00 사용자 코드 업데이트 후 재기동
    restart = datetime(2026, 5, 18, 13, 0, 0)
    did = et.maybe_reset_holdings(restart)
    assert did is False  # 오늘 이미 reset
    assert "091340" in et.load_holdings()  # 보유 안전


def test_maybe_reset_holdings_skips_weekend_and_holiday(tmp_path, monkeypatch):
    """주말 / 휴장일 호출 — skip. 어제 잔여 보유가 다음 영업일까지 안전 이월."""
    et = _reload_exit_triggers(tmp_path, monkeypatch)
    sat = datetime(2026, 5, 16, 9, 0, 0)  # 토요일
    et.save_holdings({"005930": et.Holding(
        code="005930", entry_price=70000, entry_time=sat,
    )})
    did = et.maybe_reset_holdings(sat)
    assert did is False
    assert "005930" in et.load_holdings()  # 그대로 유지
    assert not (tmp_path / "state" / "last_reset.txt").exists()


def test_maybe_reset_holdings_empty_holdings_still_marks_date(tmp_path, monkeypatch):
    """holdings 이 비어있어도 last_reset 만 갱신 — 같은 날 재호출 skip 보장."""
    et = _reload_exit_triggers(tmp_path, monkeypatch)
    now = datetime(2026, 5, 18, 8, 30, 0)
    assert et.load_holdings() == {}

    did = et.maybe_reset_holdings(now)
    assert did is True
    # 비어있어서 archive 파일은 생성 X (불필요)
    assert not (tmp_path / "state" / "holdings.archive" / "2026-05-18.json").exists()
    # last_reset 은 갱신 — 두 번째 호출은 skip
    assert et.maybe_reset_holdings(now) is False
