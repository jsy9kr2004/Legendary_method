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
        investor={
            "foreign_net_buy": 18000,
            "institution_net_buy": -8000,
            "individual_net_buy": -10000,
            "program_net_buy": 30000,
            "foreign_net_buy_value": 1_500_000_000,
            "institution_net_buy_value": -800_000_000,
        },
        sparkline="▁▂▃▅▇█",
        now=datetime(2026, 5, 11, 9, 32, 18),
    )
    assert "제룡전기" in msg
    assert "075180" in msg
    assert "⭐ 자동" in msg
    assert "🔴상한가" in msg
    # _fmt_pct 자릿수 통일 (2026-05-18) — 1자리 → 2자리 (report.fmt_pct 와 동일).
    assert "+30.00%" in msg
    assert "18.30%" in msg
    assert "4.2배" in msg
    assert "체결강도" in msg
    assert "142" in msg
    # snap 에 rank / turnover_rank 안 넣어서 "(N위)" suffix 없어야 한다.
    assert "거래대금: 1,247억  회전율: +18.30%" in msg
    assert "(위)" not in msg
    # 외인/기관/프로그램 수급 라인 round 36 부활 — round 22 에서 제거됐던 라인.
    # Buy.Score 점수 합산은 round 29 ritual 통과 전엔 X, 카드 표시만.
    assert "수급:" in msg
    assert "외인" in msg
    assert "기관" in msg
    assert "프로그램" in msg
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
    assert "11.00%" in msg  # _fmt_pct 2자리 (2026-05-18 정정)


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


def test_render_skips_investor_line_when_all_zero():
    """round 36: 수급 모두 0 이면 라인 자체 생략 (시각 노이즈 제거)."""
    msg = render_monitor_message(
        _stock(),
        snapshot_row={"price": 1000, "daily_return": 5.0, "is_limit_up": False,
                      "turnover": 5.0, "trading_value": 500_000_000},
        accel_ratio=None, recent_bar_value=None,
        ccnl=None, asking=None,
        investor={
            "foreign_net_buy": 0, "institution_net_buy": 0,
            "individual_net_buy": 0, "program_net_buy": 0,
            "foreign_net_buy_value": 0, "institution_net_buy_value": 0,
        },
        sparkline="",
        now=datetime(2026, 5, 11, 9, 0),
    )
    assert "수급:" not in msg


def test_render_investor_line_signs_and_units():
    """round 36: 외인/기관 금액(억) 부호 명시, 프로그램 수량(만주) 부호 명시."""
    msg = render_monitor_message(
        _stock(),
        snapshot_row={"price": 1000, "daily_return": 5.0, "is_limit_up": False,
                      "turnover": 5.0, "trading_value": 500_000_000},
        accel_ratio=None, recent_bar_value=None,
        ccnl=None, asking=None,
        investor={
            "foreign_net_buy": 18000,
            "institution_net_buy": -8000,
            "individual_net_buy": 0,
            "program_net_buy": 30000,
            "foreign_net_buy_value": 1_500_000_000,
            "institution_net_buy_value": -800_000_000,
        },
        sparkline="",
        now=datetime(2026, 5, 11, 9, 0),
    )
    assert "수급:" in msg
    assert "외인 +15억" in msg
    assert "기관 -8억" in msg
    assert "프로그램 +3만주" in msg


def test_render_investor_delta_inline_with_signup():
    """round 36 후속: 누계 라인 안에 괄호 Δ — 한 줄 통합."""
    msg = render_monitor_message(
        _stock(),
        snapshot_row={"price": 1000, "daily_return": 5.0, "is_limit_up": False,
                      "turnover": 5.0, "trading_value": 500_000_000},
        accel_ratio=None, recent_bar_value=None,
        ccnl=None, asking=None,
        investor={
            "foreign_net_buy_value": 1_500_000_000,
            "institution_net_buy_value": -800_000_000,
            "program_net_buy": 30000,
        },
        investor_delta={
            "foreign_value": 300_000_000,
            "institution_value": -500_000_000,
            "program_qty": 2_500,
            "elapsed_sec": 47,
        },
        sparkline="",
        now=datetime(2026, 5, 11, 9, 0),
    )
    # 헤더 옆 elapsed
    assert "수급(Δ47s):" in msg
    # 각 항목: 누계 (Δ변화)
    assert "외인 +15억 (+3억)" in msg
    assert "기관 -8억 (-5억)" in msg
    assert "프로그램 +3만주 (+2,500주)" in msg
    # 별도 Δ 라인 X — 한 줄 통합 확인
    lines = msg.split("\n")
    assert sum(1 for l in lines if "수급" in l) == 1


def test_render_investor_delta_minutes_format():
    """경과 시간 분 단위 — 헤더 (Δ2m13s)."""
    msg = render_monitor_message(
        _stock(),
        snapshot_row={"price": 1000, "daily_return": 5.0, "is_limit_up": False,
                      "turnover": 5.0, "trading_value": 500_000_000},
        accel_ratio=None, recent_bar_value=None,
        ccnl=None, asking=None,
        investor={"foreign_net_buy_value": 1_500_000_000},
        investor_delta={
            "foreign_value": 100_000_000,
            "institution_value": 0,
            "program_qty": 0,
            "elapsed_sec": 133,
        },
        sparkline="",
        now=datetime(2026, 5, 11, 9, 0),
    )
    assert "(Δ2m13s)" in msg
    # 외인은 Δ +1억 표시, 기관/프로그램은 변화 없으니 괄호 생략
    assert "외인 +15억 (+1억)" in msg
    # 기관 누계 0 인데 program도 0 → 그 항목 자체 표시 X (수급 라인 조건 통과는
    # 외인만 있으면 됨). 다만 form: "기관 0" 식이라 괄호 없는지 확인.
    assert "기관 0 (" not in msg
    assert "프로그램 0 (" not in msg


