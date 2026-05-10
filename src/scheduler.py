"""장중 스케줄러 데몬.

매일 자동 실행할 모든 잡을 한 곳에서 관리:
    08:30  글로벌 상태 리셋
    09:30  모닝 레포트 발송 (텔레그램)
    11:00  스냅샷 수집 + 정기 추적 레포트
    13:00  스냅샷 수집 + 정기 추적 레포트
    14:00  스냅샷 수집 + 정기 추적 레포트
    14:50  스냅샷 수집 + 결정 레포트 ★ 발송
    16:00  사후 레포트 이메일 발송
    interval (60s)  09:05~15:25  상한가 신규 진입 폴링 + 즉시 알림 ★

실행:
    python -m src.scheduler

휴장일 가드:
    각 잡 진입부에서 is_business_day() 체크 → 주말/공휴일이면 스킵.

환경변수:
    LIMIT_UP_POLL_INTERVAL_SEC: 상한가 폴링 간격 (초, 기본 60)
    LIMIT_UP_WATCH_TOP_N: 거래대금 상위 몇 개 종목을 감시할지 (기본 30)
"""
from __future__ import annotations

import os
import signal
import sys
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any, Callable

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

from src.calendar_kr import is_business_day
from src.config import KST, Settings, load_settings, now_kst
from src.data.intraday import fetch_volume_rank
from src.data.snapshot import save_snapshot
from src.data.storage import read_daily_ohlcv, read_naver_themes, read_stock_master
from src.jongbae.candidates import accepted_candidates, extract_candidates
from src.jongbae.historical import (
    close_position,
    historical_4layer,
    has_enough_samples,
    pick_sizing_layer,
)
from src.jongbae.leading_theme import (
    codes_in_leading_themes,
    identify_early_morning_leaders,
    identify_leading_stocks,
    identify_leading_themes,
)
from src.jongbae.limit_up import detect_new_limit_up, filter_limit_up_from_snapshot
from src.jongbae.sizing import compute_sizing
from src.kis.client import KISClient
from src.logging_setup import setup_logging
from src.notify.dispatcher import Dispatcher
from src.report.decision import build_decision_report, save_decision_report, split_messages
from src.report.event import build_limit_up_alert_from_quote
from src.report.periodic import build_periodic_report, save_periodic_report

_SNAPSHOT_TIMES = ["11:00", "13:00", "14:00", "14:50"]

_POLL_INTERVAL_SEC = int(os.getenv("LIMIT_UP_POLL_INTERVAL_SEC", "60"))
_WATCH_TOP_N = int(os.getenv("LIMIT_UP_WATCH_TOP_N", "30"))

# 9~9:30 고주파 변화감지 폴링 (장초반 주도섹터/주도주 변동에 민감 반응)
_EARLY_INTERVAL_SEC = int(os.getenv("EARLY_MORNING_INTERVAL_SEC", "60"))


# ── 일별 글로벌 상태 ─────────────────────────────────────────────────────────
# 매일 08:30에 _reset_state() 로 초기화.

_already_limit_up: set[str] = set()
_watch_codes: list[str] = []
_prev_leading_themes: list[dict[str, Any]] = []
_prev_leading_stocks: list[dict[str, Any]] = []

# ── M6 모니터링 대시보드 글로벌 상태 ────────────────────────────────────────

from src.dashboard.state import MonitoringSession  # noqa: E402

_dashboard_session = MonitoringSession()
_dashboard_message_ids: dict[str, Any] = {}
_dashboard_master_df = None
_dashboard_theme_df = None
_dashboard_daily_df = None
_dashboard_command_thread: Any = None
_dashboard_command_stop: Any = None


def _reset_state() -> None:
    """매일 장 시작 전 글로벌 상태 초기화."""
    global _already_limit_up, _watch_codes, _prev_leading_themes, _prev_leading_stocks
    _already_limit_up = set()
    _watch_codes = []
    _prev_leading_themes = []
    _prev_leading_stocks = []
    logger.info("[리셋] 일별 글로벌 상태 초기화 완료")


