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
) -> str:
    """종목별 모니터링 메시지 (편집 갱신용).

    Args:
        monitored: 종목 메타.
        snapshot_row: 가격/회전율 (intraday SNAPSHOT_COLUMNS dict).
        accel_ratio: 분봉 거래대금 가속배율.
        recent_bar_value: 최근 5분 거래대금 합계 (원).
        ccnl: fetch_ccnl_strength 결과.
        asking: fetch_asking_price 결과.
        investor: fetch_investor_flow 결과.
        sparkline: 거래대금 추세 sparkline 문자.
        now: 갱신 시각.
        grace_remaining_seconds: GRACE 상태일 때 남은 시간. None 이면 표시 안 함.
    """
    # 헤더
    src_emoji = "⭐자동/주도주" if monitored.source == Source.AUTO else "🔵수동"
    themes_str = " / ".join(monitored.themes) if monitored.themes else "—"
    name = monitored.name or monitored.code

    grace_label = ""
    if grace_remaining_seconds is not None and grace_remaining_seconds > 0:
        m = grace_remaining_seconds // 60
        s = grace_remaining_seconds % 60
        grace_label = f"  [GRACE {m}:{s:02d} 남음]"

    lines = [
        f"[모니터링] {name} ({monitored.code}) {src_emoji}{grace_label}",
        f"테마: {themes_str}",
        "─" * 30,
        f"시각: {now.strftime('%H:%M:%S')}",
    ]

    # 가격 / 등락률 / 상한가 / 회전율
    if snapshot_row:
        price = snapshot_row.get("price", 0)
        ret = snapshot_row.get("daily_return")
        is_lup = snapshot_row.get("is_limit_up", False)
        turnover = snapshot_row.get("turnover")
        trading_value = snapshot_row.get("trading_value", 0)

        lup_mark = " 🔴상한가" if is_lup else ""
        lines.append(
            f"가격: {price:,}원 ({_fmt_pct(ret)}){lup_mark}"
        )
        lines.append(
            f"거래대금: {_fmt_billion(trading_value)}  회전율: "
            f"{_fmt_pct(turnover) if turnover is not None else '—'}"
        )
    else:
        lines.append("가격/회전율: —")

    # 가속배율
    if accel_ratio is not None and accel_ratio == accel_ratio:  # not NaN
        if accel_ratio >= 1.0:
            arrow = "↑"
            label = "자금 유입 가속" if accel_ratio >= 3.0 else "유입"
        else:
            arrow = "↓"
            label = "자금 이탈" if accel_ratio < 0.6 else "감소"
        bar_val = _fmt_billion(recent_bar_value) if recent_bar_value else "—"
        lines.append(
            f"5분봉가속: {arrow} {accel_ratio:.1f}배 ({label})  분봉거래대금 {bar_val}"
        )
    else:
        lines.append("5분봉가속: —")

    # 체결강도
    if ccnl:
        strength = ccnl.get("ccnl_strength")
        buy_ratio = ccnl.get("buy_ratio")
        if strength is not None and strength == strength:
            balance = "매수 우세" if strength > 100 else "매도 우세" if strength < 100 else "균형"
            lines.append(
                f"체결강도: {strength:.0f} ({balance})  매수비율 "
                f"{_fmt_pct(buy_ratio) if buy_ratio is not None else '—'}"
            )

    # 호가 잔량
    if asking:
        bid = asking.get("bid_total_volume", 0)
        ask = asking.get("ask_total_volume", 0)
        ratio = asking.get("bid_ask_ratio")
        ratio_str = f"{ratio:.1f}배" if ratio and ratio == ratio else "—"
        lines.append(
            f"호가잔량: 매수 {bid:,} / 매도 {ask:,} ({ratio_str})"
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

    # Sparkline
    if sparkline:
        lines.append(f"직전 추세: {sparkline}")

    return "\n".join(lines)
