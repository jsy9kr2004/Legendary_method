"""실시간 모니터링 메시지 렌더링 (M6).

종목별 메시지 1개를 만들어 1~2초마다 editMessageText 로 갱신.
관련 보조지표 (분봉 가속배율 / 체결강도 / 호가잔량 / 외국인 순매수)를
한눈에 보여주는 포맷.

Pure 함수 — fetch 결과 dict 받아 마크다운 텍스트 반환.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from src.dashboard.state import LeaderState, MonitoredStock, Source
from src.scalping.score.accel import (
    is_exit_signal,
    is_one_min_exit,
    is_one_min_rise,
    is_strong_rise,
)


def _fmt_pct(v: float | None) -> str:
    """등락률/회전율 형식 — report.fmt_pct 와 동일 (자릿수/부호 정책 일치).

    None/NaN 만 dashboard 관행대로 "—" 로 표시 (report 는 "N/A"). 사용자가
    14:50 결정 레포트와 모니터링 카드를 한 채팅에서 비교할 때 같은 값이
    같은 모양으로 보이도록 자릿수 통일 (2026-05-18 정정).
    """
    if v is None or v != v:
        return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.2f}%"


def _fmt_billion(v: int | float | None) -> str:
    """거래대금(원) → "{X.X}억" 형식. report.fmt_billion 과 동일 출력.

    None/NaN 은 "—" 로 표시. 0 은 "0.0억" (값이 있는데 0 vs 데이터 없음 구분).
    1000억 이상은 콤마 + 정수, 그 외는 소수 1자리. 사용자가 채널 간 형식
    비일관성을 느끼던 문제 해결 (2026-05-18 정정).
    """
    if v is None or v != v:
        return "—"
    try:
        bil = v / 1e8
    except (TypeError, ValueError):
        return "—"
    if abs(bil) >= 1000:
        return f"{bil:,.0f}억"
    return f"{bil:.1f}억"


def _fmt_int_signed(v: int | None) -> str:
    if v is None:
        return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:,}"


def _fmt_signed_billion(v: int | float | None) -> str:
    """수급 라인용 — 부호 명시 + 억/만 단위 금액."""
    if v is None or v == 0:
        return "0"
    sign = "+" if v > 0 else "-"
    mag = abs(v)
    if mag >= 1e8:
        return f"{sign}{mag / 1e8:.0f}억"
    if mag >= 1e4:
        return f"{sign}{mag / 1e4:.0f}만"
    return f"{sign}{int(mag):,}"


def _fmt_signed_shares(v: int | float | None) -> str:
    """수급 라인용 — 부호 명시 + 만주/주 단위 수량."""
    if v is None or v == 0:
        return "0"
    sign = "+" if v > 0 else "-"
    mag = abs(v)
    if mag >= 1e4:
        return f"{sign}{mag / 1e4:.0f}만주"
    return f"{sign}{int(mag):,}주"


def _fmt_elapsed_short(seconds: int | float | None) -> str:
    """경과 시간 짧은 형식 — Δ 라인 헤더용. 47s / 2m13s / 1h05m."""
    if seconds is None:
        return "—"
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    minutes, sec = divmod(s, 60)
    if minutes < 60:
        return f"{minutes}m{sec:02d}s" if sec else f"{minutes}m"
    hours, mm = divmod(minutes, 60)
    return f"{hours}h{mm:02d}m"


def _krx_tick_size(price: int) -> int:
    """KRX 가격대별 호가 단위 (KOSPI/KOSDAQ 동일, 2023-01 이후 단순화 표).

    매도/매수 주문은 호가 단위 정렬된 가격으로만 들어간다 (정정매매 동일).
    """
    if price < 2_000:
        return 1
    if price < 5_000:
        return 5
    if price < 20_000:
        return 10
    if price < 50_000:
        return 50
    if price < 200_000:
        return 100
    if price < 500_000:
        return 500
    return 1_000


def build_trigger_lines(
    *,
    trigger_states: dict[str, bool] | None,
    is_holding: bool,
    vp_5ma: float | None,
    vp_1ma: float | None,
    accel_ratio_1m: float | None,
    divergence: Any,
) -> list[str]:
    """Exit.Triggers 청산 시그널 C1~C5 표시 라인 list.

    텔레그램 카드(`render_monitor_message`) 와 PWA 페이로드(`build_monitor_payload`)
    둘 다 동일 형식. 보유/감시 모드 분기 — 보유 모드는 C5 포함, 라벨에 "2분 지속".

    마크 (round 36): 평시 `▢` / 발화 `🚧` (이전 ❌/✅ 는 의미 오해 — 매도 발화가
    "성공/완료" 인상이라 위험). 🚧 는 노란 공사 표지로 "주의/매도 검토" 직관 +
    카드의 다른 마크(동그라미·⚠) 와 모양 다름.
    """
    if trigger_states is None:
        return []

    def _mk(fired: bool) -> str:
        return "🚧" if fired else "▢"

    lines: list[str] = []
    section_label = "청산 시그널" if is_holding else "청산 시그널 (현재 시점)"
    lines.append(f"─ {section_label} ─")

    # C1: 체결강도 5MA 100 하향
    c1_mark = _mk(trigger_states.get("E1_vp_below_100", False))
    c1_detail_parts = []
    if vp_5ma is not None and vp_5ma == vp_5ma:
        c1_detail_parts.append(f"5MA {vp_5ma:.0f}")
    if vp_1ma is not None and vp_1ma == vp_1ma:
        c1_detail_parts.append(f"1MA {vp_1ma:.0f}")
    c1_detail = " / ".join(c1_detail_parts) if c1_detail_parts else "—"
    lines.append(f"{c1_mark} 체결강도 5MA 100 하향 (현재 {c1_detail})")

    # C2: Bearish Divergence
    c2_mark = _mk(trigger_states.get("E2_bearish_divergence", False))
    if divergence is not None:
        p = getattr(divergence, "price_change_pct", float("nan"))
        v = getattr(divergence, "vp_5ma_delta", float("nan"))
        p_str = f"{p:+.2f}%" if p == p else "—"
        v_str = f"{v:+.0f}" if v == v else "—"
        lines.append(f"{c2_mark} Bearish Divergence (가격 {p_str} / 체결강도 {v_str})")
    else:
        lines.append(f"{c2_mark} Bearish Divergence")

    # C3: 자금 고갈
    c3_mark = _mk(trigger_states.get("E3_vol_drain", False))
    c3_detail = (
        f" — 현재 {accel_ratio_1m:.1f}배"
        if accel_ratio_1m is not None and accel_ratio_1m == accel_ratio_1m else ""
    )
    c3_rule = "1분 가속 < 0.5, 2분 지속" if is_holding else "1분 가속 < 0.5"
    lines.append(f"{c3_mark} 자금 고갈 ({c3_rule}){c3_detail}")

    # C4: 윗꼬리 50%↑ 음봉 (1분봉)
    c4_mark = _mk(trigger_states.get("E4_bearish_candle", False))
    lines.append(f"{c4_mark} 윗꼬리 50%↑ 음봉 (1분봉 기준)")

    # C5: VI 발동 후 재상승 실패 — 보유 모드만
    if is_holding:
        c5_mark = _mk(trigger_states.get("E5_vi_failure", False))
        lines.append(f"{c5_mark} VI 발동 후 5분 내 재상승 실패")

    return lines


def render_monitor_message(
    monitored: MonitoredStock,
    snapshot_row: dict[str, Any] | None,
    accel_ratio: float | None,
    recent_bar_value: int | None,
    ccnl: dict[str, Any] | None,
    asking: dict[str, Any] | None,
    investor: dict[str, Any] | None,
    sparkline: str,
    now: datetime,
    grace_remaining_seconds: int | None = None,
    accel_ratio_1m: float | None = None,
    last_bar_value: int | None = None,
    transition_info: dict[str, Any] | None = None,
    vp_1ma: float | None = None,
    vp_5ma: float | None = None,
    holding: Any = None,            # Holding 객체 (보유 모드일 때만)
    trigger_states: dict[str, bool] | None = None,  # Exit.Triggers 12개 트리거 발화 상태
    divergence: Any = None,         # DivergenceState
    investor_delta: dict[str, Any] | None = None,  # round 36 후속: 누적값 변화 + elapsed
) -> str:
    """종목별 모니터링 카드 (editMessageText 갱신용).

    Args:
        monitored: 종목 메타.
        snapshot_row: 가격/회전율 (intraday SNAPSHOT_COLUMNS dict).
        accel_ratio: 5분봉 거래대금 가속배율.
        recent_bar_value: 최근 5분 거래대금 합계 (원).
        ccnl: fetch_ccnl_strength 결과.
        asking: fetch_asking_price 결과.
        investor: fetch_investor_flow 결과.
        sparkline: 거래대금 추세 sparkline 문자.
        now: 갱신 시각.
        grace_remaining_seconds: GRACE 상태일 때 a2 카드에 남은 시간.
        accel_ratio_1m: 1분봉 가속배율.
        last_bar_value: 최근 1분 거래대금 (원).
        transition_info: 이 종목이 a1 일 때 부상 후보 a2 정보 (state/candidate_code/
            candidate_turnover). 카드 헤더에 통합 표시 (round 19 정책).
    """
    # 헤더 라벨 — 단저단고 패러다임 (2026-05-29).
    # 보유 / 수동 / 주도주(leader) / 주도주 후보(candidate) 중 켜진 것 모두 표시.
    # is_holding 우선. flag 가 하나도 없고 보유도 아니면 "[단저단고]" 단독.
    is_holding = holding is not None
    flag_labels: list[str] = []
    if is_holding:
        flag_labels.append("💎 보유")
    if monitored.is_manual:
        flag_labels.append("🔵 수동")
    if monitored.is_auto:
        _role = getattr(monitored, "sector_role", None)
        if _role == "leader":
            flag_labels.append("⭐ 주도주")
        elif _role == "candidate":
            flag_labels.append("🌟 주도주 후보")
        else:
            # LEGACY_RISING_FUNNEL=1 또는 fallback — 옛 라벨
            flag_labels.append("⭐ 자동")
    # is_rising 은 LEGACY_RISING_FUNNEL=1 시만 켜짐. 기본 폐기.
    if monitored.is_rising:
        flag_labels.append("⚡ 부상(legacy)")
    header_kind = " / ".join(flag_labels) if flag_labels else "단저단고"
    name = monitored.name or monitored.code

    # 테마 라인 정책 (2026-05-29): 자동 surface 종목은 surface_sector_name 1개만.
    # 수동/보유 + 주도섹터 안에 속하면 그것 1개. 안 속하면 전체 themes list 표시.
    _surface_name = getattr(monitored, "surface_sector_name", None)
    if _surface_name:
        themes_str = _surface_name
    else:
        themes_str = " / ".join(monitored.themes) if monitored.themes else "—"

    grace_label = ""
    if grace_remaining_seconds is not None and grace_remaining_seconds > 0:
        m = grace_remaining_seconds // 60
        s = grace_remaining_seconds % 60
        grace_label = f"  [GRACE {m}:{s:02d} 남음]"

    lines = [
        f"[{header_kind}] {name} ({monitored.code}){grace_label}",
        f"테마: {themes_str}",
    ]

    # 단저단고 시그널 + v10b score (2026-05-27/28, 라벨 제거 2026-05-29).
    # 페이지 자체가 단저단고 모니터링이라 "🔁 단저단고" 라벨 중복 제거.
    # default ON — 끄려면 .env 에 MONITOR_MEAN_REVERSION=0 명시 (back-out 용).
    if os.getenv("MONITOR_MEAN_REVERSION", "1") == "1":
        _mrb = getattr(monitored, "mr_sigB", False)
        _mrs = getattr(monitored, "mr_sigS", False)
        _mrr = getattr(monitored, "mr_reason", None)
        _score = getattr(monitored, "mr_score", 0.0) or 0.0
        _grade = getattr(monitored, "mr_grade", "NEUTRAL")
        if _grade != "NEUTRAL" or _mrb or _mrs:
            grade_emoji = {"STRONG": "🟢", "WATCH": "🟡"}.get(_grade, "⚫")
            sig_emoji = ""
            if _mrb and _mrs:
                sig_emoji = "🟢단저+🔴단고"
            elif _mrb:
                sig_emoji = "🟢 단저"
            elif _mrs:
                sig_emoji = "🔴 단고"
            lines.append(f"{grade_emoji}{_grade} {_score:+.1f} {sig_emoji} — {_mrr or '—'}")

    # a1 카드일 때 TRANSITION/GRACE 부상 후보 표시 (round 19 — 카드 통합)
    if transition_info is not None:
        state = transition_info.get("state")
        cand_code = transition_info.get("candidate_code")
        cand_turnover = transition_info.get("candidate_turnover")
        if state == LeaderState.TRANSITION and cand_code:
            t_str = _fmt_pct(cand_turnover) if cand_turnover is not None else "—"
            lines.append(f"🔥 부상 후보 a2: {cand_code} (회전율 {t_str})")
        elif state == LeaderState.GRACE and cand_code:
            t_str = _fmt_pct(cand_turnover) if cand_turnover is not None else "—"
            lines.append(f"🔄 GRACE — a2: {cand_code} (회전율 {t_str})")

    # 시각 + 가격 한 줄로 합침 (라인 수 절약)
    if snapshot_row:
        price = snapshot_row.get("price", 0)
        prev_close = snapshot_row.get("prev_close", 0) or 0
        ret = snapshot_row.get("daily_return")
        is_lup = snapshot_row.get("is_limit_up", False)
        turnover = snapshot_row.get("turnover")
        trading_value = snapshot_row.get("trading_value", 0)

        lup_mark = " 🔴상한가" if is_lup else ""
        # round 22: 보유 모드면 매수가 + 손익 + 경과 시간(초) 합친 라인.
        if is_holding and holding is not None:
            elapsed_sec = int((now - holding.entry_time).total_seconds())
            pnl_pct = holding.pnl_pct(price) if price else float("nan")
            buy_marker = "🔴" if is_lup else "🔵"  # 보유 매수가 기준선 (현재가 색상과 일관)
            cur_marker = "🔴" if is_lup else "🔵"
            lines.append(
                f"{now.strftime('%H:%M:%S')} (+{elapsed_sec:,}초)  "
                f"{cur_marker}{price:,}원 ({_fmt_pct(ret)}) / "
                f"{buy_marker}{int(holding.entry_price):,}({_fmt_pct(pnl_pct)})"
            )
        else:
            lines.append(
                f"{now.strftime('%H:%M:%S')}  {price:,}원 ({_fmt_pct(ret)}){lup_mark}"
            )
        # +29% 매도가 — 감시 모드만 표시 (보유 모드는 매수가 기준 손절/익절선이 의미).
        if not is_holding and prev_close > 0:
            target_29_raw = prev_close * 1.29
            tick = _krx_tick_size(int(target_29_raw))
            target_29 = (int(target_29_raw) // tick) * tick
            if price > 0:
                remaining_pct = (target_29 - price) / price * 100
                lines.append(
                    f"+29% 매도가: {target_29:,}원 (현재가 대비 {remaining_pct:+.1f}%)"
                )
            else:
                lines.append(f"+29% 매도가: {target_29:,}원")
        # 거래대금 + KIS 진짜 순위 / 회전율 + 거래대금 50위 안 회전율 순위.
        # rank = KIS 시장 전체 거래대금 순위 (ETF 포함 — 보통주만 카드에 보여서
        # "3위, 7위, 11위" 같이 sparse 할 수 있음. HTS 와 1:1 일치).
        # turnover_rank = master 필터 통과 종목 중 회전율 desc 순위 (1~top_n).
        rank = snapshot_row.get("rank")
        rank_str = f" ({int(rank)}위)" if rank else ""
        turnover_rank = snapshot_row.get("turnover_rank")
        turnover_rank_str = (
            f" ({int(turnover_rank)}위)"
            if turnover_rank is not None and turnover_rank == turnover_rank
            else ""
        )
        turnover_val_str = _fmt_pct(turnover) if turnover is not None else "—"
        lines.append(
            f"거래대금: {_fmt_billion(trading_value)}{rank_str}  "
            f"회전율: {turnover_val_str}{turnover_rank_str}"
        )
    else:
        lines.append(f"{now.strftime('%H:%M:%S')}  가격/회전율: —")

    # 5분봉 가속 — 색상 + 라벨 (round 19 — 알림 임계 도달 시 ⚡/⚠ 강조 마크)
    if accel_ratio is not None and accel_ratio == accel_ratio:  # not NaN
        recent_val_int = int(recent_bar_value) if recent_bar_value else 0
        if is_strong_rise(accel_ratio, recent_val_int):
            color5 = "🟢⚡"; arrow = "↑"; label = "강한 부상"
        elif accel_ratio >= 3.0:
            color5 = "🟢"; arrow = "↑"; label = "자금 유입 가속"
        elif accel_ratio >= 1.0:
            color5 = "🟢"; arrow = "↑"; label = "유입"
        elif is_exit_signal(accel_ratio):
            color5 = "🔴⚠"; arrow = "↓"; label = "자금 이탈"
        else:
            color5 = "🟡"; arrow = "↓"; label = "감소"
        bar_val = _fmt_billion(recent_bar_value) if recent_bar_value else "—"
        lines.append(
            f"{color5} 5분봉가속: {arrow} {accel_ratio:.1f}배 ({label})  5분합 {bar_val}"
        )
    else:
        lines.append("⚪ 5분봉가속: —")

    # 1분봉 가속 — first-mover 시그널 (recent=1, baseline=10)
    if accel_ratio_1m is not None and accel_ratio_1m == accel_ratio_1m:
        last_val_int = int(last_bar_value) if last_bar_value else 0
        if is_one_min_rise(accel_ratio_1m, last_val_int):
            color1 = "🟢⚡"; arrow1 = "↑"; label1 = "1분봉 부상"
        elif accel_ratio_1m >= 3.0:
            color1 = "🟢"; arrow1 = "↑"; label1 = "급증"
        elif accel_ratio_1m >= 1.0:
            color1 = "🟢"; arrow1 = "↑"; label1 = "유입"
        elif is_one_min_exit(accel_ratio_1m):
            color1 = "🔴⚠"; arrow1 = "↓"; label1 = "1분봉 급감"
        else:
            color1 = "🟡"; arrow1 = "↓"; label1 = "감소"
        last_val = _fmt_billion(last_bar_value) if last_bar_value else "—"
        lines.append(
            f"{color1} 1분봉가속: {arrow1} {accel_ratio_1m:.1f}배 ({label1})  최근1분 {last_val}"
        )
    else:
        lines.append("⚪ 1분봉가속: —")

    # 체결강도 — 색상 (≥120 매수강세 / 80~120 균형 / <80 매도강세) + 5MA / 1MA (round 22).
    # round 33: ccnl/strength 가 None/NaN 이어도 라인 항상 표시. 데이터 누락 시 "—"
    # placeholder 로 자리를 잡고, MA 가 있으면 따로 표시. 사용자가 "체결강도가 안 보임"
    # 으로 인지하지 않도록 일관성 우선.
    strength = ccnl.get("ccnl_strength") if ccnl else None
    buy_ratio = ccnl.get("buy_ratio") if ccnl else None
    if strength is not None and strength == strength:
        if strength >= 120:
            color_c = "🟢"; balance = "매수 우세"
        elif strength < 80:
            color_c = "🔴"; balance = "매도 우세"
        else:
            color_c = "🟡"; balance = "균형"
        strength_str = f"{strength:.0f} ({balance})"
    else:
        color_c = "⚪"
        strength_str = "— (데이터 없음)"
    ma_part = ""
    if vp_5ma is not None and vp_5ma == vp_5ma:
        ma_part += f"  5MA {vp_5ma:.0f}"
    if vp_1ma is not None and vp_1ma == vp_1ma:
        ma_part += f"  1MA {vp_1ma:.0f}"
    br_str = _fmt_pct(buy_ratio) if buy_ratio is not None and buy_ratio == buy_ratio else "—"
    lines.append(f"{color_c} 체결강도: {strength_str}{ma_part}  매수비율 {br_str}")

    # 호가 잔량 — 매수/매도 잔량 합 + 비율 색상 (🟢🟡🔴) + 1호가 상세
    # 색상 임계는 대칭 (1.5배 / 0.67배). 1.5 = 매수 1.5배, 1/1.5 ≈ 0.67 = 매도 1.5배.
    if asking:
        bid = asking.get("bid_total_volume", 0)
        ask = asking.get("ask_total_volume", 0)
        ratio = asking.get("bid_ask_ratio")
        if ratio is None or ratio != ratio:  # NaN
            color = "⚪"
            ratio_str = "—"
        elif ratio >= 1.5:
            color = "🟢"
            ratio_str = f"{ratio:.1f}배"
        elif ratio <= 0.67:
            color = "🔴"
            ratio_str = f"{ratio:.2f}배"
        else:
            color = "🟡"
            ratio_str = f"{ratio:.1f}배"
        lines.append(
            f"{color} 호가: 매수 {bid:,} / 매도 {ask:,} ({ratio_str})"
        )
        # 1호가 가격 + 잔량 (체결 직전 가장 임박한 매수/매도 단가).
        bid1_p = asking.get("bid1_price", 0)
        ask1_p = asking.get("ask1_price", 0)
        bid1_v = asking.get("bid1_volume", 0)
        ask1_v = asking.get("ask1_volume", 0)
        if bid1_p or ask1_p:
            lines.append(
                f"1호가: 매수 {bid1_p:,}원 ({bid1_v:,}) / "
                f"매도 {ask1_p:,}원 ({ask1_v:,})"
            )

    # 외인 / 기관 / 프로그램 순매수 — round 36 부활. round 22 정정의 명목 사유는
    # "KIS 응답 신뢰도 낮음" 이었으나 round 33/34 체결강도 사건 분석 후 실제
    # 원인은 fetcher 응답 list 첫 행 파싱 버그로 추정 (intraday_realtime round 36).
    # Buy.Score 점수 합산은 round 29 ritual 통과 전엔 X — 카드 표시만 (참고 지표).
    if investor:
        foreign_v = investor.get("foreign_net_buy_value") or 0
        inst_v = investor.get("institution_net_buy_value") or 0
        program_q = investor.get("program_net_buy") or 0
        if foreign_v or inst_v or program_q:
            # round 36 후속: 누계 라인 안에 괄호 Δ — 한 줄로 통합. 헤더 옆 (Δ47s)
            # 가 마지막 갱신 시점, 각 항목 옆 괄호가 그 항목의 변화량. 변화량 0
            # 인 항목은 괄호 생략 (외인은 그대로 + 기관만 움직임 같은 케이스).
            df_v = (investor_delta or {}).get("foreign_value") or 0
            di_v = (investor_delta or {}).get("institution_value") or 0
            dp_q = (investor_delta or {}).get("program_qty") or 0
            has_delta = bool(investor_delta) and (df_v or di_v or dp_q)
            elapsed_part = ""
            if has_delta:
                elapsed = investor_delta.get("elapsed_sec") or 0
                elapsed_part = f"(Δ{_fmt_elapsed_short(elapsed)})"

            def _paren(value: int, formatter) -> str:
                return f" ({formatter(value)})" if value else ""

            lines.append(
                f"수급{elapsed_part}: "
                f"외인 {_fmt_signed_billion(foreign_v)}{_paren(df_v, _fmt_signed_billion)} "
                f"/ 기관 {_fmt_signed_billion(inst_v)}{_paren(di_v, _fmt_signed_billion)} "
                f"/ 프로그램 {_fmt_signed_shares(program_q)}{_paren(dp_q, _fmt_signed_shares)}"
            )

    # 단저단고 히스토리 (2026-05-29) — 옛 Exit.Triggers 청산 시그널 자리.
    # 시그널 발화 시점 최대 3개 (최신순). 사용자가 잠깐 놓치는 시점 대비.
    _mr_history = getattr(monitored, "mr_history", None) or []
    if _mr_history:
        lines.append("─ 단저단고 히스토리 ─")
        for entry in _mr_history[:3]:
            try:
                ts_label = entry.ts.strftime("%H:%M:%S")
            except (AttributeError, ValueError):
                ts_label = "--:--:--"
            kind_emoji = "🟢" if entry.kind == "단저" else "🔴"
            reason_short = (entry.reason or "—")
            if len(reason_short) > 60:
                reason_short = reason_short[:60] + "…"
            lines.append(
                f"{ts_label} {kind_emoji} {entry.kind} "
                f"score {entry.score:.1f} {reason_short}"
            )

    # 자동 풀 surface 종목에서 수동 핀 안내 — 풀 이탈 후 카드 유지하려면 /add.
    if monitored.is_auto and not monitored.is_manual and not is_holding:
        lines.append(f"자동 풀 이탈 후에도 유지하려면 → /add {monitored.code}")

    return "\n".join(lines)


# ── PWA 대시보드 (M7) — 구조화 페이로드 빌더 ──────────────────────────────────


def _clean(v: Any) -> Any:
    """JSON 직렬화 안전 변환: NaN/+-Inf → None. 그 외는 그대로."""
    if isinstance(v, float):
        if v != v or v == float("inf") or v == float("-inf"):
            return None
    return v


def build_monitor_payload(
    monitored: MonitoredStock,
    snapshot_row: dict[str, Any] | None,
    accel_ratio: float | None,
    recent_bar_value: int | None,
    ccnl: dict[str, Any] | None,
    asking: dict[str, Any] | None,
    investor: dict[str, Any] | None,
    now: datetime,
    grace_remaining_seconds: int | None = None,
    accel_ratio_1m: float | None = None,
    last_bar_value: int | None = None,
    transition_info: dict[str, Any] | None = None,
    vp_1ma: float | None = None,
    vp_5ma: float | None = None,
    holding: Any = None,
    trigger_states: dict[str, bool] | None = None,
    divergence: Any = None,
    investor_delta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """PWA 대시보드용 구조화 페이로드 (`render_monitor_message` 와 동일 인자).

    텔레그램 텍스트 렌더와 별도 — 동일 데이터 소스를 JSON 직렬화 안전한 dict 로
    반환한다. WebSocket 으로 PWA 에 broadcast. NaN/Inf 는 None 으로 sanitize.

    스키마는 `docs/dashboard-pwa.md` §4 와 동기화. 변경 시 둘 다 갱신.
    """
    is_holding = holding is not None
    # round 35: multi-flag 모델. source 는 카드 좌측 보더 색상용 (우선순위 derive).
    # flags 는 헤더 라벨 조합용 (auto/rising/manual/hold 가 동시에 켜질 수 있음).
    source_val = monitored.primary_source(is_held=is_holding).value
    flags = {
        "auto": monitored.is_auto,
        "rising": monitored.is_rising,
        "manual": monitored.is_manual,
        "hold": is_holding,
    }

    score = _clean(monitored.buy_score) if monitored.buy_score is not None else None
    header = {
        "grade": monitored.buy_grade,
        "score": score,
        "reasons": list(monitored.buy_reasons) if monitored.buy_reasons else [],
    }

    price_block: dict[str, Any] = {}
    volume_block: dict[str, Any] = {}
    if snapshot_row:
        price = snapshot_row.get("price")
        prev_close = snapshot_row.get("prev_close") or 0
        ret = snapshot_row.get("daily_return")
        is_lup = bool(snapshot_row.get("is_limit_up", False))
        turnover = snapshot_row.get("turnover")
        trading_value = snapshot_row.get("trading_value")
        rank = snapshot_row.get("rank")
        turnover_rank = snapshot_row.get("turnover_rank")
        sell_29_pct: int | None = None
        if prev_close > 0:
            target_29_raw = prev_close * 1.29
            tick = _krx_tick_size(int(target_29_raw))
            sell_29_pct = (int(target_29_raw) // tick) * tick
        price_block = {
            "current": int(price) if price else None,
            "change_pct": _clean(ret),
            "is_limit_up": is_lup,
            "sell_29_pct": sell_29_pct,
        }
        volume_block = {
            "rank": int(rank) if rank else None,
            "turnover_rank": int(turnover_rank) if turnover_rank is not None and turnover_rank == turnover_rank else None,
            "amount": _clean(trading_value),
            "turnover_pct": _clean(turnover),
        }

    def _accel_block(ratio: float | None, bar_value: int | None) -> dict[str, Any]:
        return {
            "ratio": _clean(ratio),
            "bar_value": int(bar_value) if bar_value else None,
        }

    vp_block: dict[str, Any] = {}
    if ccnl:
        vp_block = {
            "current": _clean(ccnl.get("ccnl_strength")),
            "ma_5": _clean(vp_5ma),
            "ma_1": _clean(vp_1ma),
            "buy_ratio": _clean(ccnl.get("buy_ratio")),
        }

    asking_block: dict[str, Any] = {}
    if asking:
        bid1_p = int(asking.get("bid1_price") or 0) or None
        ask1_p = int(asking.get("ask1_price") or 0) or None
        asking_block = {
            "bid_total": int(asking.get("bid_total_volume") or 0),
            "ask_total": int(asking.get("ask_total_volume") or 0),
            "ratio": _clean(asking.get("bid_ask_ratio")),
            "bid1_price": bid1_p,
            "bid1_volume": int(asking.get("bid1_volume") or 0),
            "ask1_price": ask1_p,
            "ask1_volume": int(asking.get("ask1_volume") or 0),
        }

    # round 36: 외인/기관/프로그램 순매수 (텔레그램 카드 수급 라인과 동일 데이터).
    # 모두 0 이면 None — frontend 가 라인 자체 생략.
    investor_block: dict[str, Any] | None = None
    if investor:
        foreign_v = int(investor.get("foreign_net_buy_value") or 0)
        inst_v = int(investor.get("institution_net_buy_value") or 0)
        foreign_q = int(investor.get("foreign_net_buy") or 0)
        inst_q = int(investor.get("institution_net_buy") or 0)
        indiv_q = int(investor.get("individual_net_buy") or 0)
        program_q = int(investor.get("program_net_buy") or 0)
        if foreign_v or inst_v or foreign_q or inst_q or indiv_q or program_q:
            investor_block = {
                "foreign_value": foreign_v,
                "institution_value": inst_v,
                "foreign_qty": foreign_q,
                "institution_qty": inst_q,
                "individual_qty": indiv_q,
                "program_qty": program_q,
            }

    # round 36 후속: 수급 Δ — 마지막으로 누적값이 바뀐 시점부터의 변화량.
    # 텔레그램 카드 Δ 라인과 동일 데이터. 모두 0 또는 None 이면 키 자체 None.
    investor_delta_block: dict[str, Any] | None = None
    if investor_delta:
        df_v = int(investor_delta.get("foreign_value") or 0)
        di_v = int(investor_delta.get("institution_value") or 0)
        dp_q = int(investor_delta.get("program_qty") or 0)
        elapsed = investor_delta.get("elapsed_sec")
        if df_v or di_v or dp_q:
            investor_delta_block = {
                "foreign_value": df_v,
                "institution_value": di_v,
                "program_qty": dp_q,
                "elapsed_sec": int(elapsed) if elapsed is not None else None,
            }

    divergence_block: dict[str, Any] | None = None
    if divergence is not None:
        if getattr(divergence, "bearish", False):
            kind = "bearish"
        elif getattr(divergence, "bullish", False):
            kind = "bullish"
        else:
            kind = "neutral"
        divergence_block = {
            "kind": kind,
            "price_change_pct": _clean(getattr(divergence, "price_change_pct", None)),
            "vp_5ma_delta": _clean(getattr(divergence, "vp_5ma_delta", None)),
        }

    holding_block: dict[str, Any] | None = None
    if is_holding and holding is not None:
        current_price = (price_block.get("current") or 0)
        elapsed_sec = int((now - holding.entry_time).total_seconds())
        pnl_pct = holding.pnl_pct(current_price) if current_price else None
        holding_block = {
            "entry_price": int(holding.entry_price),
            "entry_time": holding.entry_time.isoformat(),
            "elapsed_sec": elapsed_sec,
            "pnl_pct": _clean(pnl_pct),
            "stop_loss_price": int(holding.stop_loss_price),
            "take_profit_1_price": int(holding.take_profit_1_price),
            "take_profit_2_price": int(holding.take_profit_2_price),
            "time_stop_minutes": holding.time_stop_minutes,
            "triggers_fired": list(getattr(holding, "triggers_fired", [])),
        }

    transition_block: dict[str, Any] | None = None
    if transition_info is not None:
        state = transition_info.get("state")
        if isinstance(state, LeaderState):
            state_val = state.value
        else:
            state_val = state
        transition_block = {
            "state": state_val,
            "candidate_code": transition_info.get("candidate_code"),
            "candidate_turnover": _clean(transition_info.get("candidate_turnover")),
        }

    # Exit.Triggers 청산 시그널 라인 — 2026-05-29 카드 표시 폐기 (로깅만 유지).
    # trigger_lines / trigger_states 키는 호환성을 위해 빈 list/None 유지.
    trigger_lines: list[str] = []

    # 단저단고 v10b (2026-05-28) — PWA 도 카드와 동일하게 노출. NEUTRAL + 시그널
    # X 면 None 반환해서 프론트에서 라인 자체 생략 가능.
    mr_grade = getattr(monitored, "mr_grade", "NEUTRAL")
    mr_sigB = bool(getattr(monitored, "mr_sigB", False))
    mr_sigS = bool(getattr(monitored, "mr_sigS", False))
    mean_reversion_block: dict[str, Any] | None = None
    if mr_grade != "NEUTRAL" or mr_sigB or mr_sigS:
        mean_reversion_block = {
            "grade": mr_grade,
            "score": _clean(getattr(monitored, "mr_score", 0.0)),
            "sigB": mr_sigB,
            "sigS": mr_sigS,
            "reason": getattr(monitored, "mr_reason", None),
        }

    # 단저단고 히스토리 (2026-05-29) — 최대 3개 최신순.
    mr_history_block: list[dict[str, Any]] = []
    for entry in (getattr(monitored, "mr_history", None) or [])[:3]:
        try:
            ts_iso = entry.ts.isoformat()
        except AttributeError:
            ts_iso = None
        mr_history_block.append({
            "ts": ts_iso,
            "kind": entry.kind,
            "score": _clean(entry.score),
            "reason": entry.reason,
        })

    # surface_sector_name — 카드 테마 라인이 단일 섹터로 좁아진 경우 표시.
    surface_sector_name = getattr(monitored, "surface_sector_name", None)

    return {
        "code": monitored.code,
        "name": monitored.name or monitored.code,
        "source": source_val,
        "flags": flags,
        "themes": list(monitored.themes),
        "surface_sector_name": surface_sector_name,
        "sector_role": getattr(monitored, "sector_role", None),
        "header": header,
        "price": price_block,
        "volume": volume_block,
        "accel_5m": _accel_block(accel_ratio, recent_bar_value),
        "accel_1m": _accel_block(accel_ratio_1m, last_bar_value),
        "vp": vp_block,
        "asking": asking_block,
        "investor": investor_block,
        "investor_delta": investor_delta_block,
        "divergence": divergence_block,
        "holding": holding_block,
        "transition": transition_block,
        "mean_reversion": mean_reversion_block,
        "mr_history": mr_history_block,
        "grace_remaining_sec": grace_remaining_seconds,
        "trigger_states": dict(trigger_states) if trigger_states else None,
        "trigger_lines": trigger_lines,
        "updated_at": now.isoformat(),
    }
