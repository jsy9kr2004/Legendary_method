"""실시간 모니터링 워커 (M6).

스케줄러가 5초 간격으로 `dashboard_tick()` 호출 → 한 사이클 처리:
    1) 거래대금 50위 fetch + 주도섹터/주도주 식별 (v1)
    2) 주도주 자동 갱신 → 모니터링 종목 업데이트 (메시지 send/delete)
    3) 각 monitored 종목별 4지표 fetch + 가속배율 계산
    4) 메시지 렌더 → editMessageText (푸시 X)
    5) 상태 머신 step → Alert 시 새 메시지 + 푸시 (TRANSITION/GRACE/이탈/강한부상)

별도 thread `command_poll_loop`: getUpdates long polling — 사용자 명령 수신.

I/O 분리:
    - state.py: pure 상태
    - render.py: pure 메시지 빌더
    - intraday_realtime.py: KIS API 호출
    - 본 모듈: 통합/오케스트레이션
"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any

import pandas as pd
from loguru import logger

from src.dashboard.render import render_monitor_message
from src.dashboard.state import (
    Alert,
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
from src.jongbae.config_thresholds import (
    GRACE_PERIOD_SECONDS,
    LEADING_SECTOR_TOP_N,
    LEADING_STOCK_TOP_PER_SECTOR,
    TRANSITION_TURNOVER_RATIO,
)
from src.jongbae.leading_theme import (
    identify_early_morning_leaders,
    score_leading_sectors,
)
from src.jongbae.momentum import (
    compute_accel_ratio,
    is_exit_signal,
    is_strong_rise,
    is_transition_candidate,
    short_trend_sparkline,
)
from src.notify.telegram import (
    delete_message,
    edit_message,
    get_updates,
    send_message_single,
)
from src.notify.telegram_bot import apply_command, parse_command


def _send_alert(token: str, chat_id: str, alert: Alert) -> None:
    """Alert을 별도 메시지로 발송 (편집 X, 푸시 ON)."""
    send_message_single(token, chat_id, alert.text, parse_mode=None)


def _send_or_edit_monitor(
    token: str,
    chat_id: str,
    session: MonitoringSession,
    code: str,
    text: str,
    message_ids: dict[str, int],
) -> None:
    """종목 메시지: 최초면 새로 발송 (푸시 1회), 이후엔 edit."""
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

    호출 빈도: 5초 (스케줄러 IntervalTrigger).

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
    # 시세 변동이 없는 시간대(주말/장 외)엔 카드가 정적으로 유지될 뿐 동작은
    # 정상.

    # 1) 시장 스냅샷 + 주도섹터/주도주
    snapshot = fetch_volume_rank(client, top_n=LEADING_SECTOR_TOP_N, master_df=master_df)
    if snapshot.empty:
        return

    sectors = score_leading_sectors(snapshot, theme_mapping_df)
    leaders = identify_early_morning_leaders(
        snapshot, sectors, top_per_theme=LEADING_STOCK_TOP_PER_SECTOR,
    )

    # 2) 자동 모니터링 갱신 — 추가/제거 시 메시지 send/delete
    prev_codes = {c for c, m in session.monitored.items() if m.source.value == "auto"}
    changes = session.update_auto_leaders(leaders, now)
    for ch in changes:
        logger.info(f"[모니터링] {ch}")
    new_codes = {c for c, m in session.monitored.items() if m.source.value == "auto"}
    for dropped in prev_codes - new_codes:
        _delete_monitor_message(token, chat_id, dropped, message_ids)

    # 3) 각 monitored 종목별 4지표 fetch + 렌더 + 편집
    leader_codes = {l["code"]: l for l in leaders}
    snap_by_code = {str(r["code"]): r.to_dict() for _, r in snapshot.iterrows()}

    for code, monitored in list(session.monitored.items()):
        snap_row = snap_by_code.get(code)
        # 보조지표 — 실패해도 None 으로 진행
        bars = fetch_minute_bars(client, code) if client else pd.DataFrame()
        accel = compute_accel_ratio(bars) if not bars.empty else float("nan")
        recent_value = (
            int(bars.tail(5)["trading_value"].sum()) if not bars.empty else 0
        )
        ccnl = fetch_ccnl_strength(client, code) if client else None
        asking = fetch_asking_price(client, code) if client else None
        investor = fetch_investor_flow(client, code) if client else None
        sparkline = short_trend_sparkline(bars, n_recent=6)

        # GRACE 잔여 시간 계산 (자동 종목만 의미 있음)
        grace_remaining = None
        for tracker in session.trackers.values():
            if tracker.state == LeaderState.GRACE and tracker.candidate_code == code:
                if tracker.state_entered_at is not None:
                    elapsed = (now - tracker.state_entered_at).total_seconds()
                    grace_remaining = max(0, int(GRACE_PERIOD_SECONDS - elapsed))
                break

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
        )
        _send_or_edit_monitor(token, chat_id, session, code, text, message_ids)

        # 강한 부상 / 자금 이탈 알림 (별도 푸시)
        if accel == accel:  # not NaN
            if is_strong_rise(accel, recent_value):
                alert = Alert(
                    kind="strong_rise",
                    code=code,
                    text=(
                        f"⚡ [강한 부상] {now.strftime('%H:%M:%S')}\n"
                        f"{monitored.name} ({code}) 가속배율 {accel:.1f}배 "
                        f"(임계 10배 초과)\n"
                        f"분봉 거래대금 {recent_value/1e8:.0f}억"
                    ),
                )
                _send_alert(token, chat_id, alert)
            elif is_exit_signal(accel):
                alert = Alert(
                    kind="exit",
                    code=code,
                    text=(
                        f"⚠️ [자금 이탈 경보] {now.strftime('%H:%M:%S')}\n"
                        f"{monitored.name} ({code}) 가속배율 {accel:.2f} "
                        f"(직전 30분 평균 대비 -{(1 - accel) * 100:.0f}%)\n"
                        f"주도력 약화 — 매도 검토"
                    ),
                )
                _send_alert(token, chat_id, alert)

    # 4) 상태 머신 step (섹터별 주도주 교체)
    for sector_info in sectors:
        sector = sector_info["theme"]
        sector_codes = sector_info.get("codes", [])
        if not sector_codes:
            continue
        # 섹터 내 회전율 1위 = a1
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
            # TRANSITION 검사 — 가속배율은 a2 분봉 fetch 가 필요한데 비용 크다
            # 보수적으로 a2 의 회전율비만 보고 후속 검사는 분봉 모니터링 종목만
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
        alert = session.step_tracker(sector, a1, a2, a2_check, now)
        if alert is not None:
            _send_alert(token, chat_id, alert)


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
