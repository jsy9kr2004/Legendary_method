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

from src.dashboard.render import build_monitor_payload, render_monitor_message
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
    compute_c_signal_states,
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
    compute_minute_ma,
    compute_vwap,
    price_vs_ma_pct,
    price_vs_vwap_pct,
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


def _prev_day_volume(daily_ohlcv: pd.DataFrame | None, code: str) -> float:
    """종목별 가장 최근 일봉의 거래량 (round 32, P2-2 wiring).

    R14d volume_ratio_vs_prev_day 분모. 데이터 없으면 NaN.
    """
    if daily_ohlcv is None or daily_ohlcv.empty:
        return float("nan")
    df = daily_ohlcv[daily_ohlcv["code"].astype(str) == code]
    if df.empty:
        return float("nan")
    df = df.sort_values("date")
    v = df.iloc[-1].get("volume")
    if v is None or v != v or float(v) <= 0:
        return float("nan")
    return float(v)


def _evaluate_rising_funnel(
    stage1_candidates: list[dict[str, Any]],
    client: Any,
    snap_by_code: dict[str, dict[str, Any]],
    tick_cache: dict[str, dict[str, Any]],
    daily_ohlcv: pd.DataFrame | None = None,
    limit_up_hit_times: dict[str, Any] | None = None,
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

    # 진단 로깅 — 사용자가 "30분 동안 후보 카드 한 개도 못 봄" 케이스에서 어느 단계에
    # 서 drop 되는지 확인용 (round 33). 매 tick 출력은 시끄러우니 stage 별 통과 수만.
    n_stage1 = len(stage1_candidates)

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
    # round 33 (정정): VP 데이터 부재(KIS cttr None/NaN)는 hard-fail 아닌 통과.
    # 사용자 보고: KIS 응답 cttr 이 빈값으로 와서 funnel 의 모든 후보가 Stage 3 에서
    # 죽어 RISING 카드가 0건. 데이터 없음과 "VP 명시적으로 낮음" 은 다른 신호 —
    # NaN/None 은 Stage 4 R14 점수에서 가중치 0 으로 처리. 명시적으로 100 미만일
    # 때만 매수 압력 약함으로 drop. 이렇게 하면 흥아해운류 회귀도 유지 (해당 케이스
    # 는 vp_5ma 가 NaN 이어도 vol_accel/봉/이평 음수 합산으로 점수 부족 → Stage 4 drop).
    stage3: list[dict[str, Any]] = []
    for cand in stage2:
        code = cand["code"]
        ccnl = fetch_ccnl_strength(client, code)
        vp_value: float = float("nan")
        if ccnl is not None:
            vp = ccnl.get("ccnl_strength")
            if vp is not None and vp == vp:
                vp_value = float(vp)
                if vp_value < RISING_STAGE3_VP_MIN:
                    continue  # 명시적으로 매도 우세 — drop
            # else: NaN/None → pass through (데이터 부재)
        tick_cache[code]["ccnl"] = ccnl
        tick_cache[code]["vp"] = vp_value
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
        # round 23~24 wiring (P0-1, P0-2): bars 로 VWAP/MA5/MA20 계산.
        # 호출 비용 0 — Stage 2 에서 이미 fetch 한 bars 재사용.
        bars = cache["bars"]
        vwap = compute_vwap(bars)
        ma5 = compute_minute_ma(bars, window_minutes=5)
        ma20 = compute_minute_ma(bars, window_minutes=20)
        vwap_pct = price_vs_vwap_pct(price, vwap) if price > 0 else float("nan")
        ma5_pct = price_vs_ma_pct(price, ma5) if price > 0 else float("nan")
        ma20_pct = price_vs_ma_pct(price, ma20) if price > 0 else float("nan")

        # round 28 wiring (P2-2): 오늘 누적 거래량 / 전일 일봉 거래량.
        today_volume = float(snap.get("volume") or 0)
        prev_volume = _prev_day_volume(daily_ohlcv, code)
        if today_volume > 0 and prev_volume == prev_volume and prev_volume > 0:
            vol_ratio = today_volume / prev_volume
        else:
            vol_ratio = float("nan")

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
            price_vs_vwap_pct=vwap_pct,
            price_vs_ma5_pct=ma5_pct,
            price_vs_ma20_pct=ma20_pct,
            volume_ratio_vs_prev_day=vol_ratio,
            limit_up_hit_time=(
                limit_up_hit_times.get(code) if limit_up_hit_times else None
            ),
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
    logger.info(
        f"[funnel] stage1={n_stage1} → stage2={len(stage2)} → stage3={len(stage3)} → "
        f"통과={len(out)} (RISING_MIN_SCORE={RISING_MIN_SCORE})"
    )
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
    # 진단 — leaders/sectors 가 0건이면 사용자가 "주도주 모니터링 안 나옴" 으로 인지.
    # 매 tick (3초) 마다 디버그 — DEBUG 레벨이라 운영 운영 시 INFO 만 보면 묻힘.
    logger.debug(
        f"[모니터링] snapshot={len(snapshot)}, sectors={len(sectors)}, "
        f"leaders={len(leaders)}, rising_stage1={len(rising_stage1)}"
    )

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
        daily_ohlcv=daily_ohlcv,
        limit_up_hit_times=session.limit_up_hit_times,
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

        # 청산 시그널 (R15 C 그룹) — 보유든 감시든 모든 카드에 표시.
        # 매도 시그널이 켜진 종목은 매수 진입 회피해야 하므로 감시 모드도 노출.
        # 다이버전스 / 직전 봉 / 5분 이평은 시장 메트릭 — holding 여부와 무관하게 계산.
        minute_ma_5: float | None = None
        if not bars.empty and "close" in bars.columns:
            tail = bars.tail(5)["close"].dropna()
            if not tail.empty:
                minute_ma_5 = float(tail.mean())
        candle_for_trig = cached.get("candle") or latest_completed_candle(bars)
        price_now = float(snap_row.get("price", 0)) if snap_row else 0.0
        price_5m_ago = price_now
        if not bars.empty and len(bars) >= 5 and "close" in bars.columns:
            price_5m_ago = float(bars.iloc[-5]["close"])
        divergence_state = compute_divergence(
            price_now=price_now,
            price_5m_ago=price_5m_ago,
            vp_5ma_now=vp_5ma,
            vp_5ma_5m_ago=vp_5ma_prev,
        )

        holding = holdings.get(code)
        if holding is not None:
            # 보유 모드: evaluate_triggers 가 A/B/C 전체 평가 + holding 상태 mutate.
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
            # A/B/C 전체 표시는 triggers_fired (sticky) 기반.
            trigger_states = dict.fromkeys([
                "A1_stop_price", "A2_stop_bar_low", "A3_stop_ma", "A4_stop_time",
                "A5_eod_ma_break",
                "B1_take_profit_1", "B2_take_profit_2", "B3_trailing",
                "C1_vp_below_100", "C2_bearish_divergence", "C3_vol_drain",
                "C4_bearish_candle", "C5_vi_failure",
            ], False)
            for k in holding.triggers_fired:
                trigger_states[k] = True
        else:
            # 감시/부상/수동 모드: C1~C4 instantaneous (C5 는 render 에서 숨김).
            trigger_states = compute_c_signal_states(
                vp_5ma_prev=vp_5ma_prev if vp_5ma_prev == vp_5ma_prev else None,
                vp_5ma_now=vp_5ma if vp_5ma == vp_5ma else None,
                divergence=divergence_state,
                vol_accel_1m=accel_1m if accel_1m == accel_1m else None,
                candle=candle_for_trig,
                holding=None,
            )

        # round 33/35: 모든 모니터링 모드(AUTO/MANUAL/HOLD/RISING) 에 R14 매수 점수 계산.
        # round 33 에선 `if snap_row is not None:` 가드가 있어서 거래대금 50위 밖
        # 종목(주로 보유 / 수동)은 grade 계산 자체가 skip 됐다. 사용자 보고:
        # "보유/수동 모니터링 어느 경우든 등급 안 뜸". 가드 폐지 — snap_row 가 없어도
        # bars/ccnl 같은 다른 fetch 결과로 부분 점수라도 계산해서 monitored.buy_grade
        # 채움. grader 입력은 모두 NaN-safe 라 부분 데이터로도 동작.
        snap_row_d = snap_row or {}
        bid_ask = float("nan")
        if asking is not None:
            v = asking.get("bid_ask_ratio")
            if v is not None and v == v:
                bid_ask = float(v)
        # 가격 fallback: snap_row > bars 마지막 close > 0. 50위 밖 종목도 bars 가 있으면
        # divergence / VWAP / MA / dist_high 계산 가능.
        price_for_grade = float(snap_row_d.get("price") or 0)
        if price_for_grade <= 0 and not bars.empty and "close" in bars.columns:
            price_for_grade = float(bars.iloc[-1]["close"]) if pd.notna(bars.iloc[-1]["close"]) else 0.0
        high_for_grade = float(snap_row_d.get("intraday_high") or 0)
        # intraday_high 도 fallback — bars 의 최고가
        if high_for_grade <= 0 and not bars.empty and "high" in bars.columns:
            high_for_grade = float(bars["high"].max()) if pd.notna(bars["high"].max()) else 0.0
        dist_high = (
            (price_for_grade - high_for_grade) / high_for_grade * 100.0
            if high_for_grade > 0 and price_for_grade > 0 else float("nan")
        )
        vwap_g = compute_vwap(bars) if not bars.empty else float("nan")
        ma5_g = compute_minute_ma(bars, window_minutes=5) if not bars.empty else float("nan")
        ma20_g = compute_minute_ma(bars, window_minutes=20) if not bars.empty else float("nan")
        vwap_pct_g = (
            price_vs_vwap_pct(price_for_grade, vwap_g)
            if price_for_grade > 0 else float("nan")
        )
        ma5_pct_g = (
            price_vs_ma_pct(price_for_grade, ma5_g)
            if price_for_grade > 0 else float("nan")
        )
        ma20_pct_g = (
            price_vs_ma_pct(price_for_grade, ma20_g)
            if price_for_grade > 0 else float("nan")
        )
        today_volume_g = float(snap_row_d.get("volume") or 0)
        prev_volume_g = _prev_day_volume(daily_ohlcv, code)
        if today_volume_g > 0 and prev_volume_g == prev_volume_g and prev_volume_g > 0:
            vol_ratio_g = today_volume_g / prev_volume_g
        else:
            vol_ratio_g = float("nan")
        # rank 가 0/None 이면 None — grader 의 회전율 가산 (+1) 만 skip, 다른 시그널 정상 평가.
        rank_for_grade = int(snap_row_d.get("rank") or 0) or None
        grade_snap = GraderSnapshot(
            volume_turnover_rank=rank_for_grade,
            vol_accel_1m=accel_1m if accel_1m == accel_1m else float("nan"),
            vol_accel_5m=accel if accel == accel else float("nan"),
            candle=candle_for_trig,
            vp=vp_now,
            vp_5ma=vp_5ma if vp_5ma == vp_5ma else float("nan"),
            divergence=divergence_state,
            bid_ask_ratio=bid_ask,
            dist_from_intraday_high_pct=dist_high,
            price_vs_vwap_pct=vwap_pct_g,
            price_vs_ma5_pct=ma5_pct_g,
            price_vs_ma20_pct=ma20_pct_g,
            volume_ratio_vs_prev_day=vol_ratio_g,
            limit_up_hit_time=session.limit_up_hit_times.get(code),
        )
        sc = calculate_buy_score(grade_snap)
        monitored.buy_score = sc.score
        monitored.buy_grade = sc.grade
        monitored.buy_reasons = list(sc.reasons)

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

        # M7 PWA 페이로드 — 텔레그램 텍스트와 동일 데이터 소스. WebSocket broadcast 용.
        session.last_payloads[code] = build_monitor_payload(
            monitored=monitored,
            snapshot_row=snap_row,
            accel_ratio=accel if accel == accel else None,
            recent_bar_value=recent_value or None,
            ccnl=ccnl,
            asking=asking,
            investor=investor,
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

    # M7 PWA: monitored 에서 빠진 종목 페이로드 정리 (tick 끝)
    stale_codes = set(session.last_payloads.keys()) - set(session.monitored.keys())
    for stale in stale_codes:
        session.last_payloads.pop(stale, None)
    session.last_payload_ts = now


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
    session.last_payloads.clear()


def reset_daily(session: MonitoringSession) -> None:
    """매일 자동 ON — paused 상태 리셋.

    08:30 또는 09:00 직전 호출. trackers/monitored 도 함께 초기화.
    """
    session.paused = False
    session.force_on = False  # 어제 /on 한 상태가 다음날까지 살지 않도록 리셋
    session.monitored.clear()
    session.trackers.clear()
    session.last_payloads.clear()


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
