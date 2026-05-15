"""src.dashboard.render 단위 테스트."""
from __future__ import annotations

from datetime import datetime

from src.dashboard.render import render_monitor_message
from src.dashboard.state import LeaderState, MonitoredStock, Source


def _stock(code: str = "075180", name: str = "제룡전기",
           source: Source = Source.AUTO,
           themes: list[str] | None = None) -> MonitoredStock:
    """round 35: source 인자를 받아 해당 flag 켜기 (테스트 호환)."""
    return MonitoredStock(
        code=code, name=name,
        is_auto=(source == Source.AUTO),
        is_rising=(source == Source.RISING),
        is_manual=(source == Source.MANUAL),
        added_at=datetime(2026, 5, 11, 9, 0),
        themes=themes or ["전기/전선"],
    )


def test_render_basic_fields():
    snap = {
        "price": 91300, "daily_return": 30.0, "is_limit_up": True,
        "turnover": 18.3, "trading_value": 124_700_000_000,
    }
    msg = render_monitor_message(
        _stock(),
        snap,
        accel_ratio=4.2,
        recent_bar_value=5_000_000_000,
        ccnl={"ccnl_strength": 142.0, "buy_ratio": 60.0},
        asking={"bid_total_volume": 320_000, "ask_total_volume": 45_000, "bid_ask_ratio": 7.1},
        investor={"foreign_net_buy": 1800, "institution_net_buy": 4200, "program_net_buy": 2500},
        sparkline="▁▂▃▅▇█",
        now=datetime(2026, 5, 11, 9, 32, 18),
    )
    assert "제룡전기" in msg
    assert "075180" in msg
    assert "⭐ 자동" in msg
    assert "🔴상한가" in msg
    assert "+30.0%" in msg
    assert "18.3%" in msg
    assert "4.2배" in msg
    assert "체결강도" in msg
    assert "142" in msg
    # 외국인/기관/프로그램 라인은 round 22 에서 제거 (데이터 신뢰도 낮음).
    assert "외국인" not in msg
    # sparkline 라인은 사용자 요청으로 render 에서 제거 (2026-05-13).
    assert "▁▂▃▅▇█" not in msg
    # 시각 + 가격은 한 줄로 합쳐졌고 구분선(─) 제거됨.
    assert "─" not in msg
    assert "09:32:18  91,300원" in msg


def test_render_manual_source():
    msg = render_monitor_message(
        _stock(source=Source.MANUAL),
        snapshot_row=None,
        accel_ratio=None, recent_bar_value=None,
        ccnl=None, asking=None, investor=None,
        sparkline="",
        now=datetime(2026, 5, 11, 9, 0),
    )
    assert "🔵 수동" in msg


def test_render_with_grace_label():
    msg = render_monitor_message(
        _stock(),
        snapshot_row=None,
        accel_ratio=None, recent_bar_value=None,
        ccnl=None, asking=None, investor=None,
        sparkline="",
        now=datetime(2026, 5, 11, 9, 0),
        grace_remaining_seconds=272,
    )
    assert "GRACE" in msg
    assert "4:32" in msg


def test_render_acceleration_label():
    msg = render_monitor_message(
        _stock(),
        snapshot_row={"price": 1000, "daily_return": 5.0, "is_limit_up": False,
                      "turnover": 5.0, "trading_value": 1_000_000_000},
        accel_ratio=5.5, recent_bar_value=2_000_000_000,
        ccnl=None, asking=None, investor=None,
        sparkline="",
        now=datetime(2026, 5, 11, 9, 0),
    )
    assert "↑" in msg
    assert "가속" in msg


def test_render_exit_signal_label():
    msg = render_monitor_message(
        _stock(),
        snapshot_row={"price": 1000, "daily_return": -2.0, "is_limit_up": False,
                      "turnover": 5.0, "trading_value": 500_000_000},
        accel_ratio=0.4, recent_bar_value=200_000_000,
        ccnl=None, asking=None, investor=None,
        sparkline="",
        now=datetime(2026, 5, 11, 9, 0),
    )
    assert "↓" in msg
    assert "이탈" in msg


