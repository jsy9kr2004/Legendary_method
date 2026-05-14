"""실시간 모니터링 메시지 렌더링 (M6).

종목별 메시지 1개를 만들어 1~2초마다 editMessageText 로 갱신.
관련 보조지표 (분봉 가속배율 / 체결강도 / 호가잔량 / 외국인 순매수)를
한눈에 보여주는 포맷.

Pure 함수 — fetch 결과 dict 받아 마크다운 텍스트 반환.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from src.dashboard.state import LeaderState, MonitoredStock, Source
from src.jongbae.momentum import (
    is_exit_signal,
    is_one_min_exit,
    is_one_min_rise,
    is_strong_rise,
)


def _fmt_pct(v: float | None) -> str:
    if v is None or v != v:
        return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.1f}%"


def _fmt_billion(v: int | float | None) -> str:
    if v is None or v == 0:
        return "—"
    if abs(v) >= 1e8:
        return f"{v / 1e8:.0f}억"
    if abs(v) >= 1e4:
        return f"{v / 1e4:.0f}만"
    return f"{v:,}"


def _fmt_int_signed(v: int | None) -> str:
    if v is None:
        return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:,}"


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
    # 헤더 — source 별 emoji + 라벨
    if monitored.source == Source.AUTO:
        src_emoji = "⭐자동/주도주"
        header_kind = "모니터링"
    elif monitored.source == Source.RISING:
        src_emoji = "⚡부상 후보"
        header_kind = "부상"
    else:
        src_emoji = "🔵수동"
        header_kind = "모니터링"
    themes_str = " / ".join(monitored.themes) if monitored.themes else "—"
    name = monitored.name or monitored.code

    grace_label = ""
    if grace_remaining_seconds is not None and grace_remaining_seconds > 0:
        m = grace_remaining_seconds // 60
        s = grace_remaining_seconds % 60
        grace_label = f"  [GRACE {m}:{s:02d} 남음]"

    # 매수 점수/등급 (round 21) — RISING 카드는 항상, AUTO/MANUAL 도 있을 때 표시.
    grade_label = ""
    if monitored.buy_grade and monitored.buy_score is not None:
        grade_emoji = {
            "STRONG": "🟢", "WATCH": "🟡", "NEUTRAL": "⚫", "AVOID": "🔴",
        }.get(monitored.buy_grade, "")
        grade_label = f"  {grade_emoji} {monitored.buy_grade} {monitored.buy_score:+.1f}점"

    lines = [
        f"[{header_kind}] {name} ({monitored.code}) {src_emoji}{grace_label}{grade_label}",
        f"테마: {themes_str}",
    ]
    if monitored.buy_reasons:
        lines.append("사유: " + " / ".join(monitored.buy_reasons[:3]))

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
        lines.append(
            f"{now.strftime('%H:%M:%S')}  {price:,}원 ({_fmt_pct(ret)}){lup_mark}"
        )
        # +29% 매도가 (상한가 +30% 직전 — 매매 정지 + 매도 호가 압박 회피)
        # 호가 단위로 내림 → 그대로 매도 주문 가능. 사용자는 정정매매로 미세 조정.
        if prev_close > 0:
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
        # 거래대금 + 순위 (snapshot 의 rank) + 회전율
        rank = snapshot_row.get("rank")
        rank_str = f" ({int(rank)}위)" if rank else ""
        lines.append(
            f"거래대금: {_fmt_billion(trading_value)}{rank_str}  회전율: "
            f"{_fmt_pct(turnover) if turnover is not None else '—'}"
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

    # 체결강도 — 색상 (≥120 매수강세 / 80~120 균형 / <80 매도강세)
    if ccnl:
        strength = ccnl.get("ccnl_strength")
        buy_ratio = ccnl.get("buy_ratio")
        if strength is not None and strength == strength:
            if strength >= 120:
                color_c = "🟢"; balance = "매수 우세"
            elif strength < 80:
                color_c = "🔴"; balance = "매도 우세"
            else:
                color_c = "🟡"; balance = "균형"
            lines.append(
                f"{color_c} 체결강도: {strength:.0f} ({balance})  매수비율 "
                f"{_fmt_pct(buy_ratio) if buy_ratio is not None else '—'}"
            )

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

    # 외국인 / 기관 / 프로그램
    if investor:
        f = investor.get("foreign_net_buy", 0)
        i = investor.get("institution_net_buy", 0)
        p = investor.get("program_net_buy", 0)
        lines.append(
            f"외국인 {_fmt_int_signed(f)} 주  기관 {_fmt_int_signed(i)} 주  "
            f"프로그램 {_fmt_int_signed(p)} 주"
        )

    # sparkline 라인 제거 — 5분봉/1분봉 가속으로 충분 (사용자 요청)

    # RISING 한정: 사용자 명령 안내 — 바로 복사해서 수동 모니터링 승격 가능
    if monitored.source == Source.RISING:
        lines.append(f"매매 결정 시 → /add {monitored.code}")

    return "\n".join(lines)