# ── 휴장일 가드 데코레이터 ──────────────────────────────────────────────────


def _business_day_only(label: str) -> Callable:
    """잡 함수에 휴장일 가드 적용."""
    def deco(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not is_business_day(now_kst().date()):
                logger.debug(f"[{label}] 스킵 — 주말/휴장일")
                return None
            try:
                return fn(*args, **kwargs)
            except Exception as e:  # noqa: BLE001
                logger.exception(f"[{label}] 잡 오류: {e}")
                # dispatcher 가 있으면 에러 알림
                disp = kwargs.get("dispatcher") or _find_dispatcher(args)
                if disp is not None:
                    try:
                        disp.telegram_error(str(e), context=label)
                    except Exception:  # noqa: BLE001
                        pass
                return None
        return wrapper
    return deco


def _find_dispatcher(args: tuple) -> Dispatcher | None:
    for a in args:
        if isinstance(a, Dispatcher):
            return a
    return None


# ── 잡 함수 ─────────────────────────────────────────────────────────────────


@_business_day_only("스냅샷")
def _collect_snapshot(
    client: KISClient,
    settings: Settings,
    dispatcher: Dispatcher,
    label: str,
) -> None:
    """거래대금 순위 스냅샷 수집 → 저장 → 정기 레포트 발송 (14:50 외)."""
    global _watch_codes, _already_limit_up, _prev_leading_themes
    dt = now_kst()
    logger.info(f"[스냅샷] {label} 수집 시작 ({dt.strftime('%H:%M:%S')})")

    df = fetch_volume_rank(client, top_n=_WATCH_TOP_N)
    if df.empty:
        logger.warning(f"[스냅샷] {label}: 데이터 없음 (휴장일 또는 API 오류)")
        return

    save_snapshot(df, settings.data_dir, dt)
    logger.info(f"[스냅샷] {label}: {len(df)}종목 저장")

    # 감시 종목 갱신 (상한가 폴링용)
    _watch_codes = df["code"].tolist()

    # 스냅샷에서 바로 상한가 종목 체크 → 즉시 알림
    lup_df = filter_limit_up_from_snapshot(df)
    if not lup_df.empty:
        for _, row in lup_df.iterrows():
            code = str(row["code"])
            if code not in _already_limit_up:
                _already_limit_up.add(code)
                _send_limit_up_alert(row.to_dict(), settings, dispatcher, dt)

    # 14:50 은 결정 레포트로 별도 처리 (정기 레포트 발송 안 함)
    if label == "14:50":
        _send_decision_report(df, settings, dispatcher, dt)
    else:
        _send_periodic_report(df, settings, dispatcher, dt)


def _within_polling_window(dt: datetime) -> bool:
    """상한가 폴링 활성 시간대 (장중 09:05 ~ 15:25 KST)."""
    t = dt.time()
    return (t >= datetime.strptime("09:05", "%H:%M").time()
            and t <= datetime.strptime("15:25", "%H:%M").time())


@_business_day_only("상한가 폴링")
def _poll_limit_up(
    client: KISClient,
    settings: Settings,
    dispatcher: Dispatcher,
) -> None:
    """감시 종목 상한가 폴링 → 신규 진입 시 즉시 알림."""
    global _already_limit_up
    dt = now_kst()
    if not _within_polling_window(dt):
        return
    if not _watch_codes:
        return

    new_entries, _already_limit_up = detect_new_limit_up(
        client, _watch_codes, _already_limit_up
    )
    if not new_entries:
        return

    for entry in new_entries:
        _send_limit_up_alert(entry, settings, dispatcher, dt)


@_business_day_only("모닝")
def _send_morning(settings: Settings, dispatcher: Dispatcher) -> None:
    """09:30 모닝 레포트 발송.

    market_stats 자동 수집은 별도 모듈 필요(TODO). v0 는 텍스트 placeholder
    수준으로만 발송 — Zeta 직관 판단 영역.
    """
    from src.report.morning import build_morning_report
    dt = now_kst()
    market_stats: dict[str, Any] = {}  # TODO: KIS 인덱스 조회로 채우기
    holdings: list[dict[str, Any]] = []  # TODO: 보유 입력 메커니즘
    report = build_morning_report(market_stats, holdings, dt)
    dispatcher.send_morning(report)


def _within_early_morning(dt: datetime) -> bool:
    """장초반 고주파 변화감지 시간대 (09:00 ≤ t < 10:00 KST).

    사용자 요청에 따라 09:00~10:00 1시간 동안 동작.
    """
    t = dt.time()
    return (t >= datetime.strptime("09:00", "%H:%M").time()
            and t < datetime.strptime("10:00", "%H:%M").time())


@_business_day_only("장초반 변화감지")
def _early_morning_check(
    client: KISClient,
    settings: Settings,
    dispatcher: Dispatcher,
) -> None:
    """09:00~10:00 동안 주도섹터 / 주도주 변화 감지 + 신규 상한가 알림.

    감지 대상:
        - 주도섹터(테마) 변화: 신규 진입/탈락
        - 주도주(주도테마 내 first-mover 상한가 종목) 변화
    비주도테마 상한가는 별도 limit-up 폴링이 처리.
    """
    global _watch_codes, _already_limit_up, _prev_leading_themes, _prev_leading_stocks
    dt = now_kst()
    if not _within_early_morning(dt):
        return

    df = fetch_volume_rank(client, top_n=_WATCH_TOP_N)
    if df.empty:
        return

    _watch_codes = df["code"].tolist()
    theme_df = read_naver_themes(settings.data_dir)
    leading = identify_leading_themes(df, theme_df)
    # 고주파 모니터링용 주도주 (pre-limit-up 진입 후보):
    # 주도섹터 내 거래대금 상위 + 상승률 상위 종목.
    leaders = identify_early_morning_leaders(df, leading, top_per_theme=2)

    # 신규 상한가는 그대로 limit-up 이벤트로 발송 (주도주 여부와 별개)
    lup_df = filter_limit_up_from_snapshot(df)
    if not lup_df.empty:
        for _, row in lup_df.iterrows():
            code = str(row["code"])
            if code not in _already_limit_up:
                _already_limit_up.add(code)
                _send_limit_up_alert(row.to_dict(), settings, dispatcher, dt)

    from src.report.periodic import build_early_morning_alert
    alert = build_early_morning_alert(
        df, leading, _prev_leading_themes,
        leaders, _prev_leading_stocks, dt,
    )
    dispatcher.send_early_morning(alert)
    _prev_leading_themes = leading
    _prev_leading_stocks = leaders


@_business_day_only("사후")
def _send_afterhours(settings: Settings, dispatcher: Dispatcher) -> None:
    """16:00 사후 레포트 이메일 발송."""
    from src.data.snapshot import list_snapshots
    from src.report.afterhours import build_afterhours_report

    dt = now_kst()
    today = dt.date()

    snaps = list_snapshots(settings.data_dir, today)
    daily = read_daily_ohlcv(settings.data_dir)
    today_rows = daily[daily["date"] == today] if not daily.empty else daily
    data_status = {
        "ohlcv_updated": not today_rows.empty,
        "ohlcv_count": int(today_rows["code"].nunique()) if not today_rows.empty else 0,
        "snapshots_collected": len(snaps),
        "errors": [],
    }
    report = build_afterhours_report(
        candidates=[],
        afterhours_quotes=[],
        data_status=data_status,
        report_dt=dt,
    )
    dispatcher.email_afterhours(report, today.strftime("%Y-%m-%d"))


# ── 레포트 빌더 헬퍼 ────────────────────────────────────────────────────────


def _send_periodic_report(
    snapshot_df,
    settings: Settings,
    dispatcher: Dispatcher,
    dt: datetime,
) -> None:
    """정기 추적 레포트 (11/13/14)."""
    global _prev_leading_themes
    theme_df = read_naver_themes(settings.data_dir)
    leading = identify_leading_themes(snapshot_df, theme_df)
    new_lup_rows = []  # 위 _collect_snapshot 에서 이미 즉시 알림 됨; 정기 레포트엔 빈 리스트
    report = build_periodic_report(
        snapshot_df, leading, _prev_leading_themes, new_lup_rows, dt
    )
    save_periodic_report(report, settings.data_dir, dt)
    label = dt.strftime("%H:%M")
    dispatcher.send_periodic(report, label=f"추적-{label}")
    _prev_leading_themes = leading


def _send_decision_report(
    snapshot_df,
    settings: Settings,
    dispatcher: Dispatcher,
    dt: datetime,
) -> None:
    """14:50 결정 레포트 ★."""
    theme_df = read_naver_themes(settings.data_dir)
    daily_ohlcv = read_daily_ohlcv(settings.data_dir)

    leading = identify_leading_themes(snapshot_df, theme_df)
    leading_codes = codes_in_leading_themes(leading)
    candidates_df = extract_candidates(snapshot_df, leading_codes)
    accepted = accepted_candidates(candidates_df)

    candidates_with_stats: list[dict[str, Any]] = []
    today = dt.date()
    for _, row in accepted.iterrows():
        code = str(row["code"])
        close = int(row.get("price", 0))
        high = int(row.get("intraday_high", close))
        low_raw = int(row.get("intraday_low", 0) or 0)
        low = low_raw if low_raw > 0 else int(close * 0.85)
        cp = close_position(
            open_p=float(row.get("prev_close", close)),
            high=float(high),
            low=float(low),
            close=float(close),
        )
        layers = historical_4layer(daily_ohlcv, today_close_pos=cp, today=today)
        sizing_layer_name, sizing_stats = pick_sizing_layer(layers)

        # R4 (c): 모든 layer 가 n<5 면 후보 제외
        if not has_enough_samples(sizing_stats):
            logger.info(f"[결정] {code} R4(c) 표본부족 제외 (n<5)")
            continue

        themes = (
            theme_df[theme_df["code"] == code]["theme"].tolist()
            if not theme_df.empty
            else []
        )
        c: dict[str, Any] = row.to_dict()
        c["themes"] = themes
        c["layers"] = layers
        c["sizing_layer"] = sizing_layer_name
        c["sizing_stats"] = sizing_stats
        candidates_with_stats.append(c)

    sizing_results = compute_sizing(candidates_with_stats)
    for i, c in enumerate(candidates_with_stats):
        c["sizing"] = {
            "kelly":  sizing_results["kelly"][i],
            "sharpe": sizing_results["sharpe"][i],
            "equal":  sizing_results["equal"][i],
        }

    report = build_decision_report(leading, candidates_with_stats, dt)
    save_decision_report(report, settings.data_dir, dt)
    parts = split_messages(report)
    dispatcher.send_decision(parts)


def _send_limit_up_alert(
    entry: dict[str, Any],
    settings: Settings,
    dispatcher: Dispatcher,
    dt: datetime,
) -> None:
    """상한가 진입 즉시 알림."""
    code = str(entry.get("code", ""))
    theme_df = read_naver_themes(settings.data_dir)
    themes = (
        theme_df[theme_df["code"] == code]["theme"].tolist()
        if not theme_df.empty
        else []
    )
    daily = read_daily_ohlcv(settings.data_dir)

    layer2_stats: dict[str, Any] = {}
    if not daily.empty:
        try:
            layers = historical_4layer(daily, today_close_pos=1.0, today=dt.date())
            layer2_stats = layers.get("layer2", {})
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[상한가 알림] historical 계산 실패 ({code}): {e}")

    alert = build_limit_up_alert_from_quote(entry, themes, layer2_stats, dt)
    dispatcher.send_limit_up_event(alert)
    logger.info(
        f"[상한가 진입] {entry.get('name', code)}({code}) "
        f"현재가={entry.get('price')} 수익률={entry.get('daily_return', 0):.1f}%"
    )


# ── 스케줄러 본체 ───────────────────────────────────────────────────────────


# ── M6 대시보드 잡 함수 ──────────────────────────────────────────────────────


@_business_day_only("모니터링 시작")
def _dashboard_start(client: KISClient, settings: Settings) -> None:
    """09:00 매일 자동 ON — 데이터 캐시 로드 + paused 리셋 + 명령 thread 시작."""
    global _dashboard_master_df, _dashboard_theme_df, _dashboard_daily_df
    global _dashboard_command_thread, _dashboard_command_stop

    from src.dashboard.worker import reset_daily, start_command_thread

    logger.info("[M6] 모니터링 대시보드 시작")
    try:
        _dashboard_master_df = read_stock_master(settings.data_dir)
    except FileNotFoundError:
        logger.warning("[M6] 종목 마스터 미존재 — turnover 계산 불가, 후보 필터 X")
        _dashboard_master_df = None
    try:
        _dashboard_theme_df = read_naver_themes(settings.data_dir)
    except FileNotFoundError:
        logger.warning("[M6] 네이버 테마 미존재 — 주도섹터 식별 불가")
        _dashboard_theme_df = None
    try:
        _dashboard_daily_df = read_daily_ohlcv(settings.data_dir)
    except FileNotFoundError:
        _dashboard_daily_df = None

    reset_daily(_dashboard_session)
    _dashboard_message_ids.clear()

    # 사용자 명령 long polling thread (이미 동작 중이면 stop 후 재시작)
    if _dashboard_command_stop is not None:
        _dashboard_command_stop.set()
    if settings.telegram_bot_token and settings.telegram_chat_id:
        _dashboard_command_thread, _dashboard_command_stop = start_command_thread(
            _dashboard_session,
            settings.telegram_bot_token,
            settings.telegram_chat_id,
        )
    else:
        logger.warning("[M6] 텔레그램 미설정 — 명령 thread 미시작")


@_business_day_only("모니터링 tick")
def _dashboard_tick_job(client: KISClient, settings: Settings) -> None:
    """5초마다 호출 — 모니터링 한 사이클. 시간창은 worker 안에서 가드."""
    if _dashboard_master_df is None or _dashboard_theme_df is None:
        return
    if not (settings.telegram_bot_token and settings.telegram_chat_id):
        return

    from src.dashboard.worker import dashboard_tick
    dashboard_tick(
        session=_dashboard_session,
        message_ids=_dashboard_message_ids,
        client=client,
        master_df=_dashboard_master_df,
        theme_mapping_df=_dashboard_theme_df,
        daily_ohlcv=_dashboard_daily_df,
        token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
        now=now_kst(),
    )


@_business_day_only("모니터링 종료")
def _dashboard_stop(settings: Settings) -> None:
    """10:30 종료 — 모니터링 메시지 정리 + 명령 thread stop."""
    global _dashboard_command_thread, _dashboard_command_stop

    from src.dashboard.worker import cleanup_messages

    logger.info("[M6] 모니터링 대시보드 종료")
    if settings.telegram_bot_token and settings.telegram_chat_id:
        cleanup_messages(
            token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
            session=_dashboard_session,
            message_ids=_dashboard_message_ids,
        )
    if _dashboard_command_stop is not None:
        _dashboard_command_stop.set()
    _dashboard_command_thread = None
    _dashboard_command_stop = None


def _make_scheduler() -> BlockingScheduler:
    return BlockingScheduler(timezone=KST)


def run() -> None:
    settings = load_settings()
    setup_logging(settings)

    logger.info("장중 스케줄러 시작")
    logger.info(f"  API 모드: {settings.kis_api_mode}")
    logger.info(f"  DRY_RUN:  {settings.dry_run}")
    logger.info(f"  DATA_DIR: {settings.data_dir}")
    logger.info(f"  상한가 폴링 간격: {_POLL_INTERVAL_SEC}초")

    client = KISClient(settings)
    dispatcher = Dispatcher(settings)
    scheduler = _make_scheduler()

    # 매일 08:30 글로벌 상태 리셋 (월~금)
    scheduler.add_job(
        _reset_state,
        trigger=CronTrigger(day_of_week="mon-fri", hour=8, minute=30),
        id="state_reset",
        name="일별 상태 리셋",
        misfire_grace_time=600,
    )

    # 09:30 모닝 레포트
    scheduler.add_job(
        _send_morning,
        trigger=CronTrigger(day_of_week="mon-fri", hour=9, minute=30),
        args=[settings, dispatcher],
        id="morning",
        name="모닝 레포트",
        misfire_grace_time=600,
    )

    # 09:00~09:30 장초반 고주파 변화감지 (테마/주도주 변동 즉시 알림)
    from apscheduler.triggers.interval import IntervalTrigger
    scheduler.add_job(
        _early_morning_check,
        trigger=IntervalTrigger(seconds=_EARLY_INTERVAL_SEC),
        args=[client, settings, dispatcher],
        id="early_morning_poll",
        name="장초반 변화감지",
        misfire_grace_time=30,
        max_instances=1,
        coalesce=True,
    )

    # 4시점 스냅샷 + 정기/결정 레포트
    for t in _SNAPSHOT_TIMES:
        hh, mm = t.split(":")
        scheduler.add_job(
            _collect_snapshot,
            trigger=CronTrigger(day_of_week="mon-fri", hour=int(hh), minute=int(mm)),
            args=[client, settings, dispatcher, t],
            id=f"snapshot_{t.replace(':', '')}",
            name=f"스냅샷+레포트 {t}",
            misfire_grace_time=300,
        )

    # 16:00 사후 레포트 (이메일)
    scheduler.add_job(
        _send_afterhours,
        trigger=CronTrigger(day_of_week="mon-fri", hour=16, minute=0),
        args=[settings, dispatcher],
        id="afterhours",
        name="사후 레포트",
        misfire_grace_time=1800,
    )

    # 상한가 폴링 — IntervalTrigger 기반, 함수 내부에서 시간창 가드.
    # start_date/end_date 미지정 → 매일 영구 동작 (이전 버그 수정).
    scheduler.add_job(
        _poll_limit_up,
        trigger=IntervalTrigger(seconds=_POLL_INTERVAL_SEC),
        args=[client, settings, dispatcher],
        id="limit_up_poll",
        name="상한가 폴링",
        misfire_grace_time=30,
        max_instances=1,
        coalesce=True,
    )

    # ── M6 모니터링 대시보드 (09:00 시작 / 10:30 종료, 5초 tick) ─────────────
    scheduler.add_job(
        _dashboard_start,
        trigger=CronTrigger(day_of_week="mon-fri", hour=9, minute=0),
        args=[client, settings],
        id="dashboard_start",
        name="모니터링 시작",
        misfire_grace_time=120,
    )
    scheduler.add_job(
        _dashboard_tick_job,
        trigger=IntervalTrigger(seconds=5),
        args=[client, settings],
        id="dashboard_tick",
        name="모니터링 tick (5s)",
        misfire_grace_time=10,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _dashboard_stop,
        trigger=CronTrigger(day_of_week="mon-fri", hour=10, minute=30),
        args=[settings],
        id="dashboard_stop",
        name="모니터링 종료",
        misfire_grace_time=300,
    )

    def _shutdown(signum, frame):
        logger.info("종료 시그널 수신 — 스케줄러 셧다운")
        scheduler.shutdown(wait=False)
        client.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        scheduler.start()
    except Exception as e:
        logger.error(f"스케줄러 오류: {e}")
        client.close()
        raise


if __name__ == "__main__":
    run()
