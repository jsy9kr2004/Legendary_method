"""실시간 모니터링 워커 (M6).

스케줄러가 2초 간격으로 `dashboard_tick()` 호출 → 한 사이클 처리:
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

import os
import threading
from datetime import datetime
from time import perf_counter
from typing import Any

import pandas as pd
from loguru import logger

from src.dashboard.parallel_fetch import StockBundle, fetch_bundles_parallel
from src.dashboard.render import build_monitor_payload, render_monitor_message
from src.dashboard.state import (
    LeaderState,
    MonitoredStock,
    MonitoringSession,
)
from src.data.intraday import compute_market_breadth, compute_turnover, fetch_volume_rank
from src.data.intraday_realtime import (
    fetch_asking_price,
    fetch_ccnl_strength,
    fetch_investor_flow,
    fetch_minute_bars,
)
from src.data.tick_log import (
    TickLogRow,
    append_tick_log,
    build_tick_log_row,
)
from src.scalping.score.candle import is_weak_candle, latest_completed_candle
from src.scalping.score.thresholds import (
    GRACE_PERIOD_SECONDS,
    LEADING_SECTOR_TOP_N,
    LEADING_STOCK_TOP_PER_SECTOR,
    RISING_MIN_SCORE,
    TICK_DURATION_WARN_SEC,
    TRANSITION_TURNOVER_RATIO,
)
from src.scalping.score.divergence import compute_divergence
from src.scalping.exit.triggers import (
    Holding,
    compute_c_signal_states,
    evaluate_triggers,
    load_holdings,
)
from src.scalping.score.grader import GraderSnapshot, calculate_buy_score
from src.scalping.score.method_label import classify_method
from src.scalping.signals.mean_reversion import analyze_minute_bars
from src.common.theme import (
    identify_early_morning_leaders,
    identify_rising_candidates,
    score_leading_sectors,
    select_leaders_and_candidates,
)
from src.scalping.score.accel import (
    compute_accel_ratio,
    compute_minute_ma,
    compute_vwap,
    price_vs_ma_pct,
    price_vs_vwap_pct,
    short_trend_sparkline,
)
from src.scalping.score.vp import VPSeries
from src.notify.telegram import (
    delete_message,
    edit_message,
    get_updates,
    send_message_single,
)
from src.notify.telegram_bot import apply_command, parse_command


def _prev_day_volume(daily_ohlcv: pd.DataFrame | None, code: str) -> float:
    """종목별 가장 최근 일봉의 거래량 (round 32, P2-2 wiring).

    Buy.Score.d volume_ratio_vs_prev_day 분모. 데이터 없으면 NaN.
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


def _prev_day_close(daily_ohlcv: pd.DataFrame | None, code: str) -> float:
    """종목별 가장 최근 일봉 종가. daily_return 계산용. 없으면 NaN."""
    if daily_ohlcv is None or daily_ohlcv.empty:
        return float("nan")
    df = daily_ohlcv[daily_ohlcv["code"].astype(str) == code]
    if df.empty:
        return float("nan")
    df = df.sort_values("date")
    v = df.iloc[-1].get("close")
    if v is None or v != v or float(v) <= 0:
        return float("nan")
    return float(v)


def _synthesize_snap_row(
    code: str,
    name: str,
    bars: pd.DataFrame,
    market_cap: float,
    prev_close: float,
) -> dict[str, Any] | None:
    """거래대금 50위 밖 종목용 합성 snap_row.

    수동/보유 종목이 50위 밖이면 fetch_volume_rank 의 snap_by_code 에 없어
    render 가 "가격/회전율: —" 만 출력. 분봉(bars) + master_df(market_cap) +
    daily_ohlcv(prev_close) 만으로 price/trading_value/turnover/daily_return
    재구성. rank 는 None (50위 밖이라 순위 미표시).

    bars 비어있거나 last close ≤ 0 이면 None 반환 → 호출자가 snap_row=None
    유지하면 render 는 기존대로 "—" 처리.
    """
    if bars is None or bars.empty or "close" not in bars.columns:
        return None
    last_close_raw = bars.iloc[-1]["close"]
    if last_close_raw is None or last_close_raw != last_close_raw or float(last_close_raw) <= 0:
        return None
    last_close = float(last_close_raw)

    day_trading_value = 0
    if "trading_value" in bars.columns:
        s = bars["trading_value"].sum()
        if s == s and s > 0:
            day_trading_value = int(s)

    high_val = last_close
    low_val = last_close
    if "high" in bars.columns:
        h = bars["high"].max()
        if h == h and h > 0:
            high_val = float(h)
    if "low" in bars.columns:
        lo = bars["low"].min()
        if lo == lo and lo > 0:
            low_val = float(lo)

    turnover_pct = compute_turnover(day_trading_value, int(market_cap)) if market_cap > 0 else float("nan")
    daily_return_pct = (
        (last_close / prev_close - 1.0) * 100.0
        if prev_close == prev_close and prev_close > 0 else None
    )

    # int 캐스팅 — snap_by_code 원본 row 와 동일 dtype 유지 (render 의 `{price:,}원`
    # 같은 포맷이 float 이면 "5,000.0원" 으로 출력되는 것 방지).
    prev_close_int = int(prev_close) if prev_close == prev_close and prev_close > 0 else 0
    return {
        "rank": None,
        "code": code,
        "name": name,
        "price": int(last_close),
        "prev_close": prev_close_int,
        "daily_return": daily_return_pct,
        "intraday_high": int(high_val),
        "intraday_low": int(low_val),
        "volume": 0,
        "trading_value": day_trading_value,
        "is_limit_up": False,
        "market_cap": int(market_cap) if market_cap > 0 else 0,
        "turnover": turnover_pct if turnover_pct == turnover_pct else None,
    }


