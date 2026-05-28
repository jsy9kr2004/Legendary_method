"""src.dashboard.render.build_monitor_payload 단위 테스트 (M7).

PWA 대시보드용 구조화 페이로드 — JSON 직렬화 안전성 + 핵심 필드 매핑 검증.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from src.dashboard.render import build_monitor_payload
from src.dashboard.state import LeaderState, MonitoredStock, Source
from src.scalping.score.divergence import DivergenceState
from src.scalping.exit.triggers import Holding

# 2026-05-29 단저단고 패러다임 — Exit.Triggers 청산 시그널 카드/페이로드 표시 폐기.
DEPRECATED_CARD_DISPLAY = pytest.mark.skip(
    reason="2026-05-29 단저단고 패러다임 — trigger_lines 카드/페이로드 표시 폐기"
)


def _stock(
    code: str = "075180",
    name: str = "제룡전기",
    source: Source = Source.AUTO,
    themes: list[str] | None = None,
    buy_score: float | None = None,
    buy_grade: str | None = None,
    buy_reasons: list[str] | None = None,
) -> MonitoredStock:
    """round 35: source 인자를 받아 해당 flag 켜기 (테스트 호환)."""
    return MonitoredStock(
        code=code,
        name=name,
        is_auto=(source == Source.AUTO),
        is_rising=(source == Source.RISING),
        is_manual=(source == Source.MANUAL),
        added_at=datetime(2026, 5, 11, 9, 0),
        themes=themes or ["전기/전선"],
        buy_score=buy_score,
        buy_grade=buy_grade,
        buy_reasons=buy_reasons or [],
    )


def test_payload_basic_fields():
    snap = {
        "price": 91300,
        "prev_close": 70200,
        "daily_return": 30.0,
        "is_limit_up": True,
        "turnover": 18.3,
        "trading_value": 124_700_000_000,
        "rank": 1,
    }
    payload = build_monitor_payload(
        _stock(buy_score=6.5, buy_grade="STRONG", buy_reasons=["+1 거래대금", "+2 가속"]),
        snap,
        accel_ratio=5.5,
        recent_bar_value=5_000_000_000,
        ccnl={"ccnl_strength": 142.0, "buy_ratio": 60.0},
        asking={
            "bid_total_volume": 320_000,
            "ask_total_volume": 45_000,
            "bid_ask_ratio": 7.1,
            "bid1_price": 91200,
            "bid1_volume": 850,
            "ask1_price": 91300,
            "ask1_volume": 120,
        },
        investor=None,
        now=datetime(2026, 5, 11, 9, 32, 18),
        accel_ratio_1m=5.5,
        last_bar_value=1_000_000_000,
        vp_1ma=135.0,
        vp_5ma=138.0,
    )
    assert payload["code"] == "075180"
    assert payload["name"] == "제룡전기"
    assert payload["source"] == "auto"
    assert payload["themes"] == ["전기/전선"]
    assert payload["header"]["grade"] == "STRONG"
    assert payload["header"]["score"] == 6.5
    assert payload["header"]["reasons"] == ["+1 거래대금", "+2 가속"]
    assert payload["price"]["current"] == 91300
    assert payload["price"]["change_pct"] == 30.0
    assert payload["price"]["is_limit_up"] is True
    # +29% 매도가 — 70200 * 1.29 = 90558 → 호가단위 100 (5만~20만) → 90500
    assert payload["price"]["sell_29_pct"] == 90500
    assert payload["volume"]["rank"] == 1
    assert payload["volume"]["turnover_pct"] == 18.3
    assert payload["accel_5m"]["ratio"] == 5.5
    assert payload["accel_5m"]["bar_value"] == 5_000_000_000
    assert payload["accel_1m"]["ratio"] == 5.5
    assert payload["vp"]["current"] == 142.0
    assert payload["vp"]["ma_5"] == 138.0
    assert payload["vp"]["ma_1"] == 135.0
    assert payload["asking"]["ratio"] == 7.1
    assert payload["asking"]["bid1_price"] == 91200
    assert payload["updated_at"].startswith("2026-05-11T09:32:18")


def test_payload_json_serializable():
    snap = {
        "price": 91300, "prev_close": 70200, "daily_return": 30.0,
        "is_limit_up": True, "turnover": 18.3,
        "trading_value": 124_700_000_000, "rank": 1,
    }
    payload = build_monitor_payload(
        _stock(),
        snap,
        accel_ratio=5.5,
        recent_bar_value=5_000_000_000,
        ccnl={"ccnl_strength": 142.0, "buy_ratio": 60.0},
        asking={"bid_total_volume": 320_000, "ask_total_volume": 45_000, "bid_ask_ratio": 7.1},
        investor=None,
        now=datetime(2026, 5, 11, 9, 32, 18),
    )
    # NaN/datetime/enum 없이 json.dumps 통과해야 함
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "075180" in serialized
    # 역직렬화도 가능
    loaded = json.loads(serialized)
    assert loaded["code"] == "075180"


def test_payload_nan_to_none():
    """NaN/Inf 가 None 으로 sanitize 되어야 JSON 직렬화 안전."""
    snap = {
        "price": 91300, "prev_close": 70200,
        "daily_return": float("nan"),
        "is_limit_up": False,
        "turnover": float("nan"),
        "trading_value": float("nan"),
        "rank": 1,
    }
    payload = build_monitor_payload(
        _stock(),
        snap,
        accel_ratio=float("nan"),
        recent_bar_value=None,
        ccnl={"ccnl_strength": float("nan"), "buy_ratio": float("inf")},
        asking=None,
        investor=None,
        now=datetime(2026, 5, 11, 9, 32, 18),
        vp_1ma=float("nan"),
        vp_5ma=float("nan"),
    )
    assert payload["price"]["change_pct"] is None
    assert payload["volume"]["turnover_pct"] is None
    assert payload["volume"]["amount"] is None
    assert payload["accel_5m"]["ratio"] is None
    assert payload["vp"]["current"] is None
    assert payload["vp"]["ma_5"] is None
    assert payload["vp"]["buy_ratio"] is None
    # json.dumps 통과
    json.dumps(payload, ensure_ascii=False)


def test_payload_source_hold_when_holding():
    """holding 인자가 있으면 source='hold' override (텔레그램 카드와 일관)."""
    holding = Holding(
        code="075180",
        entry_price=89000.0,
        entry_time=datetime(2026, 5, 11, 9, 2, 0),
        time_stop_minutes=10,
    )
    snap = {"price": 91300, "prev_close": 70200, "daily_return": 30.0, "rank": 1}
    payload = build_monitor_payload(
        _stock(source=Source.MANUAL),  # 원래는 manual 인데
        snap,
        accel_ratio=2.0, recent_bar_value=None,
        ccnl=None, asking=None, investor=None,
        now=datetime(2026, 5, 11, 9, 32, 18),
        holding=holding,
    )
    assert payload["source"] == "hold"
    assert payload["holding"] is not None
    assert payload["holding"]["entry_price"] == 89000
    assert payload["holding"]["elapsed_sec"] == 30 * 60 + 18  # 30분 18초
    # pnl_pct = (91300 - 89000) / 89000 * 100 ≈ 2.58
    assert payload["holding"]["pnl_pct"] is not None
    assert abs(payload["holding"]["pnl_pct"] - 2.584) < 0.01
    assert payload["holding"]["stop_loss_price"] == int(89000 * 0.98)  # A1 -2% (commit fa5b248)
    assert "triggers_fired" in payload["holding"]


def test_payload_source_rising():
    payload = build_monitor_payload(
        _stock(source=Source.RISING, buy_score=3.5, buy_grade="WATCH"),
        snapshot_row=None,
        accel_ratio=None, recent_bar_value=None,
        ccnl=None, asking=None, investor=None,
        now=datetime(2026, 5, 11, 9, 32, 18),
    )
    assert payload["source"] == "rising"
    assert payload["header"]["grade"] == "WATCH"
    assert payload["header"]["score"] == 3.5


def test_payload_transition_info():
    payload = build_monitor_payload(
        _stock(),
        snapshot_row=None,
        accel_ratio=None, recent_bar_value=None,
        ccnl=None, asking=None, investor=None,
        now=datetime(2026, 5, 11, 9, 32, 18),
        transition_info={
            "state": LeaderState.TRANSITION,
            "candidate_code": "012200",
            "candidate_turnover": 21.5,
        },
    )
    assert payload["transition"] is not None
    assert payload["transition"]["state"] == "transition"
    assert payload["transition"]["candidate_code"] == "012200"
    assert payload["transition"]["candidate_turnover"] == 21.5
    json.dumps(payload, ensure_ascii=False)  # enum 직렬화 통과


def test_payload_divergence_kind():
    """DivergenceState.bearish/bullish 두 bool 을 kind 문자열로 변환."""
    bearish = DivergenceState(
        bearish=True, bullish=False, price_change_pct=1.5, vp_5ma_delta=-5.0,
    )
    payload = build_monitor_payload(
        _stock(),
        snapshot_row=None,
        accel_ratio=None, recent_bar_value=None,
        ccnl=None, asking=None, investor=None,
        now=datetime(2026, 5, 11, 9, 32, 18),
        divergence=bearish,
    )
    assert payload["divergence"]["kind"] == "bearish"
    assert payload["divergence"]["price_change_pct"] == 1.5
    assert payload["divergence"]["vp_5ma_delta"] == -5.0

    neutral = DivergenceState(
        bearish=False, bullish=False, price_change_pct=0.1, vp_5ma_delta=0.5,
    )
    p2 = build_monitor_payload(
        _stock(),
        snapshot_row=None,
        accel_ratio=None, recent_bar_value=None,
        ccnl=None, asking=None, investor=None,
        now=datetime(2026, 5, 11, 9, 32, 18),
        divergence=neutral,
    )
    assert p2["divergence"]["kind"] == "neutral"


def test_payload_trigger_states():
    triggers = {
        "A1_stop_price": False,
        "A2_stop_bar_low": True,
        "E1_vp_below_100": False,
    }
    payload = build_monitor_payload(
        _stock(),
        snapshot_row=None,
        accel_ratio=None, recent_bar_value=None,
        ccnl=None, asking=None, investor=None,
        now=datetime(2026, 5, 11, 9, 32, 18),
        trigger_states=triggers,
    )
    assert payload["trigger_states"]["A2_stop_bar_low"] is True
    assert payload["trigger_states"]["A1_stop_price"] is False


@DEPRECATED_CARD_DISPLAY
def test_payload_trigger_lines_for_pwa():
    """PWA 가 trigger_states 보고 자체 렌더하지 않게 — payload.trigger_lines 가
    텔레그램 카드와 동일 텍스트 줄 list 로 제공돼야 함.
    """
    triggers = {
        "E1_vp_below_100": False,
        "E2_bearish_divergence": False,
        "E3_vol_drain": True,
        "E4_bearish_candle": False,
    }
    payload = build_monitor_payload(
        _stock(),
        snapshot_row=None,
        accel_ratio=None, recent_bar_value=None,
        ccnl=None, asking=None, investor=None,
        now=datetime(2026, 5, 11, 9, 32, 18),
        trigger_states=triggers,
        vp_5ma=95.0,
        vp_1ma=90.0,
        accel_ratio_1m=0.3,
    )
    lines = payload["trigger_lines"]
    # 헤더 + C1~C4 = 5줄 (감시 모드, C5 제외)
    assert len(lines) == 5
    assert "청산 시그널 (현재 시점)" in lines[0]
    # C1 ▢ + 현재 VP 수치 표시
    assert "▢" in lines[1] and "95" in lines[1]
    # C3 🚧 (발화) + 현재 1분 가속 0.3 표시
    assert "🚧" in lines[3] and "0.3배" in lines[3]


@DEPRECATED_CARD_DISPLAY
def test_payload_trigger_lines_holding_includes_c5():
    """보유 모드는 C5 (VI 발동) 포함, 5줄 + C5 = 6줄."""
    from src.scalping.exit.triggers import Holding

    triggers = {
        "E1_vp_below_100": False, "E2_bearish_divergence": False,
        "E3_vol_drain": False, "E4_bearish_candle": False,
        "E5_vi_failure": False,
    }
    holding = Holding(
        code="091340", entry_price=89000.0,
        entry_time=datetime(2026, 5, 11, 9, 2),
        time_stop_minutes=10,
    )
    payload = build_monitor_payload(
        _stock(),
        snapshot_row=None,
        accel_ratio=None, recent_bar_value=None,
        ccnl=None, asking=None, investor=None,
        now=datetime(2026, 5, 11, 9, 32, 18),
        trigger_states=triggers,
        holding=holding,
    )
    lines = payload["trigger_lines"]
    # 보유 모드 = "청산 시그널" (instantaneous 라벨 X)
    assert "청산 시그널" in lines[0] and "(현재 시점)" not in lines[0]
    # 6줄 (헤더 + C1~C5)
    assert len(lines) == 6
    assert "VI" in lines[-1]


def test_payload_trigger_lines_empty_when_states_none():
    payload = build_monitor_payload(
        _stock(),
        snapshot_row=None,
        accel_ratio=None, recent_bar_value=None,
        ccnl=None, asking=None, investor=None,
        now=datetime(2026, 5, 11, 9, 32, 18),
        trigger_states=None,
    )
    assert payload["trigger_lines"] == []


def test_payload_missing_snapshot_row():
    """snapshot_row=None 이어도 빈 dict 로 안전하게 빌드."""
    payload = build_monitor_payload(
        _stock(),
        snapshot_row=None,
        accel_ratio=None, recent_bar_value=None,
        ccnl=None, asking=None, investor=None,
        now=datetime(2026, 5, 11, 9, 32, 18),
    )
    assert payload["price"] == {}
    assert payload["volume"] == {}
    assert payload["vp"] == {}
    assert payload["asking"] == {}
    assert payload["holding"] is None
    json.dumps(payload, ensure_ascii=False)


def test_payload_includes_investor_block():
    """round 36: PWA payload 에 investor 키 노출 — 텔레그램 카드와 동일 데이터."""
    payload = build_monitor_payload(
        _stock(),
        snapshot_row=None,
        accel_ratio=None, recent_bar_value=None,
        ccnl=None, asking=None,
        investor={
            "foreign_net_buy": 18000,
            "institution_net_buy": -8000,
            "individual_net_buy": 5000,
            "program_net_buy": 30000,
            "foreign_net_buy_value": 1_500_000_000,
            "institution_net_buy_value": -800_000_000,
        },
        now=datetime(2026, 5, 11, 9, 32, 18),
    )
    inv = payload["investor"]
    assert inv is not None
    assert inv["foreign_value"] == 1_500_000_000
    assert inv["institution_value"] == -800_000_000
    assert inv["foreign_qty"] == 18000
    assert inv["institution_qty"] == -8000
    assert inv["individual_qty"] == 5000
    assert inv["program_qty"] == 30000
    json.dumps(payload, ensure_ascii=False)


def test_payload_investor_none_when_all_zero():
    """round 36: 수급 모두 0 이면 investor 키는 None — frontend 가 라인 자체 생략."""
    payload = build_monitor_payload(
        _stock(),
        snapshot_row=None,
        accel_ratio=None, recent_bar_value=None,
        ccnl=None, asking=None,
        investor={
            "foreign_net_buy": 0, "institution_net_buy": 0,
            "individual_net_buy": 0, "program_net_buy": 0,
            "foreign_net_buy_value": 0, "institution_net_buy_value": 0,
        },
        now=datetime(2026, 5, 11, 9, 32, 18),
    )
    assert payload["investor"] is None


def test_payload_investor_none_when_input_none():
    """investor=None 입력이면 payload["investor"] 도 None."""
    payload = build_monitor_payload(
        _stock(),
        snapshot_row=None,
        accel_ratio=None, recent_bar_value=None,
        ccnl=None, asking=None, investor=None,
        now=datetime(2026, 5, 11, 9, 32, 18),
    )
    assert payload["investor"] is None


def test_payload_includes_investor_delta_block():
    """round 36 후속: investor_delta 키 — 변화량 + elapsed_sec."""
    payload = build_monitor_payload(
        _stock(),
        snapshot_row=None,
        accel_ratio=None, recent_bar_value=None,
        ccnl=None, asking=None,
        investor={"foreign_net_buy_value": 1_500_000_000},
        investor_delta={
            "foreign_value": 300_000_000,
            "institution_value": -100_000_000,
            "program_qty": 2_500,
            "elapsed_sec": 47,
        },
        now=datetime(2026, 5, 11, 9, 32, 18),
    )
    d = payload["investor_delta"]
    assert d is not None
    assert d["foreign_value"] == 300_000_000
    assert d["institution_value"] == -100_000_000
    assert d["program_qty"] == 2_500
    assert d["elapsed_sec"] == 47
    json.dumps(payload, ensure_ascii=False)


def test_payload_investor_delta_none_when_all_zero():
    """Δ 모두 0 이면 payload["investor_delta"] 는 None (frontend 라인 생략)."""
    payload = build_monitor_payload(
        _stock(),
        snapshot_row=None,
        accel_ratio=None, recent_bar_value=None,
        ccnl=None, asking=None,
        investor={"foreign_net_buy_value": 1_500_000_000},
        investor_delta={
            "foreign_value": 0,
            "institution_value": 0,
            "program_qty": 0,
            "elapsed_sec": 60,
        },
        now=datetime(2026, 5, 11, 9, 32, 18),
    )
    assert payload["investor_delta"] is None


def test_payload_investor_delta_none_when_input_none():
    """investor_delta 인자 미지정 → payload 키 None."""
    payload = build_monitor_payload(
        _stock(),
        snapshot_row=None,
        accel_ratio=None, recent_bar_value=None,
        ccnl=None, asking=None, investor=None,
        now=datetime(2026, 5, 11, 9, 32, 18),
    )
    assert payload["investor_delta"] is None
