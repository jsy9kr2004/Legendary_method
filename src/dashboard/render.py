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


def build_trigger_lines(
    *,
    trigger_states: dict[str, bool] | None,
    is_holding: bool,
    vp_5ma: float | None,
    vp_1ma: float | None,
    accel_ratio_1m: float | None,
    divergence: Any,
) -> list[str]:
    """R15 청산 시그널 C1~C5 표시 라인 list.

    텔레그램 카드(`render_monitor_message`) 와 PWA 페이로드(`build_monitor_payload`)
    둘 다 동일 형식. 보유/감시 모드 분기 — 보유 모드는 C5 포함, 라벨에 "2분 지속".
    """
    if trigger_states is None:
        return []

    lines: list[str] = []
    section_label = "청산 시그널" if is_holding else "청산 시그널 (현재 시점)"
    lines.append(f"─ {section_label} ─")

    # C1: 체결강도 5MA 100 하향
    c1_mark = "✅" if trigger_states.get("C1_vp_below_100") else "❌"
    c1_detail_parts = []
    if vp_5ma is not None and vp_5ma == vp_5ma:
        c1_detail_parts.append(f"5MA {vp_5ma:.0f}")
    if vp_1ma is not None and vp_1ma == vp_1ma:
        c1_detail_parts.append(f"1MA {vp_1ma:.0f}")
    c1_detail = " / ".join(c1_detail_parts) if c1_detail_parts else "—"
    lines.append(f"{c1_mark} 체결강도 5MA 100 하향 (현재 {c1_detail})")

    # C2: Bearish Divergence
    c2_mark = "✅" if trigger_states.get("C2_bearish_divergence") else "❌"
    if divergence is not None:
        p = getattr(divergence, "price_change_pct", float("nan"))
        v = getattr(divergence, "vp_5ma_delta", float("nan"))
        p_str = f"{p:+.2f}%" if p == p else "—"
        v_str = f"{v:+.0f}" if v == v else "—"
        lines.append(f"{c2_mark} Bearish Divergence (가격 {p_str} / 체결강도 {v_str})")
    else:
        lines.append(f"{c2_mark} Bearish Divergence")

    # C3: 자금 고갈
    c3_mark = "✅" if trigger_states.get("C3_vol_drain") else "❌"
    c3_detail = (
        f" — 현재 {accel_ratio_1m:.1f}배"
        if accel_ratio_1m is not None and accel_ratio_1m == accel_ratio_1m else ""
    )
    c3_rule = "1분 가속 < 0.5, 2분 지속" if is_holding else "1분 가속 < 0.5"
    lines.append(f"{c3_mark} 자금 고갈 ({c3_rule}){c3_detail}")

    # C4: 윗꼬리 50%↑ 음봉 (1분봉)
    c4_mark = "✅" if trigger_states.get("C4_bearish_candle") else "❌"
    lines.append(f"{c4_mark} 윗꼬리 50%↑ 음봉 (1분봉 기준)")

    # C5: VI 발동 후 재상승 실패 — 보유 모드만
    if is_holding:
        c5_mark = "✅" if trigger_states.get("C5_vi_failure") else "❌"
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
    trigger_states: dict[str, bool] | None = None,  # R15 12개 트리거 발화 상태
    divergence: Any = None,         # DivergenceState
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
    # 헤더 — 보유/감시 모드 분기 (round 22).
    # 보유 모드 = holding 인자 있을 때. source emoji prefix 없이 [보유] 만 표시
    # (CLAUDE.md 정책: source 라벨 중복 방지).
    is_holding = holding is not None
    if is_holding:
        src_emoji = ""
        header_kind = "보유"
    elif monitored.source == Source.AUTO:
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

    src_part = f" {src_emoji}" if src_emoji else ""
    lines = [
        f"[{header_kind}] {name} ({monitored.code}){src_part}{grace_label}{grade_label}",
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

    # 외국인 / 기관 / 프로그램 — round 22: 데이터 신뢰도 낮아 카드에서 제거.
    # 단 investor 인자는 호환 위해 유지.

    # 청산 시그널 (R15 C 그룹) — build_trigger_lines 헬퍼 (텔레그램 / PWA 공용).
    lines.extend(build_trigger_lines(
        trigger_states=trigger_states,
        is_holding=is_holding,
        vp_5ma=vp_5ma,
        vp_1ma=vp_1ma,
        accel_ratio_1m=accel_ratio_1m,
        divergence=divergence,
    ))

    # RISING 한정: 사용자 명령 안내 — 바로 복사해서 수동 모니터링 승격 가능
    if monitored.source == Source.RISING:
        lines.append(f"매매 결정 시 → /add {monitored.code}")

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
) -> dict[str, Any]:
    """PWA 대시보드용 구조화 페이로드 (`render_monitor_message` 와 동일 인자).

    텔레그램 텍스트 렌더와 별도 — 동일 데이터 소스를 JSON 직렬화 안전한 dict 로
    반환한다. WebSocket 으로 PWA 에 broadcast. NaN/Inf 는 None 으로 sanitize.

    스키마는 `docs/dashboard-pwa.md` §4 와 동기화. 변경 시 둘 다 갱신.
    """
    is_holding = holding is not None
    source_val = "hold" if is_holding else monitored.source.value

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

    trigger_lines = build_trigger_lines(
        trigger_states=trigger_states,
        is_holding=is_holding,
        vp_5ma=vp_5ma,
        vp_1ma=vp_1ma,
        accel_ratio_1m=accel_ratio_1m,
        divergence=divergence,
    )

    return {
        "code": monitored.code,
        "name": monitored.name or monitored.code,
        "source": source_val,
        "themes": list(monitored.themes),
        "header": header,
        "price": price_block,
        "volume": volume_block,
        "accel_5m": _accel_block(accel_ratio, recent_bar_value),
        "accel_1m": _accel_block(accel_ratio_1m, last_bar_value),
        "vp": vp_block,
        "asking": asking_block,
        "divergence": divergence_block,
        "holding": holding_block,
        "transition": transition_block,
        "grace_remaining_sec": grace_remaining_seconds,
        "trigger_states": dict(trigger_states) if trigger_states else None,
        "trigger_lines": trigger_lines,
        "updated_at": now.isoformat(),
    }
