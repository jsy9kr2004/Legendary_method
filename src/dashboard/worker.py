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
    identify_rising_candidates,
    score_leading_sectors,
)
from src.jongbae.momentum import (
    compute_accel_ratio,
    is_exit_signal,
    is_one_min_exit,
    is_one_min_rise,
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


def _send_alert(
    token: str, chat_id: str, alert: Alert, session: MonitoringSession | None = None
) -> None:
    """Alert을 별도 메시지로 발송 (편집 X, 푸시 ON).

    Alert 가 발송되면 모니터링 메시지가 위로 밀려나므로 다음 tick 에서 재배치하도록
    session.reposition_pending 을 set 한다. session 인자가 None 이면 skip.
    """
    send_message_single(token, chat_id, alert.text, parse_mode=None)
    if session is not None:
        session.reposition_pending = True


def _send_or_edit_monitor(
    token: str,
    chat_id: str,
    session: MonitoringSession,
    code: str,
    text: str,
    message_ids: dict[str, int],
    reposition: bool = False,
) -> None:
    """종목 메시지 발송/편집.

    - 최초 (message_ids 에 없음): send 후 id 기록.
    - 기존 (message_ids 에 있음):
        - reposition=False → edit (조용한 갱신, 화면 위치 유지).
        - reposition=True  → delete + 새로 send (silent, 푸시 X).
            최하단으로 위치 이동. 새 alert 직후 화면 최하단에 두기 위함.
    """
    msg_id = message_ids.get(code)
    if msg_id is not None and not reposition:
        edit_message(token, chat_id, msg_id, text, parse_mode=None)
        return
    if msg_id is not None and reposition:
        # delete 실패해도 silent send 시도. 옛 메시지 ID 도 더 이상 추적 X.
        delete_message(token, chat_id, msg_id)
        message_ids.pop(code, None)
    resp = send_message_single(
        token, chat_id, text, parse_mode=None,
        disable_notification=reposition,  # 재배치는 silent
    )
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
    # force_on 은 명시적으로 사용자가 /on 한 상태를 추적할 뿐 가드는 아님.

    # 1) 시장 스냅샷 + 주도섹터/주도주
    snapshot = fetch_volume_rank(client, top_n=LEADING_SECTOR_TOP_N, master_df=master_df)
    if snapshot.empty:
        return

    sectors = score_leading_sectors(snapshot, theme_mapping_df)
    leaders = identify_early_morning_leaders(
        snapshot, sectors, top_per_theme=LEADING_STOCK_TOP_PER_SECTOR,
    )
    # 부상 후보(거래대금 급증): leaders 와 중복 제거. 모니터링이 아닌 RISING 분기로
    # 별도 처리 — 첫 알림 + 2분 유지 + 갱신. 사용자가 매매 결정하면 /add 로 승격.
    rising = identify_rising_candidates(
        snapshot, theme_mapping_df=theme_mapping_df,
    )
    leader_codes_set = {l["code"] for l in leaders}
    rising = [c for c in rising if c["code"] not in leader_codes_set]

    # 2) 자동 모니터링 갱신 (주도주만) — 추가/제거 시 메시지 send/delete
    prev_auto_codes = {c for c, m in session.monitored.items() if m.source.value == "auto"}
    changes = session.update_auto_leaders(leaders, now)
    for ch in changes:
        logger.info(f"[모니터링] {ch}")
    new_auto_codes = {c for c, m in session.monitored.items() if m.source.value == "auto"}
    for dropped in prev_auto_codes - new_auto_codes:
        _delete_monitor_message(token, chat_id, dropped, message_ids)

    # 2b) 부상 후보 (RISING) 갱신 — 신규 진입은 ⚡ 알림, 만료된 건 제거
    rising_changes = session.update_rising_candidates(rising, now)
    for code in session.prune_expired(now):
        _delete_monitor_message(token, chat_id, code, message_ids)
        logger.info(f"[부상] {code} 만료 — RISING 메시지 제거")
    if rising_changes:
        alert = Alert(
            kind="rising",
            code=None,
            text="⚡ [부상 후보 — 거래대금 급증]\n" + "\n".join(rising_changes)
                 + "\n\n매매 결정 시 /add <코드> 로 모니터링 승격",
        )
        _send_alert(token, chat_id, alert, session=session)

    # auto pool 변경 / 부상 신규 진입 / 만료 / 직전 tick alert → 모니터링 재배치
    if changes or rising_changes:
        session.reposition_pending = True
    reposition_now = session.reposition_pending
    session.reposition_pending = False  # consume

    # 3) 각 monitored 종목별 4지표 fetch + 렌더 + 편집
    leader_codes = {l["code"]: l for l in leaders}
    snap_by_code = {str(r["code"]): r.to_dict() for _, r in snapshot.iterrows()}

    for code, monitored in list(session.monitored.items()):
        snap_row = snap_by_code.get(code)
        # 보조지표 — 실패해도 None 으로 진행
        bars = fetch_minute_bars(client, code) if client else pd.DataFrame()
        # 5분봉 가속 (recent=5, baseline=30) — 기존
        accel = compute_accel_ratio(bars) if not bars.empty else float("nan")
        recent_value = (
            int(bars.tail(5)["trading_value"].sum()) if not bars.empty else 0
        )
        # 1분봉 가속 (recent=1, baseline=10) — 갑작스러운 한 봉 급증 감지
        accel_1m = (
            compute_accel_ratio(bars, recent_minutes=1, baseline_minutes=10)
            if not bars.empty else float("nan")
        )
        last_bar_value = (
            int(bars.tail(1)["trading_value"].iloc[0]) if not bars.empty else 0
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
            accel_ratio_1m=accel_1m if accel_1m == accel_1m else None,
            last_bar_value=last_bar_value or None,
        )
        _send_or_edit_monitor(
            token, chat_id, session, code, text, message_ids,
            reposition=reposition_now,
        )

        # 강한 부상 / 자금 이탈 알림 (별도 푸시)
        # 디바운스: 같은 종목·같은 kind 가 매 5초 tick 마다 반복 푸시되지 않도록
        # session.last_alert_accel 로 edge-trigger.
        #   첫 진입: 푸시 + 기록
        #   재진입: 마지막 푸시 대비 의미있게 더 악화/강화될 때만 푸시
        #     - exit:        accel ≤ last - 0.1 (더 떨어짐)
        #     - strong_rise: accel ≥ last + 1.0 (더 급증)
        #   복귀(정상권): exit 의 경우 accel ≥ 1.0 시 state 삭제 → 다음 이탈 시 1st 알림
        if accel == accel:  # not NaN
            if is_strong_rise(accel, recent_value):
                key = (code, "strong_rise")
                last = session.last_alert_accel.get(key)
                if last is None or accel >= last + 1.0:
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
                    _send_alert(token, chat_id, alert, session=session)
                    session.last_alert_accel[key] = accel
                # strong_rise 가 가라앉으면 (accel < 임계) 자연스럽게 다음 분기로 이동
            elif is_exit_signal(accel):
                key = (code, "exit")
                last = session.last_alert_accel.get(key)
                if last is None or accel <= last - 0.1:
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
                    _send_alert(token, chat_id, alert, session=session)
                    session.last_alert_accel[key] = accel
            else:
                # 정상권 (강한 부상도 자금 이탈도 아님) — 복귀로 간주, state 클리어
                # 다음에 다시 트리거되면 1st 알림으로 푸시.
                session.last_alert_accel.pop((code, "exit"), None)
                session.last_alert_accel.pop((code, "strong_rise"), None)

        # 1분봉 알림 — 5분봉보다 lag 짧음. first-mover / 급감 시그널.
        if accel_1m == accel_1m:  # not NaN
            if is_one_min_rise(accel_1m, last_bar_value):
                key = (code, "one_min_rise")
                last = session.last_alert_accel.get(key)
                if last is None or accel_1m >= last + 1.0:
                    alert = Alert(
                        kind="one_min_rise",
                        code=code,
                        text=(
                            f"🟢⚡ [1분봉 부상] {now.strftime('%H:%M:%S')}\n"
                            f"{monitored.name} ({code}) 1분봉 가속 {accel_1m:.1f}배\n"
                            f"최근 1분 거래대금 {last_bar_value/1e8:.1f}억"
                        ),
                    )
                    _send_alert(token, chat_id, alert, session=session)
                    session.last_alert_accel[key] = accel_1m
            elif is_one_min_exit(accel_1m):
                key = (code, "one_min_exit")
                last = session.last_alert_accel.get(key)
                if last is None or accel_1m <= last - 0.1:
                    alert = Alert(
                        kind="one_min_exit",
                        code=code,
                        text=(
                            f"🔴⚠ [1분봉 급감] {now.strftime('%H:%M:%S')}\n"
                            f"{monitored.name} ({code}) 1분봉 가속 {accel_1m:.2f}\n"
                            f"직전 10분 평균 대비 -{(1 - accel_1m) * 100:.0f}%"
                        ),
                    )
                    _send_alert(token, chat_id, alert, session=session)
                    session.last_alert_accel[key] = accel_1m
            else:
                session.last_alert_accel.pop((code, "one_min_rise"), None)
                session.last_alert_accel.pop((code, "one_min_exit"), None)

        # 호가 색상 전환 alert (🟢↔🔴) — 매수↔매도 강세 역전은 매매 시그널.
        if asking is not None:
            ratio = asking.get("bid_ask_ratio")
            if ratio is not None and ratio == ratio:
                if ratio >= 1.5:
                    color = "green"
                elif ratio <= 0.67:
                    color = "red"
                else:
                    color = "yellow"
                prev_color = session.last_asking_color.get(code)
                # 🟢 → 🔴 (매수강세 → 매도강세): 매도 시그널
                if prev_color == "green" and color == "red":
                    _send_alert(token, chat_id, Alert(
                        kind="asking_flip_sell",
                        code=code,
                        text=(
                            f"🔴 [호가 역전 — 매도 우세] {now.strftime('%H:%M:%S')}\n"
                            f"{monitored.name} ({code})\n"
                            f"매수/매도 비율 {ratio:.2f}배 (직전 매수강세 → 매도강세)\n"
                            f"매도 검토"
                        ),
                    ), session=session)
                # 🔴 → 🟢 (매도강세 → 매수강세): 진입 시그널
                elif prev_color == "red" and color == "green":
                    _send_alert(token, chat_id, Alert(
                        kind="asking_flip_buy",
                        code=code,
                        text=(
                            f"🟢 [호가 역전 — 매수 우세] {now.strftime('%H:%M:%S')}\n"
                            f"{monitored.name} ({code})\n"
                            f"매수/매도 비율 {ratio:.1f}배 (직전 매도강세 → 매수강세)"
                        ),
                    ), session=session)
                session.last_asking_color[code] = color

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
            _send_alert(token, chat_id, alert, session=session)


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
    session.last_alert_accel.clear()
    session.last_asking_color.clear()


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