def test_render_skips_delta_line_when_all_zero():
    """Δ 모두 0 이면 Δ 라인 자체 생략 (시각 노이즈)."""
    msg = render_monitor_message(
        _stock(),
        snapshot_row={"price": 1000, "daily_return": 5.0, "is_limit_up": False,
                      "turnover": 5.0, "trading_value": 500_000_000},
        accel_ratio=None, recent_bar_value=None,
        ccnl=None, asking=None,
        investor={"foreign_net_buy_value": 1_500_000_000},
        investor_delta={
            "foreign_value": 0,
            "institution_value": 0,
            "program_qty": 0,
            "elapsed_sec": 60,
        },
        sparkline="",
        now=datetime(2026, 5, 11, 9, 0),
    )
    assert "수급:" in msg  # 수급 라인은 있음
    assert "Δ" not in msg  # Δ 라인 없음


def test_render_no_delta_line_without_delta_arg():
    """investor_delta=None 이어도 수급 라인은 표시 (호환성)."""
    msg = render_monitor_message(
        _stock(),
        snapshot_row={"price": 1000, "daily_return": 5.0, "is_limit_up": False,
                      "turnover": 5.0, "trading_value": 500_000_000},
        accel_ratio=None, recent_bar_value=None,
        ccnl=None, asking=None,
        investor={"foreign_net_buy_value": 1_500_000_000},
        # investor_delta 인자 자체 생략
        sparkline="",
        now=datetime(2026, 5, 11, 9, 0),
    )
    assert "수급:" in msg
    assert "Δ" not in msg


def test_render_holding_mode_basic():
    """round 22: 보유 모드 카드 — [보유] 헤더 + 합쳐진 시간/가격 라인 + 청산 시그널."""
    from src.scalping.exit.triggers import Holding
    from src.scalping.score.divergence import DivergenceState

    entry = datetime(2026, 5, 14, 10, 3, 45)
    now = datetime(2026, 5, 14, 10, 23, 45)
    holding = Holding(
        code="091340", entry_price=91300, entry_time=entry,
        high_since_entry=92800,
    )
    triggers = {
        "A1_stop_price": False, "E1_vp_below_100": False,
        "E2_bearish_divergence": False, "E3_vol_drain": False,
        "E4_bearish_candle": False, "E5_vi_failure": False,
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
    assert "(+1.31%)" in msg  # 손익률 — _fmt_pct 2자리 (2026-05-18 정정)
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
    from src.scalping.exit.triggers import Holding

    entry = datetime(2026, 5, 14, 10, 3, 45)
    now = datetime(2026, 5, 14, 10, 23, 45)
    holding = Holding(code="091340", entry_price=91300, entry_time=entry,
                      high_since_entry=92800)
    triggers = {
        "E1_vp_below_100": True,    # 발화
        "E2_bearish_divergence": True,  # 발화
        "E3_vol_drain": False,
        "E4_bearish_candle": False,
        "E5_vi_failure": False,
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
            "E1_vp_below_100": False, "E2_bearish_divergence": False,
            "E3_vol_drain": True, "E4_bearish_candle": False,
            "E5_vi_failure": False,
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


# ── rank / turnover_rank 표시 (2026-05-18) ──────────────────────────────────
# 사용자 보고: "거래대금 순위가 카드에 표시되지만 KIS HTS 와 다르다 + 회전율
# 순위는 안 보인다". 거래대금 rank 는 이제 KIS 원본 (data_rank), 회전율 옆에는
# turnover_rank 표시.

def test_render_shows_kis_data_rank_and_turnover_rank():
    """snap_row 의 rank (KIS 원본) 가 거래대금 옆에, turnover_rank 가 회전율
    옆에 표시.
    """
    snap = {
        "rank": 4,                   # KIS 원본 거래대금 순위 (ETF 2개 빠진 사이)
        "turnover_rank": 1,          # master 필터 통과 종목 중 회전율 1위
        "price": 91300, "daily_return": 30.0, "is_limit_up": True,
        "turnover": 18.3, "trading_value": 124_700_000_000,
    }
    msg = render_monitor_message(
        _stock(),
        snap,
        accel_ratio=None, recent_bar_value=None,
        ccnl=None, asking=None, investor=None,
        sparkline="", now=datetime(2026, 5, 11, 9, 32, 18),
    )
    # 거래대금: ...억 (4위)  회전율: +18.30% (1위)
    assert "거래대금: 1,247억 (4위)  회전율: +18.30% (1위)" in msg, (
        f"rank/turnover_rank 표시 누락: {msg}"
    )


def test_render_omits_turnover_rank_when_missing():
    """50위 밖 종목(_synthesize_snap_row 가 만드는 합성 row) 처럼 rank /
    turnover_rank 둘 다 None 이면 (위) suffix 자체 미출력.
    """
    snap = {
        "rank": None, "turnover_rank": None,
        "price": 5000, "daily_return": 4.17, "is_limit_up": False,
        "turnover": 0.1, "trading_value": 100_000_000,
    }
    msg = render_monitor_message(
        _stock(),
        snap,
        accel_ratio=None, recent_bar_value=None,
        ccnl=None, asking=None, investor=None,
        sparkline="", now=datetime(2026, 5, 11, 9, 32, 18),
    )
    assert "회전율: +0.10%" in msg
    assert "(위)" not in msg, f"빈 rank 인데 (위) 출력됨: {msg}"