def test_render_handles_none_snapshot():
    """스냅샷 없는 경우에도 깨지지 않음."""
    msg = render_monitor_message(
        _stock(),
        snapshot_row=None,
        accel_ratio=None, recent_bar_value=None,
        ccnl=None, asking=None, investor=None,
        sparkline="",
        now=datetime(2026, 5, 11, 9, 0),
    )
    assert "—" in msg
    assert "제룡전기" in msg


def test_render_transition_candidate_in_header():
    """round 19: TRANSITION 시 a1 카드 헤더에 a2 부상 후보 한 줄 통합 표시."""
    msg = render_monitor_message(
        _stock(),
        snapshot_row=None,
        accel_ratio=None, recent_bar_value=None,
        ccnl=None, asking=None, investor=None,
        sparkline="",
        now=datetime(2026, 5, 11, 9, 0),
        transition_info={
            "state": LeaderState.TRANSITION,
            "candidate_code": "001440",
            "candidate_turnover": 11.0,
        },
    )
    assert "🔥 부상 후보 a2" in msg
    assert "001440" in msg
    assert "11.0%" in msg


def test_render_grace_candidate_in_header():
    msg = render_monitor_message(
        _stock(),
        snapshot_row=None,
        accel_ratio=None, recent_bar_value=None,
        ccnl=None, asking=None, investor=None,
        sparkline="",
        now=datetime(2026, 5, 11, 9, 0),
        transition_info={
            "state": LeaderState.GRACE,
            "candidate_code": "001440",
            "candidate_turnover": 22.5,
        },
    )
    assert "🔄 GRACE — a2" in msg
    assert "001440" in msg


def test_render_strong_rise_mark_5min():
    """round 19: 5분봉 강한 부상 임계(10배+ & 20억+) 도달 시 ⚡ 마크."""
    msg = render_monitor_message(
        _stock(),
        snapshot_row={"price": 1000, "daily_return": 5.0, "is_limit_up": False,
                      "turnover": 5.0, "trading_value": 1_000_000_000},
        accel_ratio=12.0, recent_bar_value=25_000_000_000,
        ccnl=None, asking=None, investor=None,
        sparkline="",
        now=datetime(2026, 5, 11, 9, 0),
    )
    assert "🟢⚡" in msg
    assert "강한 부상" in msg


def test_render_one_min_exit_mark():
    msg = render_monitor_message(
        _stock(),
        snapshot_row={"price": 1000, "daily_return": -2.0, "is_limit_up": False,
                      "turnover": 5.0, "trading_value": 500_000_000},
        accel_ratio=1.0, recent_bar_value=2_000_000_000,
        accel_ratio_1m=0.2, last_bar_value=100_000_000,
        ccnl=None, asking=None, investor=None,
        sparkline="",
        now=datetime(2026, 5, 11, 9, 0),
    )
    assert "🔴⚠" in msg
    assert "1분봉 급감" in msg


