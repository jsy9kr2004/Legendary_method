"""모니터링 카드 샘플을 텔레그램으로 발송.

상황별 카드 5종을 render_monitor_message 로 생성해 send_message_single 로 발송.
1) ⭐ 주도주 NORMAL (강한 부상 + STRONG 등급)
2) ⭐ 주도주 TRANSITION (a1 카드 안에 부상 후보 a2 통합 표시)
3) ⚡ 부상 후보 RISING (WATCH 등급, /add 안내)
4) 🔵 수동 모니터링 (감시 모드)
5) 보유 모드 (매수가/손익/R15 청산 시그널/Bearish Divergence)

데모용 정적 fixture — 실제 시세 호출 없음.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from src.config import load_settings
from src.dashboard.render import render_monitor_message
from src.dashboard.state import LeaderState, MonitoredStock, Source
from src.jongbae.divergence import DivergenceState
from src.jongbae.exit_triggers import Holding
from src.notify.telegram import send_message_single


def _render_auto_normal(now: datetime) -> str:
    m = MonitoredStock(
        code="075180", name="제룡전기", is_auto=True, added_at=now,
        themes=["전기/전선", "원자력", "AI데이터센터"],
        buy_score=6.5, buy_grade="STRONG",
        buy_reasons=["회전율 14.2% (섹터 1위)", "VP 5MA 135", "1분 가속 6.8배"],
    )
    snap = {
        "price": 90_300, "prev_close": 70_230, "daily_return": 28.6,
        "is_limit_up": False, "turnover": 14.2,
        "trading_value": 4_300_0000_0000, "rank": 3,
    }
    ccnl = {"ccnl_strength": 142.0, "buy_ratio": 58.7}
    asking = {
        "bid_total_volume": 312_400, "ask_total_volume": 95_200,
        "bid_ask_ratio": 3.28,
        "bid1_price": 90_300, "bid1_volume": 4_200,
        "ask1_price": 90_400, "ask1_volume": 1_800,
    }
    # 모든 시그널 off — STRONG 등급의 깨끗한 진입 후보 예시.
    trigger_states = {
        "C1_vp_below_100": False, "C2_bearish_divergence": False,
        "C3_vol_drain": False, "C4_bearish_candle": False,
        "C5_vi_failure": False,
    }
    # round 36: 수급 라인 — STRONG 종목은 외인/기관 동반 매수 + 프로그램도 양수.
    investor = {
        "foreign_net_buy": 95_000,
        "institution_net_buy": 32_000,
        "individual_net_buy": -127_000,
        "program_net_buy": 58_000,
        "foreign_net_buy_value": 8_500_000_000,
        "institution_net_buy_value": 2_900_000_000,
    }
    # round 36 후속: Δ — 방금 막 갱신 (47s 전).
    investor_delta = {
        "foreign_value": 300_000_000,
        "institution_value": 120_000_000,
        "program_qty": 2_500,
        "elapsed_sec": 47,
    }
    return render_monitor_message(
        monitored=m, snapshot_row=snap,
        accel_ratio=12.4, recent_bar_value=3_800_000_000,
        ccnl=ccnl, asking=asking, investor=investor,
        sparkline="▁▂▃▄▅▆▇█", now=now,
        accel_ratio_1m=6.8, last_bar_value=820_000_000,
        vp_1ma=138.0, vp_5ma=135.0,
        trigger_states=trigger_states,
        investor_delta=investor_delta,
    )


def _render_auto_transition(now: datetime) -> str:
    m = MonitoredStock(
        code="010120", name="LS ELECTRIC", is_auto=True, added_at=now,
        themes=["전기/전선", "원전"],
        buy_score=3.0, buy_grade="WATCH",
        buy_reasons=["회전율 5.8%", "VP 5MA 108", "5분 가속 2.1배"],
    )
    snap = {
        "price": 132_500, "prev_close": 110_000, "daily_return": 20.4,
        "is_limit_up": False, "turnover": 5.8,
        "trading_value": 2_100_0000_0000, "rank": 8,
    }
    ccnl = {"ccnl_strength": 108.0, "buy_ratio": 52.1}
    asking = {
        "bid_total_volume": 88_300, "ask_total_volume": 71_500,
        "bid_ask_ratio": 1.23,
        "bid1_price": 132_500, "bid1_volume": 1_100,
        "ask1_price": 132_600, "ask1_volume": 1_400,
    }
    transition_info = {
        "state": LeaderState.TRANSITION,
        "candidate_code": "001440",
        "candidate_turnover": 11.3,
    }
    # a1 카드 — 시그널 1개만 켬 (C2 Bearish): 모멘텀이 a2 로 옮겨가는 신호.
    trigger_states = {
        "C1_vp_below_100": False, "C2_bearish_divergence": True,
        "C3_vol_drain": False, "C4_bearish_candle": False,
        "C5_vi_failure": False,
    }
    # round 36: TRANSITION — 모멘텀이 a2 로 옮겨가는 중이라 기관이 먼저 빠짐.
    investor = {
        "foreign_net_buy": 12_000,
        "institution_net_buy": -28_000,
        "individual_net_buy": 16_000,
        "program_net_buy": -8_000,
        "foreign_net_buy_value": 1_600_000_000,
        "institution_net_buy_value": -3_700_000_000,
    }
    # round 36 후속: Δ — 3분쯤 전 마지막 갱신, 기관 매도 가속.
    investor_delta = {
        "foreign_value": 50_000_000,
        "institution_value": -500_000_000,
        "program_qty": -1_800,
        "elapsed_sec": 192,
    }
    return render_monitor_message(
        monitored=m, snapshot_row=snap,
        accel_ratio=2.1, recent_bar_value=620_000_000,
        ccnl=ccnl, asking=asking, investor=investor,
        sparkline="▃▄▅▅▆▆▅▄", now=now,
        accel_ratio_1m=1.4, last_bar_value=130_000_000,
        transition_info=transition_info,
        vp_1ma=106.0, vp_5ma=108.0,
        trigger_states=trigger_states,
        investor_delta=investor_delta,
    )


def _render_rising(now: datetime) -> str:
    m = MonitoredStock(
        code="001440", name="대한전선", is_rising=True, added_at=now,
        themes=["전기/전선", "구리"],
        buy_score=3.2, buy_grade="WATCH",
        buy_reasons=["회전율 11.3% 급증", "1분 가속 4.5배", "VP 5MA 121"],
    )
    snap = {
        "price": 4_410, "prev_close": 3_500, "daily_return": 26.0,
        "is_limit_up": False, "turnover": 11.3,
        "trading_value": 1_580_0000_0000, "rank": 12,
    }
    ccnl = {"ccnl_strength": 121.0, "buy_ratio": 56.4}
    asking = {
        "bid_total_volume": 1_240_000, "ask_total_volume": 690_000,
        "bid_ask_ratio": 1.80,
        "bid1_price": 4_410, "bid1_volume": 38_200,
        "ask1_price": 4_415, "ask1_volume": 21_400,
    }
    # 부상 후보 — 시그널 모두 깨끗. /add 결정 가능 상태.
    trigger_states = {
        "C1_vp_below_100": False, "C2_bearish_divergence": False,
        "C3_vol_drain": False, "C4_bearish_candle": False,
        "C5_vi_failure": False,
    }
    # round 36: 부상 후보 — 외인 막 들어오기 시작, 프로그램 강한 양수 (저가주라 수량 큼).
    investor = {
        "foreign_net_buy": 250_000,
        "institution_net_buy": 9_000,
        "individual_net_buy": -274_000,
        "program_net_buy": 145_000,
        "foreign_net_buy_value": 1_100_000_000,
        "institution_net_buy_value": 400_000_000,
    }
    # round 36 후속: Δ — 1분 전 새로 갱신, 외인/프로그램 모두 추가 진입.
    investor_delta = {
        "foreign_value": 250_000_000,
        "institution_value": 80_000_000,
        "program_qty": 32_000,
        "elapsed_sec": 68,
    }
    return render_monitor_message(
        monitored=m, snapshot_row=snap,
        accel_ratio=5.2, recent_bar_value=1_200_000_000,
        ccnl=ccnl, asking=asking, investor=investor,
        sparkline="▁▁▂▃▄▆▇█", now=now,
        accel_ratio_1m=4.5, last_bar_value=540_000_000,
        vp_1ma=124.0, vp_5ma=121.0,
        trigger_states=trigger_states,
        investor_delta=investor_delta,
    )


def _render_manual(now: datetime) -> str:
    m = MonitoredStock(
        code="034730", name="SK스퀘어", is_manual=True, added_at=now,
        themes=["반도체", "지주회사"],
    )
    snap = {
        "price": 72_400, "prev_close": 68_000, "daily_return": 6.5,
        "is_limit_up": False, "turnover": 1.8,
        "trading_value": 1_440_0000_0000, "rank": 22,
    }
    ccnl = {"ccnl_strength": 96.0, "buy_ratio": 49.1}
    asking = {
        "bid_total_volume": 41_200, "ask_total_volume": 54_300,
        "bid_ask_ratio": 0.76,
        "bid1_price": 72_400, "bid1_volume": 820,
        "ask1_price": 72_500, "ask1_volume": 1_050,
    }
    # 수동 추적 — 시그널 3개 켬 (C1 VP<100, C3 자금고갈, 모멘텀 식어가는 케이스).
    # "이 종목 사면 안 됨" 을 카드에서 바로 인지 가능한 예시.
    trigger_states = {
        "C1_vp_below_100": True, "C2_bearish_divergence": False,
        "C3_vol_drain": True, "C4_bearish_candle": False,
        "C5_vi_failure": False,
    }
    # round 36: 모멘텀 식어가는 종목 — 외인/기관/프로그램 동반 매도.
    investor = {
        "foreign_net_buy": -45_000,
        "institution_net_buy": -18_000,
        "individual_net_buy": 65_000,
        "program_net_buy": -16_000,
        "foreign_net_buy_value": -3_200_000_000,
        "institution_net_buy_value": -1_300_000_000,
    }
    # round 36 후속: Δ — 약 1분 전 매도 가속.
    investor_delta = {
        "foreign_value": -350_000_000,
        "institution_value": -120_000_000,
        "program_qty": -5_500,
        "elapsed_sec": 73,
    }
    return render_monitor_message(
        monitored=m, snapshot_row=snap,
        accel_ratio=0.9, recent_bar_value=180_000_000,
        ccnl=ccnl, asking=asking, investor=investor,
        sparkline="▄▅▆▆▅▄▃▃", now=now,
        accel_ratio_1m=0.7, last_bar_value=42_000_000,
        vp_1ma=92.0, vp_5ma=96.0,
        trigger_states=trigger_states,
        investor_delta=investor_delta,
    )


def _render_holding(now: datetime) -> str:
    m = MonitoredStock(
        code="075180", name="제룡전기", is_manual=True, added_at=now,
        themes=["전기/전선", "원자력", "AI데이터센터"],
        buy_score=2.5, buy_grade="WATCH",
        buy_reasons=["보유 중 — 매수가 91,300"],
    )
    snap = {
        "price": 90_100, "prev_close": 70_230, "daily_return": 28.3,
        "is_limit_up": False, "turnover": 14.0,
        "trading_value": 4_200_0000_0000, "rank": 3,
    }
    ccnl = {"ccnl_strength": 95.0, "buy_ratio": 48.6}
    asking = {
        "bid_total_volume": 142_000, "ask_total_volume": 248_000,
        "bid_ask_ratio": 0.57,
        "bid1_price": 90_100, "bid1_volume": 1_900,
        "ask1_price": 90_200, "ask1_volume": 3_400,
    }
    holding = Holding(
        code="075180",
        entry_price=91_300.0,
        entry_time=now - timedelta(minutes=12),
        entry_bar_low=90_800.0,
        high_since_entry=91_500.0,
    )
    trigger_states = {
        "C1_vp_below_100": True,        # VP 5MA 95 < 100
        "C2_bearish_divergence": True,   # 가격↑ / VP↓
        "C3_vol_drain": False,
        "C4_bearish_candle": False,
        "C5_vi_failure": False,
    }
    divergence = DivergenceState(
        bearish=True, bullish=False,
        price_change_pct=0.42, vp_5ma_delta=-18.0,
    )
    # round 36: 보유 중인데 수급 꺾이는 중 — 기관 매도 시작, 외인 약하게 유지.
    # Bearish Divergence 와 정합 (가격↑ 인데 자금 흐름 약화).
    investor = {
        "foreign_net_buy": 8_000,
        "institution_net_buy": -22_000,
        "individual_net_buy": 14_000,
        "program_net_buy": -16_000,
        "foreign_net_buy_value": 700_000_000,
        "institution_net_buy_value": -1_800_000_000,
    }
    # round 36 후속: Δ — 약 2분 45초 전 갱신, 기관 매도 가속 / 외인은 미온.
    investor_delta = {
        "foreign_value": -80_000_000,
        "institution_value": -300_000_000,
        "program_qty": -10_000,
        "elapsed_sec": 165,
    }
    return render_monitor_message(
        monitored=m, snapshot_row=snap,
        accel_ratio=1.3, recent_bar_value=320_000_000,
        ccnl=ccnl, asking=asking, investor=investor,
        sparkline="▆▆▅▅▄▄▃▂", now=now,
        accel_ratio_1m=0.8, last_bar_value=68_000_000,
        vp_1ma=92.0, vp_5ma=95.0,
        holding=holding,
        trigger_states=trigger_states,
        divergence=divergence,
        investor_delta=investor_delta,
    )


def main() -> int:
    s = load_settings()
    if not s.telegram_bot_token or not s.telegram_chat_id:
        print("ERROR: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 미설정")
        return 1

    now = datetime(2026, 5, 15, 9, 42, 18)
    header = (
        "🧪 [샘플] 모니터링 카드 5종 — 실시간 시세 아님\n"
        "1) ⭐ 주도주 NORMAL  2) ⭐ 주도주 TRANSITION  "
        "3) ⚡ 부상 후보  4) 🔵 수동  5) 보유 모드"
    )
    send_message_single(s.telegram_bot_token, s.telegram_chat_id, header)

    cards = [
        ("1. ⭐ 주도주 NORMAL (STRONG)", _render_auto_normal(now)),
        ("2. ⭐ 주도주 TRANSITION (a2 부상 후보 통합)", _render_auto_transition(now)),
        ("3. ⚡ 부상 후보 (RISING)", _render_rising(now)),
        ("4. 🔵 수동 모니터링", _render_manual(now)),
        ("5. 보유 모드 (R15 청산 시그널)", _render_holding(now)),
    ]
    for label, body in cards:
        text = f"━━ {label} ━━\n{body}"
        r = send_message_single(s.telegram_bot_token, s.telegram_chat_id, text)
        ok = bool(r and r.get("ok"))
        print(f"[{ok}] {label} ({len(text)}자)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
