"""실시간 모니터링 워커 (M6).

스케줄러가 3초 간격으로 `dashboard_tick()` 호출 → 한 사이클 처리:
    1) 거래대금 50위 fetch + 주도섹터/주도주 식별 (v1)
    2) 주도주 자동 갱신 → 모니터링 종목 업데이트 (메시지 send/delete)
    3) 각 monitored 종목별 4지표 fetch + 가속배율 계산
    4) 메시지 렌더 → editMessageText (푸시 X)
    5) 상태 머신 step → tracker 상태만 갱신 (카드 헤더에 통합 표시)

별도 thread `command_poll_loop`: getUpdates long polling — 사용자 명령 수신.

알림 정책 (정정 round 19):
    카드 외 별도 푸시는 모두 폐기. TRANSITION / GRACE / 강한 부상 / 자금 이탈 /
    1분봉 부상·급감 / 호가 역전 / 부상 후보 신규 진입은 모두 카드 색상·이모지·
    사유 한 줄로 통합 표시. 카드 재배치(reposition) 도 함께 폐기 — 새 푸시가
    없으니 카드가 위로 밀려날 일이 없음.

I/O 분리:
    - state.py: pure 상태
    - render.py: pure 메시지 빌더
    - intraday_realtime.py: KIS API 호출
    - 본 모듈: 통합/오케스트레이션
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

import pandas as pd
from loguru import logger

from src.dashboard.render import render_monitor_message
from src.dashboard.state import (
    LeaderState,
    MonitoringSession,
)
from src.data.intraday import fetch_volume_rank
from src.data.intraday_realtime import (
    fetch_asking_price,
    fetch_ccnl_strength,
    fetch_investor_flow,
    fetch_minute_bars,
)
from src.jongbae.candle import is_weak_candle, latest_completed_candle
from src.jongbae.config_thresholds import (
    GRACE_PERIOD_SECONDS,
    LEADING_SECTOR_TOP_N,
    LEADING_STOCK_TOP_PER_SECTOR,
    RISING_MIN_SCORE,
    RISING_STAGE2_VOL_ACCEL_MIN,
    RISING_STAGE3_VP_MIN,
    TRANSITION_TURNOVER_RATIO,
)
from src.jongbae.divergence import compute_divergence
from src.jongbae.exit_triggers import (
    Holding,
    evaluate_triggers,
    load_holdings,
)
from src.jongbae.grader import GraderSnapshot, calculate_buy_score
from src.jongbae.leading_theme import (
    identify_early_morning_leaders,
    identify_rising_candidates,
    score_leading_sectors,
)
from src.jongbae.momentum import (
    compute_accel_ratio,
    short_trend_sparkline,
)
from src.jongbae.volume_power import VPSeries
from src.notify.telegram import (
    delete_message,
    edit_message,
    get_updates,
    send_message_single,
)
from src.notify.telegram_bot import apply_command, parse_command


def _evaluate_rising_funnel(
    stage1_candidates: list[dict[str, Any]],
    client: Any,
    snap_by_code: dict[str, dict[str, Any]],
    tick_cache: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """부상 후보 다단계 funnel — Stage 2~4 (round 21).

    Stage 0+1 (snapshot 무료 필터 + 회전율 상위 N) 통과 후보를 받아 차례로:
        Stage 2: minute_bars fetch → vol_accel_5m > 0.8 AND not is_weak_candle
        Stage 3: ccnl fetch → VP ≥ 100
        Stage 4: asking + investor fetch → R14 풀스코어 → score ≥ RISING_MIN_SCORE

    각 단계에서 탈락한 종목은 다음 단계 fetch 비용을 발생시키지 않는다. 통과한
    종목의 fetch 결과는 `tick_cache` 에 보존돼서 카드 렌더 단계에서 재사용.

    Args:
        stage1_candidates: identify_rising_candidates 결과.
        client: KIS client (None 이면 모든 단계 스킵 — 빈 리스트 반환).
        snap_by_code: snapshot 의 code → row 매핑 (rank/가격/회전율 등).
        tick_cache: code → {"bars", "accel_5m", "accel_1m", "candle", "vp",
            "ccnl", "asking", "investor"} — 본 함수가 통과 종목에 한해 채움.

    Returns:
        Stage 4 통과 종목 dict 리스트 (원 cand dict + "buy_score" / "buy_grade" /
        "buy_reasons" 키 추가). 점수 내림차순.
    """
    if client is None or not stage1_candidates:
        return []

    # ── Stage 2: 모멘텀 (minute_bars + vol_accel + candle) ────────────────────
    stage2: list[dict[str, Any]] = []
    for cand in stage1_candidates:
        code = cand["code"]
        bars = fetch_minute_bars(client, code)
        if bars is None or bars.empty:
            continue
        accel_5m = compute_accel_ratio(bars)
        if not (accel_5m == accel_5m) or accel_5m <= RISING_STAGE2_VOL_ACCEL_MIN:
            continue
        candle = latest_completed_candle(bars)
        if candle is not None and is_weak_candle(candle):
            continue
        # 통과 — Stage 4 에서 쓰일 1분 가속도 미리 계산 (분봉 한 번 fetch 재활용)
        accel_1m = compute_accel_ratio(bars, recent_minutes=1, baseline_minutes=10)
        tick_cache[code] = {
            "bars": bars,
            "accel_5m": accel_5m,
            "accel_1m": accel_1m,
            "candle": candle,
        }
        stage2.append(cand)

    if not stage2:
        return []

    # ── Stage 3: 체결강도 ────────────────────────────────────────────────────
    stage3: list[dict[str, Any]] = []
    for cand in stage2:
        code = cand["code"]
        ccnl = fetch_ccnl_strength(client, code)
        if ccnl is None:
            continue
        vp = ccnl.get("ccnl_strength")
        if vp is None or not (vp == vp) or vp < RISING_STAGE3_VP_MIN:
            continue
        tick_cache[code]["ccnl"] = ccnl
        tick_cache[code]["vp"] = float(vp)
        stage3.append(cand)

    if not stage3:
        return []

    # ── Stage 4: R14 풀스코어 ────────────────────────────────────────────────
    out: list[dict[str, Any]] = []
    for cand in stage3:
        code = cand["code"]
        asking = fetch_asking_price(client, code)
        investor = fetch_investor_flow(client, code)
        tick_cache[code]["asking"] = asking
        tick_cache[code]["investor"] = investor

        snap = snap_by_code.get(code, {})
        cache = tick_cache[code]
        bid_ask = float("nan")
        if asking is not None:
            v = asking.get("bid_ask_ratio")
            if v is not None and v == v:
                bid_ask = float(v)
        # 당일 고점 대비 거리 (필수조건 R12.5)
        price = float(snap.get("price") or 0)
        high = float(snap.get("intraday_high") or 0)
        dist_high_pct = (
            (price - high) / high * 100.0 if high > 0 and price > 0 else float("nan")
        )
        gsnap = GraderSnapshot(
            volume_turnover_rank=int(snap.get("rank") or 0) or None,
            vol_accel_1m=cache["accel_1m"] if cache["accel_1m"] == cache["accel_1m"] else float("nan"),
            vol_accel_5m=cache["accel_5m"],
            candle=cache.get("candle"),
            vp=cache["vp"],
            # VP_5MA 시계열은 아직 미보유 (별도 round 에서 추가). 0 = strong/weak
            # 판정 시 NaN 처리 → 가산점 없이 진행.
            vp_5ma=float("nan"),
            divergence=None,
            bid_ask_ratio=bid_ask,
            dist_from_intraday_high_pct=dist_high_pct,
        )
        score_card = calculate_buy_score(gsnap)
        if score_card.score < RISING_MIN_SCORE:
            continue
        enriched = dict(cand)
        enriched["buy_score"] = score_card.score
        enriched["buy_grade"] = score_card.grade
        enriched["buy_reasons"] = list(score_card.reasons)
        out.append(enriched)

    # 점수 내림차순
    out.sort(key=lambda c: c["buy_score"], reverse=True)
    return out


def _send_or_edit_monitor(
    token: str,
    chat_id: str,
    code: str,
    text: str,
    message_ids: dict[str, int],
) -> None:
    """종목 카드 메시지 발송/편집.

    - 최초 (message_ids 에 없음): send 후 id 기록.
    - 기존 (message_ids 에 있음): editMessageText 로 in-place 갱신, 푸시 X.
    """
    msg_id = message_ids.get(code)
    if msg_id is not None:
        edit_message(token, chat_id, msg_id, text, parse_mode=None)
        return
    resp = send_message_single(token, chat_id, text, parse_mode=None)
    if resp and resp.get("ok"):
        new_id = resp.get("result", {}).get("message_id")
        if isinstance(new_id, int):
            message_ids[code] = new_id


def _delete_monitor_message(
    token: str,
    chat_id: str,
    code: str,
    message_ids: dict[str, int],
) -> None:
    msg_id = message_ids.pop(code, None)
    if msg_id is not None:
        delete_message(token, chat_id, msg_id)


def dashboard_tick(
    *,
    session: MonitoringSession,
    message_ids: dict[str, int],
    client: Any,
    master_df: pd.DataFrame,
    theme_mapping_df: pd.DataFrame,
    daily_ohlcv: pd.DataFrame | None,
    token: str,
    chat_id: str,
    now: datetime,
) -> None:
    """한 사이클의 모니터링 처리.

    호출 빈도: 3초 (스케줄러 IntervalTrigger, scheduler.py:741).

    Args:
        session: 공유 세션 상태.
        message_ids: code → telegram message_id (편집 추적).
        client: KISClient.
        master_df: 종목 마스터 (주도섹터 후보 필터용 turnover 적재).
        theme_mapping_df: 네이버 테마 매핑.
        daily_ohlcv: 신고가 판정용 long format. None 가능.
        token: Telegram 봇 토큰.
        chat_id: 채팅 ID.
        now: 현재 시각 (KST).
    """
    if session.paused:
        return
    # in_monitoring_window 가드 폐지 (round 18) — /on 으로 24h 임의 시점에 켤
    # 수 있음. 평일/주말, 정규장 외 시간이어도 KIS 시세를 받아 카드 표시.
    # force_on 은 명시적으로 사용자가 /on 한 상태를 추적할 뿐 가드는 아님.

    # round 22: 보유 종목 메타데이터 로드 (R15 트리거 평가용).
    # /buy /sell 가 holdings.json 을 갱신하므로 매 tick 디스크 로드. 종목 수는
    # 단타 운영상 한 자릿수라 비용 무시 가능.
    holdings: dict[str, Holding] = load_holdings()

    # 1) 시장 스냅샷 + 주도섹터/주도주
    snapshot = fetch_volume_rank(client, top_n=LEADING_SECTOR_TOP_N, master_df=master_df)
    if snapshot.empty:
        return

    sectors = score_leading_sectors(snapshot, theme_mapping_df)
    leaders = identify_early_morning_leaders(
        snapshot, sectors, top_per_theme=LEADING_STOCK_TOP_PER_SECTOR,
    )
    # 부상 후보 Stage 0+1 (snapshot 무료 필터 + 회전율 상위 N). leaders 와 중복 제거.
    rising_stage1 = identify_rising_candidates(
        snapshot, theme_mapping_df=theme_mapping_df,
    )
    leader_codes_set = {l["code"] for l in leaders}
    rising_stage1 = [c for c in rising_stage1 if c["code"] not in leader_codes_set]

    # 2) 자동 모니터링 갱신 (주도주만) — 추가/제거 시 메시지 send/delete
    prev_auto_codes = {c for c, m in session.monitored.items() if m.source.value == "auto"}
    changes = session.update_auto_leaders(leaders, now)
    for ch in changes:
        logger.info(f"[모니터링] {ch}")
    new_auto_codes = {c for c, m in session.monitored.items() if m.source.value == "auto"}
    for dropped in prev_auto_codes - new_auto_codes:
        _delete_monitor_message(token, chat_id, dropped, message_ids)

    snap_by_code = {str(r["code"]): r.to_dict() for _, r in snapshot.iterrows()}

    # /buy CODE (가격 인자 생략) UX 를 위해 최근 시세를 세션에 노출 (round 20).
    # telegram_bot._apply_buy 가 다른 thread 에서 읽음.
    for code, row in snap_by_code.items():
        price = row.get("price")
        if price is not None and price == price and price > 0:
            session.last_prices[code] = float(price)

    # 2b) 부상 후보 Stage 2~4 funnel (round 21) — 모멘텀 → VP → R14 풀스코어.
    # tick_cache 는 funnel 통과 종목의 fetch 결과를 보관해 카드 렌더에서 재사용.
    tick_cache: dict[str, dict[str, Any]] = {}
    rising_scored = _evaluate_rising_funnel(
        rising_stage1, client, snap_by_code, tick_cache,
    )
    prev_rising_codes = {c for c, m in session.monitored.items() if m.source.value == "rising"}
    rising_changes = session.update_rising_candidates(rising_scored, now)
    for ch in rising_changes:
        logger.info(f"[부상] {ch}")
    new_rising_codes = {c for c, m in session.monitored.items() if m.source.value == "rising"}
    for dropped in prev_rising_codes - new_rising_codes:
        _delete_monitor_message(token, chat_id, dropped, message_ids)

    # 카드 헤더에 TRANSITION 부상 후보 정보를 통합 표시하기 위해 a1 → a2 매핑 구성.
    # step_tracker 가 갱신한 후 카드를 렌더해야 정확한 상태를 표시할 수 있으므로
    # 종목 루프 *전* 에 상태 머신을 먼저 step 한다.
    for sector_info in sectors:
        sector = sector_info["theme"]
        sector_codes = sector_info.get("codes", [])
        if not sector_codes:
            continue
        in_sector = [
            (c, snap_by_code.get(c)) for c in sector_codes if snap_by_code.get(c)
        ]
        if not in_sector:
            continue
        in_sector.sort(
            key=lambda x: (x[1].get("turnover") or 0.0)
            if (x[1].get("turnover") or 0.0) == (x[1].get("turnover") or 0.0)
            else 0.0,
            reverse=True,
        )
        a1_code, a1_row = in_sector[0]
        a2 = None
        a2_check = False
        if len(in_sector) >= 2:
            a2_code, a2_row = in_sector[1]
            a2 = {
                "code": a2_code,
                "name": a2_row.get("name", a2_code),
                "turnover": float(a2_row.get("turnover") or 0.0),
            }
            a1_turnover = float(a1_row.get("turnover") or 0.0)
            ratio = (
                a2["turnover"] / a1_turnover if a1_turnover > 0 else 0.0
            )
            a2_check = ratio >= TRANSITION_TURNOVER_RATIO

        a1 = {
            "code": a1_code,
            "name": a1_row.get("name", a1_code),
            "turnover": float(a1_row.get("turnover") or 0.0),
        }
        # step_tracker 는 상태만 갱신 — 알림 발송 X (카드 헤더에 통합 표시).
        session.step_tracker(sector, a1, a2, a2_check, now)

    # tracker 인덱스 — code → (state, candidate_info)
    tracker_info_by_a1: dict[str, dict[str, Any]] = {}
    for tracker in session.trackers.values():
        if tracker.state in (LeaderState.TRANSITION, LeaderState.GRACE):
            tracker_info_by_a1[tracker.incumbent_code] = {
                "state": tracker.state,
                "candidate_code": tracker.candidate_code,
                "candidate_turnover": tracker.candidate_turnover,
            }

    for code, monitored in list(session.monitored.items()):
        snap_row = snap_by_code.get(code)
        cached = tick_cache.get(code, {})
        # 보조지표 — funnel 에서 이미 fetch 한 결과를 우선 재사용 (round 21).
        # cache miss (AUTO/MANUAL 종목) 는 종전대로 매 tick fetch.
        if "bars" in cached:
            bars = cached["bars"]
            accel = cached.get("accel_5m", float("nan"))
            accel_1m = cached.get("accel_1m", float("nan"))
        else:
            bars = fetch_minute_bars(client, code) if client else pd.DataFrame()
            accel = compute_accel_ratio(bars) if not bars.empty else float("nan")
            accel_1m = (
                compute_accel_ratio(bars, recent_minutes=1, baseline_minutes=10)
                if not bars.empty else float("nan")
            )
        recent_value = (
            int(bars.tail(5)["trading_value"].sum()) if not bars.empty else 0
        )
        last_bar_value = (
            int(bars.tail(1)["trading_value"].iloc[0]) if not bars.empty else 0
        )
        ccnl = cached.get("ccnl") if "ccnl" in cached else (
            fetch_ccnl_strength(client, code) if client else None
        )
        asking = cached.get("asking") if "asking" in cached else (
            fetch_asking_price(client, code) if client else None
        )
        investor = cached.get("investor") if "investor" in cached else (
            fetch_investor_flow(client, code) if client else None
        )
        sparkline = short_trend_sparkline(bars, n_recent=6)

        # round 22: VP 시계열 push + 5MA / 1MA / 20MA 산출 (체결강도 라인 보강 + R15 C1)
        vp_now = float("nan")
        vp_1ma = float("nan")
        vp_5ma = float("nan")
        vp_5ma_prev = float("nan")
        if ccnl is not None:
            raw_vp = ccnl.get("ccnl_strength")
            if raw_vp is not None and raw_vp == raw_vp:
                vp_now = float(raw_vp)
                series = session.vp_series.get(code)
                if series is None:
                    series = VPSeries()
                    session.vp_series[code] = series
                # push 전에 직전 5MA 캡처 — C1 cross 판정용
                vp_5ma_prev = series.ma_5(now)
                series.push(now, vp_now)
                vp_1ma = series.ma_1(now)
                vp_5ma = series.ma_5(now)

        # GRACE 잔여 시간 (a2 카드 측 표시용)
        grace_remaining = None
        for tracker in session.trackers.values():
            if tracker.state == LeaderState.GRACE and tracker.candidate_code == code:
                if tracker.state_entered_at is not None:
                    elapsed = (now - tracker.state_entered_at).total_seconds()
                    grace_remaining = max(0, int(GRACE_PERIOD_SECONDS - elapsed))
                break

        # 이 종목이 a1 (현재 주도주) 이면 TRANSITION/GRACE 부상 후보 정보 전달
        transition_info = tracker_info_by_a1.get(code)

        # round 22: 보유 모드 = holdings 에 등록된 종목. evaluate_triggers 매 tick 호출,
        # 결과 + holding 메타데이터를 render 에 전달. 트리거 발화는 카드에 표시만 (push X).
        holding = holdings.get(code)
        trigger_states: dict[str, bool] | None = None
        divergence_state = None
        if holding is not None:
            # 5분 이평 (A3 입력) 분봉의 최근 5봉 종가 평균 — 1분봉 5개 = 5분 이평
            minute_ma_5 = None
            if not bars.empty and "close" in bars.columns:
                tail = bars.tail(5)["close"].dropna()
                if not tail.empty:
                    minute_ma_5 = float(tail.mean())
            # 직전 candle (1분봉) — C4 입력
            candle_for_trig = cached.get("candle") or latest_completed_candle(bars)
            # 다이버전스 — VP_5MA 의 5분 전 값과 현재 가격 5분 전 값 비교
            price_now = float(snap_row.get("price", 0)) if snap_row else 0.0
            price_5m_ago = price_now
            if not bars.empty and len(bars) >= 5 and "close" in bars.columns:
                price_5m_ago = float(bars.iloc[-5]["close"])
            # VP_5MA 5분 전 값은 시계열 캐시에서 5분 전 ma 계산 (근사: ma 함수에 과거 now 인자)
            # 간단화 — 직전 tick 5MA 를 사용 (vp_5ma_prev)
            divergence_state = compute_divergence(
                price_now=price_now,
                price_5m_ago=price_5m_ago,
                vp_5ma_now=vp_5ma,
                vp_5ma_5m_ago=vp_5ma_prev,
            )
            events = evaluate_triggers(
                holding=holding,
                now=now,
                current_price=price_now,
                minute_ma_5=minute_ma_5,
                candle=candle_for_trig,
                vp_5ma_prev=vp_5ma_prev if vp_5ma_prev == vp_5ma_prev else None,
                vp_5ma_now=vp_5ma if vp_5ma == vp_5ma else None,
                divergence=divergence_state,
                vol_accel_1m_value=accel_1m if accel_1m == accel_1m else None,
            )
            for ev in events:
                logger.info(f"[R15 트리거] {code} {ev.kind} — {ev.text}")
            # 카드 표시는 holding.triggers_fired 전체 + 분기별 현재 상태 사용
            trigger_states = dict.fromkeys([
                "A1_stop_price", "A2_stop_bar_low", "A3_stop_ma", "A4_stop_time",
                "B1_take_profit_1", "B2_take_profit_2", "B3_trailing",
                "C1_vp_below_100", "C2_bearish_divergence", "C3_vol_drain",
                "C4_bearish_candle", "C5_vi_failure",
            ], False)
            for k in holding.triggers_fired:
                trigger_states[k] = True

        text = render_monitor_message(
            monitored=monitored,
            snapshot_row=snap_row,
            accel_ratio=accel if accel == accel else None,
            recent_bar_value=recent_value or None,
            ccnl=ccnl,
            asking=asking,
            investor=investor,
            sparkline=sparkline,
            now=now,
            grace_remaining_seconds=grace_remaining,
            accel_ratio_1m=accel_1m if accel_1m == accel_1m else None,
            last_bar_value=last_bar_value or None,
            transition_info=transition_info,
            vp_1ma=vp_1ma if vp_1ma == vp_1ma else None,
            vp_5ma=vp_5ma if vp_5ma == vp_5ma else None,
            holding=holding,
            trigger_states=trigger_states,
            divergence=divergence_state,
        )
        _send_or_edit_monitor(token, chat_id, code, text, message_ids)


def cleanup_messages(
    *,
    token: str,
    chat_id: str,
    session: MonitoringSession,
    message_ids: dict[str, int],
) -> None:
    """모니터링 종료 시 메시지들 정리.

    10:30 종료 잡에서 호출. 수동 종목도 자동 종목도 모두 삭제.
    """
    for code in list(message_ids.keys()):
        _delete_monitor_message(token, chat_id, code, message_ids)
    session.monitored.clear()
    session.trackers.clear()


def reset_daily(session: MonitoringSession) -> None:
    """매일 자동 ON — paused 상태 리셋.

    08:30 또는 09:00 직전 호출. trackers/monitored 도 함께 초기화.
    """
    session.paused = False
    session.force_on = False  # 어제 /on 한 상태가 다음날까지 살지 않도록 리셋
    session.monitored.clear()
    session.trackers.clear()


# ── 사용자 명령 long polling thread ──────────────────────────────────────────


def command_poll_loop(
    session: MonitoringSession,
    token: str,
    chat_id: str,
    stop_event: threading.Event,
    poll_timeout: int = 30,
) -> None:
    """별도 thread 에서 getUpdates 무한 루프 — 사용자 명령 수신/응답.

    Args:
        session: 공유 세션.
        token: 봇 토큰.
        chat_id: 채팅 ID (이 chat 의 메시지만 처리).
        stop_event: 외부에서 종료 시그널.
        poll_timeout: long polling 대기 시간(초).
    """
    offset: int | None = None
    logger.info("[명령 poll] 시작")
    while not stop_event.is_set():
        updates = get_updates(token, offset=offset, timeout=poll_timeout)
        for upd in updates:
            try:
                offset = max(offset or 0, upd["update_id"]) + 1
                msg = upd.get("message") or {}
                if str(msg.get("chat", {}).get("id")) != str(chat_id):
                    continue  # 다른 chat 무시
                text = msg.get("text", "")
                cmd = parse_command(text)
                from src.config import now_kst as _now
                response = apply_command(cmd, session, _now())
                if response:
                    send_message_single(token, chat_id, response, parse_mode=None)
            except Exception as e:  # noqa: BLE001
                logger.exception(f"[명령 처리] 오류: {e}")
        # stop 체크는 timeout 으로 자연스럽게 일어남
    logger.info("[명령 poll] 종료")


def start_command_thread(
    session: MonitoringSession,
    token: str,
    chat_id: str,
) -> tuple[threading.Thread, threading.Event]:
    """command_poll_loop 를 daemon thread 로 시작."""
    stop_event = threading.Event()
    th = threading.Thread(
        target=command_poll_loop,
        args=(session, token, chat_id, stop_event),
        daemon=True,
        name="dashboard-command-poll",
    )
    th.start()
    return th, stop_event