def test_render_holding_mode_basic():
    """round 22: 보유 모드 카드 — [보유] 헤더 + 합쳐진 시간/가격 라인 + 청산 시그널."""
    from src.jongbae.exit_triggers import Holding
    from src.jongbae.divergence import DivergenceState

    entry = datetime(2026, 5, 14, 10, 3, 45)
    now = datetime(2026, 5, 14, 10, 23, 45)
    holding = Holding(
        code="091340", entry_price=91300, entry_time=entry,
        high_since_entry=92800,
    )
    triggers = {
        "A1_stop_price": False, "C1_vp_below_100": False,
        "C2_bearish_divergence": False, "C3_vol_drain": False,
        "C4_bearish_candle": False, "C5_vi_failure": False,
    }
    div = DivergenceState(bearish=False, bullish=False,
                         price_change_pct=0.8, vp_5ma_delta=-3.0)
    msg = render_monitor_message(
        _stock(code="091340", name="대한광통신", source=Source.MANUAL),
        snapshot_row={"price": 92500, "prev_close": 70200, "daily_return": 31.8,
                      "is_limit_up": True, "turnover": 19.0,
                      "trading_value": 135_000_000_000, "rank": 1},
        accel_ratio=4.8, recent_bar_value=4_500_000_000,
        ccnl={"ccnl_strength": 138.0, "buy_ratio": 58.0},
        asking=None, investor=None, sparkline="", now=now,
        accel_ratio_1m=3.5, last_bar_value=900_000_000,
        vp_5ma=135.0, vp_1ma=123.0,
        holding=holding, trigger_states=triggers, divergence=div,
    )
    # 헤더 — multi-flag (round 35): is_manual + holding → "[💎 보유 / 🔵 수동]"
    assert "💎 보유" in msg
    assert "🔵 수동" in msg
    assert "대한광통신 (091340)" in msg
    # 합쳐진 시간/가격 라인 — 매수가 + 손익 + 경과 초
    assert "(+1,200초)" in msg
    assert "92,500원" in msg
    assert "91,300" in msg
    assert "(+1.3%)" in msg  # 손익률
    # 체결강도 5MA + 1MA
    assert "5MA 135" in msg
    assert "1MA 123" in msg
    # 청산 시그널 섹션
    assert "─ 청산 시그널 ─" in msg
    assert "▢ 체결강도 5MA 100 하향 (현재 5MA 135 / 1MA 123)" in msg
    assert "▢ Bearish Divergence" in msg
    assert "가격 +0.80%" in msg
    assert "체결강도 -3" in msg
    # round 33: 보유 모드 C3 라벨에 "2분 지속" 명시.
    assert "▢ 자금 고갈 (1분 가속 < 0.5, 2분 지속) — 현재 3.5배" in msg
    assert "▢ 윗꼬리 50%↑ 음봉 (1분봉 기준)" in msg
    assert "▢ VI 발동 후 5분 내 재상승 실패" in msg
    # +29% 매도가 라인은 보유 모드에서 미표시
    assert "+29% 매도가" not in msg


def test_render_holding_mode_with_fired_triggers():
    """발화된 트리거는 🚧, 미발화는 ▢."""
    from src.jongbae.exit_triggers import Holding

    entry = datetime(2026, 5, 14, 10, 3, 45)
    now = datetime(2026, 5, 14, 10, 23, 45)
    holding = Holding(code="091340", entry_price=91300, entry_time=entry,
                      high_since_entry=92800)
    triggers = {
        "C1_vp_below_100": True,    # 발화
        "C2_bearish_divergence": True,  # 발화
        "C3_vol_drain": False,
        "C4_bearish_candle": False,
        "C5_vi_failure": False,
    }
    msg = render_monitor_message(
        _stock(code="091340", name="대한광통신", source=Source.MANUAL),
        snapshot_row={"price": 90000, "prev_close": 91000, "daily_return": -1.1,
                      "is_limit_up": False, "turnover": 8.0,
                      "trading_value": 50_000_000_000, "rank": 5},
        accel_ratio=0.4, recent_bar_value=500_000_000,
        ccnl={"ccnl_strength": 85.0, "buy_ratio": 45.0},
        asking=None, investor=None, sparkline="", now=now,
        accel_ratio_1m=0.3, last_bar_value=80_000_000,
        vp_5ma=92.0, vp_1ma=85.0,
        holding=holding, trigger_states=triggers,
    )
    assert "🚧 체결강도 5MA 100 하향" in msg
    assert "🚧 Bearish Divergence" in msg
    assert "▢ 자금 고갈" in msg