def _evaluate_rising_funnel(
    stage1_candidates: list[dict[str, Any]],
    snap_by_code: dict[str, dict[str, Any]],
    tick_cache: dict[str, dict[str, Any]],
    daily_ohlcv: pd.DataFrame | None = None,
    limit_up_hit_times: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """부상 후보 Buy.Score 풀스코어 평가 — round 40 (병렬 fetch 분리 후).

    Stage 0+1 (snapshot 무료 필터 + 회전율 상위 N) 통과 후보 dict 리스트와, 호출자가
    fetch_bundles_parallel 로 미리 채워둔 tick_cache 를 받아 Buy.Score 점수만 계산.
    score ≥ RISING_MIN_SCORE 통과 종목 반환.

    round 37: Stage 2/3 hard-fail 폐지 — 모든 후보를 Buy.Score 풀스코어 단일 컷 통과.
    round 40: KIS fetch 가 본 함수 밖 fetch_bundles_parallel 로 이동. 본 함수는 순수
    CPU — score 계산만. tick_cache 가 bars/accel_5m/accel_1m/candle/ccnl/vp/asking/
    investor 키를 미리 가지고 있다고 가정 (없으면 NaN-safe 로 처리).

    Args:
        stage1_candidates: identify_rising_candidates 결과.
        snap_by_code: snapshot 의 code → row 매핑 (rank/가격/회전율 등).
        tick_cache: code → bundle-derived dict (호출자가 prefill). bars/accel/candle/
            ccnl/vp/asking/investor 부분 가능. 부재 시 가산점 0.

    Returns:
        Buy.Score score ≥ RISING_MIN_SCORE 통과 종목 dict 리스트 (원 cand dict +
        "buy_score" / "buy_grade" / "buy_reasons" 키 추가). 점수 내림차순.
    """
    if not stage1_candidates:
        return []

    n_stage1 = len(stage1_candidates)
    scored_candidates: list[dict[str, Any]] = []
    for cand in stage1_candidates:
        code = cand["code"]
        cache = tick_cache.get(code)
        if cache is None:
            # tick_cache 미존재 — fetch 실패했거나 합집합에서 빠진 경우. skip (예전
            # "분봉 빈 응답" 과 같은 처리).
            continue
        bars = cache.get("bars")
        if bars is None or bars.empty:
            continue
        scored_candidates.append(cand)

    if not scored_candidates:
        return []

    # ── Buy.Score 풀스코어 평가 (호가 + 투자자 + 종합 점수) ───────────────────────
    out: list[dict[str, Any]] = []
    for cand in scored_candidates:
        code = cand["code"]
        snap = snap_by_code.get(code, {})
        cache = tick_cache[code]
        asking = cache.get("asking")
        bid_ask = float("nan")
        if asking is not None:
            v = asking.get("bid_ask_ratio")
            if v is not None and v == v:
                bid_ask = float(v)
        # 당일 고점 대비 거리 (필수조건 Buy.Position)
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

        accel_1m_val = cache.get("accel_1m", float("nan"))
        # 일중 등락률 (R14l 횡보 정점 페널티용) — snap_row.daily_return
        daily_return_raw = snap.get("daily_return")
        daily_return_for_grade = (
            float(daily_return_raw) if daily_return_raw is not None and daily_return_raw == daily_return_raw
            else float("nan")
        )
        gsnap = GraderSnapshot(
            volume_turnover_rank=int(snap.get("rank") or 0) or None,
            vol_accel_1m=accel_1m_val if accel_1m_val == accel_1m_val else float("nan"),
            vol_accel_5m=cache.get("accel_5m", float("nan")),
            candle=cache.get("candle"),
            vp=cache.get("vp", float("nan")),
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
            daily_return_pct=daily_return_for_grade,
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
        f"[funnel] stage1={n_stage1} → 풀스코어 평가={len(scored_candidates)} → "
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


def _maybe_push_mr_strong_alert(
    monitored: Any,
    now: datetime,
    token: str,
    chat_id: str,
) -> None:
    """단저단고 STRONG 푸시 알림 — kind 변경/STRONG 재진입 시 1회 send (2026-05-29).

    조건:
        - monitored.mr_grade == "STRONG"
        - mr_sigB OR mr_sigS True
        - 직전 push 한 kind 와 현재 kind 가 다름 (mr_alert_kind 추적)
    STRONG 벗어나면 mr_alert_kind 를 None 으로 reset → 다음 STRONG 진입 시 재 push.

    /on /off 와 무관 (paused 일 때도 push). 카드 갱신(edit)과 별개의 send 알림.
    """
    if monitored.mr_grade != "STRONG":
        # STRONG 벗어남 — alert 추적 reset
        monitored.mr_alert_kind = None
        return

    if monitored.mr_sigB:
        new_kind = "단저"
    elif monitored.mr_sigS:
        new_kind = "단고"
    else:
        # STRONG 영역 이지만 트리거 발화 X — 알림 X (사용자가 명시한 "단저/단고 strong"만)
        return

    if monitored.mr_alert_kind == new_kind:
        # 같은 kind 연속 발화 — 중복 push 방지
        return

    # 새 kind — push + alert_kind 갱신
    name = monitored.name or monitored.code
    rank_str = f" #{monitored.sector_rank}" if monitored.sector_rank else ""
    if monitored.sector_role == "leader":
        source_label = f"⭐ 주도주{rank_str}"
    elif monitored.sector_role == "candidate":
        source_label = f"🌟 주도주 후보{rank_str}"
    elif monitored.is_manual:
        source_label = "🔵 수동"
    else:
        source_label = "💎 보유"
    sector_str = monitored.surface_sector_name or " / ".join(monitored.themes) or "—"
    kind_emoji = "🟢" if new_kind == "단저" else "🔴"
    reason = monitored.mr_reason or "—"
    ts_label = now.strftime("%H:%M:%S")
    text = (
        f"🚨 단저단고 STRONG — {source_label}\n"
        f"{name} ({monitored.code})\n"
        f"{kind_emoji} {new_kind} score {monitored.mr_score:.1f} — {reason}\n"
        f"테마: {sector_str} | {ts_label}"
    )
    try:
        send_message_single(token, chat_id, text, parse_mode=None)
        monitored.mr_alert_kind = new_kind
    except Exception as e:
        logger.warning(f"{monitored.code} 단저단고 STRONG push 실패: {e}")


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
    send_telegram_cards: bool = True,
) -> None:
    """한 사이클의 모니터링 처리.

    호출 빈도: 2초 (스케줄러 IntervalTrigger, scheduler.py).

    Args:
        session: 공유 세션 상태.
        message_ids: code → telegram message_id (편집 추적).
        client: KISClient.
        master_df: 종목 마스터 (주도섹터 후보 필터용 turnover 적재).
        theme_mapping_df: 네이버 테마 매핑.
        daily_ohlcv: 신고가 판정용 long format. None 가능.
        token: Telegram 봇 토큰.
        chat_id: 채팅 ID.
        send_telegram_cards: False 면 카드 send/edit/delete 모두 skip. PWA
            payload / KIS fetch / tick_log / 명령 응답은 그대로. 사용자가 PWA
            만 보면서 tick 시간 단축이 목적인 경우. settings.monitoring_
            telegram_cards_enabled (env MONITORING_TELEGRAM_CARDS_ENABLED) 와 매핑.
        now: 현재 시각 (KST).
    """
    if session.paused:
        return
    # in_monitoring_window 가드 폐지 (round 18) — /on 으로 24h 임의 시점에 켤
    # 수 있음. 평일/주말, 정규장 외 시간이어도 KIS 시세를 받아 카드 표시.
    # force_on 은 명시적으로 사용자가 /on 한 상태를 추적할 뿐 가드는 아님.

    # tick 계측 — 사용자 인지 지연 = tick 소요시간 (스케줄러 max_instances=1 +
    # coalesce=True 라 tick 이 interval 보다 길면 다음 trigger 는 병합·드롭됨).
    # 4구간으로 쪼개 보틀넥 파악: snapshot / funnel / monitored 루프 / 비-monitored 로깅.
    t_start = perf_counter()

    # round 22: 보유 종목 메타데이터 로드 (Exit.Triggers 트리거 평가용).
    # /buy /sell 가 holdings.json 을 갱신하므로 매 tick 디스크 로드. 종목 수는
    # 단타 운영상 한 자릿수라 비용 무시 가능.
    holdings: dict[str, Holding] = load_holdings()

    # 1) 시장 스냅샷 + 주도섹터/주도주
    snapshot = fetch_volume_rank(client, top_n=LEADING_SECTOR_TOP_N, master_df=master_df)
    if snapshot.empty:
        return
    t_snapshot = perf_counter()

    # 2026-05-29 단저단고 surface 룰 — universe 풀 → 주도섹터 → 주도주/후보.
    # Step (1) 거래대금 30위 ∩ 회전율 30위 교집합 = scalping universe 풀.
    # Step (2) 좁혀진 풀로 z-score 주도섹터 식별 (Top 3).
    # Step (3) 주도섹터 내 거래대금 1위 ∩ 회전율 1위 = 주도주, 2위 == 2위 = 후보.
    from src.common.universe import intersect_universe
    _rank_series = pd.Series(
        {str(r["code"]): r.get("rank") for _, r in snapshot.iterrows() if pd.notna(r.get("rank"))}
    )
    _turnover_series = pd.Series(
        {str(r["code"]): r.get("turnover") for _, r in snapshot.iterrows() if pd.notna(r.get("turnover"))}
    )
    universe_codes: set[str] = intersect_universe(_rank_series, _turnover_series)
    if universe_codes:
        universe_snapshot = snapshot[snapshot["code"].astype(str).isin(universe_codes)].copy()
    else:
        # 교집합 빈 set (장 초반 데이터 부족 등) → fallback 으로 전체 snapshot 사용.
        # 카드는 빈 상태가 되겠지만 데몬은 계속 동작.
        universe_snapshot = snapshot

    sectors = score_leading_sectors(universe_snapshot, theme_mapping_df)
    leaders, candidates = select_leaders_and_candidates(universe_snapshot, sectors)
    auto_entries = leaders + candidates

    # LEGACY Buy.Score 부상 후보 funnel — 기본 OFF (단저단고 primary).
    # LEGACY_RISING_FUNNEL=1 시만 부활 (back-out 용).
    if os.getenv("LEGACY_RISING_FUNNEL", "0") == "1":
        rising_stage1 = identify_rising_candidates(
            snapshot, theme_mapping_df=theme_mapping_df,
        )
        auto_codes_set = {e["code"] for e in auto_entries}
        rising_stage1 = [c for c in rising_stage1 if c["code"] not in auto_codes_set]
    else:
        rising_stage1 = []

    logger.debug(
        f"[모니터링] snapshot={len(snapshot)}, sectors={len(sectors)}, "
        f"leaders={len(leaders)}, candidates={len(candidates)}, "
        f"rising_stage1={len(rising_stage1)}"
    )

    # round 35: multi-flag 모델. monitored 에서 빠진 종목만 message 삭제.
    # flag 변화만으로는 카드 유지 (manual/hold 가 다른 flag 보충).
    prev_monitored_codes = set(session.monitored.keys())

    # 2) 자동 주도주/후보 — is_auto flag + sector_role/surface_sector_name 갱신
    changes = session.update_auto_leaders(auto_entries, now)
    for ch in changes:
        logger.info(f"[모니터링] {ch}")

    snap_by_code = {str(r["code"]): r.to_dict() for _, r in snapshot.iterrows()}

    # 시장 폭(breadth) — 국면 게이지 (P2-7). top_n 스냅샷의 상승종목 비율. tick 1회 계산.
    tick_breadth = compute_market_breadth(snap_by_code)

    # 2026-05-29: 옛 MONITOR_MR_UNIVERSE 단저단고 분석 게이트 폐기.
    # universe 30 ∩ 30 풀은 이미 위에서 surface 룰의 첫 단계로 적용됨 → monitored 자체가
    # 좁혀짐. 권외 종목은 사용자가 직접 수동 등록 / 보유로만 진입 (분석 항상 수행).
    mr_universe: set[str] = set()

    # /buy CODE (가격 인자 생략) UX 를 위해 최근 시세를 세션에 노출 (round 20).
    for code, row in snap_by_code.items():
        price = row.get("price")
        if price is not None and price == price and price > 0:
            session.last_prices[code] = float(price)

    # 2a) round 40: KIS fetch 병렬화. funnel 후보 ∪ monitored ∪ holdings 합집합을
    # ThreadPoolExecutor 로 동시 fetch — 4 API × N 종목 의 직렬 라운드를 제거.
    # 듀얼 키 합산 ~40 req/s 한도 안에서 limiter 가 자연 throttle (rate_limit.py).
    # tick_cache 는 본 tick 안에서만 살아있는 buffer (cache 아님 — fresh).
    fetch_codes: set[str] = {c["code"] for c in rising_stage1}
    fetch_codes.update(session.monitored.keys())
    fetch_codes.update(holdings.keys())
    bundles: dict[str, StockBundle] = fetch_bundles_parallel(client, fetch_codes)
    tick_cache: dict[str, dict[str, Any]] = {}
    for code, bundle in bundles.items():
        bars = bundle.bars
        accel_5m = compute_accel_ratio(bars) if not bars.empty else float("nan")
        accel_1m = (
            compute_accel_ratio(bars, recent_minutes=1, baseline_minutes=10)
            if not bars.empty else float("nan")
        )
        candle = latest_completed_candle(bars) if not bars.empty else None
        vp_value: float = float("nan")
        if bundle.ccnl is not None:
            vp = bundle.ccnl.get("ccnl_strength")
            if vp is not None and vp == vp:
                vp_value = float(vp)
        tick_cache[code] = {
            "bars": bars,
            "accel_5m": accel_5m,
            "accel_1m": accel_1m,
            "candle": candle,
            "ccnl": bundle.ccnl,
            "vp": vp_value,
            "asking": bundle.asking,
            "investor": bundle.investor,
        }
    t_fetch = perf_counter()

    # 2b) 부상 후보 Buy.Score 풀스코어 — LEGACY_RISING_FUNNEL=1 시만 활성.
    # 2026-05-29 단저단고 패러다임 전환 후 기본 OFF — rising_scored 항상 빈 list,
    # update_rising_candidates([]) 호출로 기존 is_rising flag 종목 모두 해제.
    if os.getenv("LEGACY_RISING_FUNNEL", "0") == "1":
        rising_scored = _evaluate_rising_funnel(
            rising_stage1, snap_by_code, tick_cache,
            daily_ohlcv=daily_ohlcv,
            limit_up_hit_times=session.limit_up_hit_times,
        )
    else:
        rising_scored = []
    rising_changes = session.update_rising_candidates(rising_scored, now)
    for ch in rising_changes:
        logger.info(f"[부상] {ch}")
    t_score = perf_counter()

    # 2c) 보유 종목 surface — holdings.json 의 모든 code 가 monitored 에 있어야 카드 표시.
    # _apply_buy 도 이걸 호출하지만, 데몬 재시작 / external holdings 변경 대비.
    # name 해상도: snap (거래대금 50위) → master_df (KRX 전종목) → code.
    # master_meta_by_code 는 50위 밖 manual/hold 종목의 회전율 계산용 market_cap 도
    # 동시에 노출 — 아래 monitored 루프 _synthesize_snap_row 가 사용.
    master_name_by_code: dict[str, str] = {}
    master_market_cap_by_code: dict[str, float] = {}
    if (
        master_df is not None
        and not master_df.empty
        and "code" in master_df.columns
    ):
        codes_str = master_df["code"].astype(str)
        if "name" in master_df.columns:
            master_name_by_code = dict(zip(codes_str, master_df["name"].astype(str)))
        if "market_cap" in master_df.columns:
            master_market_cap_by_code = dict(zip(
                codes_str,
                master_df["market_cap"].fillna(0).astype(float),
            ))

    # 테마 보완 dict — 한 종목이 여러 테마에 속할 수 있어 list[str]. monitored 풀의
    # m.themes 가 비어있으면 (수동/보유 entry) 매 tick 채움. auto/rising 은 이미
    # leaders/rising dict 의 themes 가 채워서 들어옴.
    theme_map_by_code: dict[str, list[str]] = {}
    if (
        theme_mapping_df is not None
        and not theme_mapping_df.empty
        and "code" in theme_mapping_df.columns
        and "theme" in theme_mapping_df.columns
    ):
        grouped = theme_mapping_df.groupby(
            theme_mapping_df["code"].astype(str)
        )["theme"].apply(list)
        theme_map_by_code = {
            c: [str(t) for t in themes] for c, themes in grouped.items()
        }

    for h_code in holdings.keys():
        if h_code not in session.monitored:
            h_name = (
                snap_by_code.get(h_code, {}).get("name")
                or master_name_by_code.get(h_code)
                or h_code
            )
            session.ensure_held_stock(h_code, h_name, now)

    # 수동 등록 종목 metadata 보완 — add_manual 이 name=code / themes=[] 로 박은
    # entry 들을 매 tick snap → master_df / theme_mapping_df fallback 으로 갱신.
    # 거래대금 50위 밖 중소형주를 수동 등록한 경우 "[🔵 수동] 123456 (123456)" /
    # "테마: —" / "가격/회전율: —" 로 누락되던 버그 해결 (가격/회전율은 monitored
    # 루프의 snap_row 합성에서 별도 처리).
    for mcode, m in session.monitored.items():
        if m.name == mcode:
            resolved = (
                snap_by_code.get(mcode, {}).get("name")
                or master_name_by_code.get(mcode)
            )
            if resolved:
                m.name = resolved
        if not m.themes:
            themes = theme_map_by_code.get(mcode)
            if themes:
                m.themes = themes

    # 2d) prune — flag 없고 보유도 아닌 종목 제거 + message 삭제
    holding_codes_set = set(holdings.keys())
    pruned = session.prune_empty(holding_codes_set)
    for msg in pruned:
        logger.info(f"[모니터링] {msg}")
    new_monitored_codes = set(session.monitored.keys())
    if send_telegram_cards:
        for dropped in prev_monitored_codes - new_monitored_codes:
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

    # Phase 1: tick-level 시그널 로깅 (`src/data/tick_log.py`).
    # monitored 종목 = 풀 시그널, Stage 0 통과 비-monitored 종목 = snap + cache 데이터.
    tick_log_rows: list[TickLogRow] = []

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
        # 거래대금 50위 밖 수동/보유 종목: snap 에 없어 가격/거래대금/회전율이
        # 카드에 "—" 로만 출력됨. bars + master_df(market_cap) + daily_ohlcv(prev_close)
        # 로 합성. 합성 실패 (bars 비거나 close ≤ 0) 시 snap_row=None 유지 → render
        # 기존 동작 ("가격/회전율: —").
        if snap_row is None:
            synth = _synthesize_snap_row(
                code=code,
                name=monitored.name or code,
                bars=bars,
                market_cap=master_market_cap_by_code.get(code, 0.0),
                prev_close=_prev_day_close(daily_ohlcv, code),
            )
            if synth is not None:
                snap_row = synth
                snap_by_code[code] = synth  # 하단 grader / payload 도 동일 데이터 사용
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

        # round 22: VP 시계열 push + 5MA / 1MA / 20MA 산출 (체결강도 라인 보강 + Exit.Triggers C1)
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

        # 청산 시그널 (Exit.Triggers C 그룹) — 보유든 감시든 모든 카드에 표시.
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
                logger.info(f"[Exit.Triggers 트리거] {code} {ev.kind} — {ev.text}")
            # A/B/C 전체 표시는 triggers_fired (sticky) 기반.
            trigger_states = dict.fromkeys([
                "A1_stop_price", "A2_stop_bar_low", "A3_stop_ma", "A4_stop_time",
                "A5_eod_ma_break",
                "P1_take_profit_1", "P2_take_profit_2", "P3_trailing",
                "E1_vp_below_100", "E2_bearish_divergence", "E3_vol_drain",
                "E4_bearish_candle", "E5_vi_failure",
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

        # round 33/35: 모든 모니터링 모드(AUTO/MANUAL/HOLD/RISING) 에 Buy.Score 매수 점수 계산.
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
        # 일중 등락률 (R14l) — snap_row.daily_return
        dr_raw = snap_row_d.get("daily_return")
        dr_for_grade = float(dr_raw) if dr_raw is not None and dr_raw == dr_raw else float("nan")
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
            daily_return_pct=dr_for_grade,
        )
        sc = calculate_buy_score(grade_snap)
        monitored.buy_score = sc.score
        monitored.buy_grade = sc.grade
        monitored.buy_reasons = list(sc.reasons)

        # P1-4 (docs §11.1) — 매매법 분류. 가설 D2 재설계 (2026-05-27): pullback 가중치
        # 재학습 + bid_ask / volratio / vp_5ma_delta 신규 입력 추가. 자세한 ritual 은
        # data/journal/2026-05-26.md 토론 #4.
        _ml = classify_method(
            dist_high_pct=dist_high if dist_high == dist_high else float("nan"),
            daily_return_pct=dr_for_grade,
            vol_accel_5m=accel if accel == accel else float("nan"),
            vol_accel_1m=accel_1m if accel_1m == accel_1m else float("nan"),
            vp=vp_now,
            candle_bullish=(getattr(candle_for_trig, "type", None) == "bullish"),
            candle_upper_wick=getattr(candle_for_trig, "upper_wick_ratio", float("nan")) if candle_for_trig else float("nan"),
            candle_lower_wick=getattr(candle_for_trig, "lower_wick_ratio", float("nan")) if candle_for_trig else float("nan"),
            price_vs_ma5_pct=ma5_pct_g,
            volume_ratio_vs_prev_day=vol_ratio_g,
            divergence_bullish=bool(getattr(divergence_state, "bullish", False)),
            bid_ask_ratio=bid_ask if bid_ask == bid_ask else float("nan"),
            vp_5ma_delta=getattr(divergence_state, "vp_5ma_delta", float("nan")),
        )
        monitored.setup_label = _ml.setup
        monitored.setup_score_breakout = _ml.score_breakout
        monitored.setup_score_pullback = _ml.score_pullback
        monitored.setup_chase_warning = _ml.chase_warning

        # 단저단고 시그널 (docs/scalping-redesign-2026-05-27.md, 2026-05-27).
        # KIS 1분봉 bars 그대로 사용 (≥25봉 필요). dry-run 표시 — 라이브 매매
        # 영향 X. analyze_minute_bars 는 NaN-safe + 미달 시 (False, False, None).
        # universe 게이트 (MONITOR_MR_UNIVERSE=1 시) — 거래대금 30위 ∩ 회전율
        # 30위 통과 종목만 분석. 빈 set 이면 게이트 무효 (모든 종목).
        # 단 auto/rising/manual/holding 종목 (사용자 명시 관심) 은 항상 분석 —
        # universe 게이트는 자동 추가 종목 풀 좁힘 용도, 사용자 관심 종목 막지 X.
        _user_pinned = (
            monitored.is_auto or monitored.is_rising or monitored.is_manual
            or (holdings is not None and code in holdings)
        )
        _in_mr_universe = (not mr_universe) or (code in mr_universe) or _user_pinned
        try:
            if _in_mr_universe:
                mr_b, mr_s, mr_r, mr_score, mr_g = analyze_minute_bars(bars)
            else:
                mr_b, mr_s, mr_r, mr_score, mr_g = False, False, None, 0.0, "NEUTRAL"
        except Exception as e:
            logger.warning(f"{code} mean_reversion 분석 실패: {e}")
            mr_b, mr_s, mr_r, mr_score, mr_g = False, False, None, 0.0, "NEUTRAL"
        monitored.mr_sigB = mr_b
        monitored.mr_sigS = mr_s
        monitored.mr_reason = mr_r
        monitored.mr_score = mr_score
        monitored.mr_grade = mr_g
        # 단저단고 히스토리 (2026-05-29) — sigB/sigS 발화 시 카드 히스토리 섹션용 push.
        # 연속 동일 kind 는 score/reason 만 갱신 (FIFO 3 max).
        if mr_b:
            monitored.push_mr_event(now, "단저", float(mr_score), mr_r)
        if mr_s:
            monitored.push_mr_event(now, "단고", float(mr_score), mr_r)

        # 단저단고 STRONG 푸시 알림 (2026-05-29) — /on/off 와 무관, 자동 6종+수동+보유만.
        # 같은 kind 연속 발화는 1회만 push. STRONG 벗어났다 재진입 시 재 push.
        # MR_STRONG_ALERT=0 으로 끌 수 있음 (default ON).
        if os.getenv("MR_STRONG_ALERT", "1") == "1" and token and chat_id:
            _maybe_push_mr_strong_alert(monitored, now, token, chat_id)

        if tick_breadth:
            monitored.market_breadth_up_frac = tick_breadth["breadth_up_frac"]
            monitored.market_n_up5 = tick_breadth["n_up5"]

        # round 36 후속: 수급 Δ — KIS 갱신 주기 자동 추종 (윈도우 고정 X).
        # 값이 이전과 다른 시점에만 새 Δ 기록, 같은 응답이면 기존 Δ + 늘어난 elapsed.
        investor_delta = session.update_investor_delta(code, investor, now)

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
            investor_delta=investor_delta,
        )
        if send_telegram_cards:
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
            investor_delta=investor_delta,
        )

        # Phase 1: tick-level 로깅 — monitored 종목 풀 시그널.
        tick_log_rows.append(build_tick_log_row(
            now=now,
            code=code,
            name=monitored.name or code,
            monitored=monitored,
            snap_row=snap_row,
            bars_present=not bars.empty,
            accel_5m=accel,
            accel_1m=accel_1m,
            recent_bar_value=recent_value,
            last_bar_value=last_bar_value,
            candle=candle_for_trig,
            vp_now=vp_now,
            vp_5ma=vp_5ma,
            vp_1ma=vp_1ma,
            ccnl=ccnl,
            asking=asking,
            investor=investor,
            investor_delta=investor_delta,
            vwap_pct=vwap_pct_g,
            ma5_pct=ma5_pct_g,
            ma20_pct=ma20_pct_g,
            divergence=divergence_state,
            volume_ratio=vol_ratio_g,
            limit_up_hit_time=session.limit_up_hit_times.get(code),
            trigger_states=trigger_states,
            funnel_evaluated=(code in tick_cache),
            holding=holding,
            intraday_high_override=int(high_for_grade) if high_for_grade > 0 else None,
        ))

    t_monitored = perf_counter()
    monitored_count = len(session.monitored)

    # Phase 1: Stage 0 통과 중 monitored 아닌 종목 (funnel 평가 받았다 RISING 못
    # 들어가거나, 그냥 거래대금 50위 안에 있는 종목) — 사후 분석에 "이 종목 왜
    # 후보 안 떴지?" 확인용. tick_cache 에 있으면 분봉/체결강도/호가/투자자 cache
    # 활용, 없으면 snap 만.
    monitored_codes_set = set(session.monitored.keys())
    for snap_code, snap_row_extra in snap_by_code.items():
        if snap_code in monitored_codes_set:
            continue  # 이미 monitored 루프에서 로깅됨
        cached = tick_cache.get(snap_code, {})
        cached_bars = cached.get("bars")
        bars_present_x = cached_bars is not None and not cached_bars.empty
        recent_value_x: int | None = None
        last_bar_value_x: int | None = None
        if bars_present_x:
            try:
                recent_value_x = int(cached_bars.tail(5)["trading_value"].sum())
                last_bar_value_x = int(cached_bars.tail(1)["trading_value"].iloc[0])
            except (KeyError, ValueError):
                pass
        fake_monitored = MonitoredStock(
            code=snap_code,
            name=snap_row_extra.get("name") or snap_code,
            added_at=now,
        )
        # 비-monitored 도 cached bars 가 있으면 일중 최고가 fallback (KIS stck_hgpr=0 회피)
        intraday_high_x: int | None = None
        if bars_present_x and cached_bars is not None and "high" in cached_bars.columns:
            try:
                h = float(cached_bars["high"].max())
                if h == h and h > 0:
                    intraday_high_x = int(h)
            except (KeyError, ValueError):
                pass
        tick_log_rows.append(build_tick_log_row(
            now=now,
            code=snap_code,
            name=fake_monitored.name,
            monitored=fake_monitored,
            snap_row=snap_row_extra,
            bars_present=bars_present_x,
            accel_5m=cached.get("accel_5m", float("nan")),
            accel_1m=cached.get("accel_1m", float("nan")),
            recent_bar_value=recent_value_x,
            last_bar_value=last_bar_value_x,
            candle=cached.get("candle"),
            vp_now=cached.get("vp", float("nan")),
            vp_5ma=float("nan"),  # session.vp_series 는 monitored 만 누적
            vp_1ma=float("nan"),
            ccnl=cached.get("ccnl"),
            asking=cached.get("asking"),
            investor=cached.get("investor"),
            investor_delta=None,
            vwap_pct=float("nan"),  # 비-monitored 는 grader 계산 X
            ma5_pct=float("nan"),
            ma20_pct=float("nan"),
            divergence=None,
            volume_ratio=float("nan"),
            limit_up_hit_time=session.limit_up_hit_times.get(snap_code),
            trigger_states={},
            funnel_evaluated=bool(cached),
            holding=None,
            intraday_high_override=intraday_high_x,
        ))

    append_tick_log(tick_log_rows, now)

    # M7 PWA: monitored 에서 빠진 종목 페이로드 정리 (tick 끝)
    stale_codes = set(session.last_payloads.keys()) - set(session.monitored.keys())
    for stale in stale_codes:
        session.last_payloads.pop(stale, None)
    session.last_payload_ts = now

    # tick 계측 종합 — INFO 로 매 tick 출력. 1 tick > 2초 (interval) 면 warning —
    # 다음 trigger 가 coalesce 되어 실효 갱신 주기가 길어진다는 신호.
    # round 40: funnel fetch 가 fetch_bundles_parallel 로 이동 → 라벨 재설계
    # (snap / fetch / score / monitored / log). fetch = 합집합 4×N KIS 호출 (병렬),
    # score = funnel Buy.Score 풀스코어 CPU only.
    t_end = perf_counter()
    tick_total = t_end - t_start
    dt_snapshot = t_snapshot - t_start
    dt_fetch = t_fetch - t_snapshot
    dt_score = t_score - t_fetch
    dt_monitored = t_monitored - t_score
    dt_log = t_end - t_monitored
    per_card = (dt_monitored / monitored_count) if monitored_count else 0.0
    fetched_count = len(bundles)
    per_fetch = (dt_fetch / fetched_count) if fetched_count else 0.0
    msg = (
        f"[tick] total={tick_total*1000:.0f}ms "
        f"snap={dt_snapshot*1000:.0f}ms "
        f"fetch={dt_fetch*1000:.0f}ms ({fetched_count}종목, 종목당 {per_fetch*1000:.0f}ms) "
        f"score={dt_score*1000:.0f}ms "
        f"monitored={dt_monitored*1000:.0f}ms ({monitored_count}종목, "
        f"종목당 {per_card*1000:.0f}ms) log={dt_log*1000:.0f}ms"
    )
    if tick_total > TICK_DURATION_WARN_SEC:
        logger.warning(f"{msg} — interval(2초) 초과, 다음 trigger coalesce 위험")
    else:
        logger.info(msg)


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