def test_render_strength_line_always_shown_when_ccnl_missing():
    """round 33: ccnl=None 이어도 체결강도 라인 항상 표시 (— placeholder)."""
    msg = render_monitor_message(
        _stock(),
        snapshot_row={"price": 1000, "daily_return": 5.0, "is_limit_up": False,
                      "turnover": 5.0, "trading_value": 1_000_000_000},
        accel_ratio=2.0, recent_bar_value=1_000_000_000,
        ccnl=None, asking=None, investor=None,
        sparkline="",
        now=datetime(2026, 5, 11, 9, 0),
    )
    assert "체결강도" in msg
    assert "데이터 없음" in msg


def test_render_strength_line_with_ma_when_ccnl_nan():
    """ccnl 응답에 cttr 가 NaN 이어도 5MA/1MA 가 있으면 같이 표시."""
    msg = render_monitor_message(
        _stock(),
        snapshot_row={"price": 1000, "daily_return": 5.0, "is_limit_up": False,
                      "turnover": 5.0, "trading_value": 1_000_000_000},
        accel_ratio=2.0, recent_bar_value=1_000_000_000,
        ccnl={"ccnl_strength": float("nan"), "buy_ratio": float("nan")},
        asking=None, investor=None,
        sparkline="",
        now=datetime(2026, 5, 11, 9, 0),
        vp_5ma=115.0, vp_1ma=120.0,
    )
    assert "체결강도: —" in msg
    assert "5MA 115" in msg
    assert "1MA 120" in msg


def test_render_watch_mode_c3_label_no_sustain():
    """감시 모드는 C3 instantaneous — '2분 지속' 표기 없음."""
    msg = render_monitor_message(
        _stock(),
        snapshot_row={"price": 1000, "daily_return": 5.0, "is_limit_up": False,
                      "turnover": 5.0, "trading_value": 1_000_000_000},
        accel_ratio=2.0, recent_bar_value=1_000_000_000,
        ccnl=None, asking=None, investor=None,
        sparkline="",
        now=datetime(2026, 5, 11, 9, 0),
        accel_ratio_1m=0.3, last_bar_value=100_000_000,
        trigger_states={
            "C1_vp_below_100": False, "C2_bearish_divergence": False,
            "C3_vol_drain": True, "C4_bearish_candle": False,
            "C5_vi_failure": False,
        },
    )
    assert "🚧 자금 고갈 (1분 가속 < 0.5)" in msg
    assert "2분 지속" not in msg


def test_render_buy_grade_label_shows_on_any_source():
    """round 33: buy_grade 가 set 되어 있으면 AUTO/MANUAL/HOLD 모두 라벨 표시."""
    from src.dashboard.state import MonitoredStock
    stock = MonitoredStock(
        code="091340", name="대한광통신", is_auto=True,
        added_at=datetime(2026, 5, 11, 9, 0),
        themes=["AI"],
        buy_score=6.5, buy_grade="STRONG",
        buy_reasons=["+2 VP", "+2 양봉"],
    )
    msg = render_monitor_message(
        stock,
        snapshot_row={"price": 91300, "daily_return": 18.0, "is_limit_up": False,
                      "turnover": 12.0, "trading_value": 80_000_000_000},
        accel_ratio=3.0, recent_bar_value=2_000_000_000,
        ccnl={"ccnl_strength": 135.0}, asking=None, investor=None,
        sparkline="", now=datetime(2026, 5, 11, 9, 0),
    )
    assert "STRONG" in msg
    assert "+6.5점" in msg


def test_render_themes_slash_join():
    msg = render_monitor_message(
        _stock(themes=["전기/전선", "원자력", "AI데이터센터"]),
        snapshot_row=None,
        accel_ratio=None, recent_bar_value=None,
        ccnl=None, asking=None, investor=None,
        sparkline="",
        now=datetime(2026, 5, 11, 9, 0),
    )
    assert "전기/전선 / 원자력 / AI데이터센터" in msg
