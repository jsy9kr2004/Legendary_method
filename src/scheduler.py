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
from datetime import date, datetime
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
from src.ops.error_log import record_error
from src.report.decision import (
    build_decision_report,
    load_decision_candidates,
    save_decision_candidates,
    save_decision_report,
    split_messages,
)
from src.report.event import build_limit_up_alert_from_quote
from src.report.periodic import build_periodic_report, save_periodic_report

_SNAPSHOT_TIMES = ["11:00", "13:00", "14:00", "14:50"]

_POLL_INTERVAL_SEC = int(os.getenv("LIMIT_UP_POLL_INTERVAL_SEC", "60"))
_WATCH_TOP_N = int(os.getenv("LIMIT_UP_WATCH_TOP_N", "30"))


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
                # 사후 레포트가 읽도록 일자별 에러 로그에 기록
                settings = kwargs.get("settings") or _find_settings(args)
                if settings is not None:
                    try:
                        record_error(settings.data_dir, label, str(e))
                    except Exception:  # noqa: BLE001
                        pass
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


def _find_settings(args: tuple) -> Settings | None:
    for a in args:
        if isinstance(a, Settings):
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
        record_error(settings.data_dir, f"스냅샷 {label}", "데이터 없음 (휴장일 또는 API 오류)")
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
        _send_decision_report(df, settings, dispatcher, dt, client=client)
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
def _send_morning(settings: Settings, dispatcher: Dispatcher,
                  client: KISClient | None = None) -> None:
    """09:30 모닝 레포트 발송.

    market_stats 는 KIS 지수 API 로 자동 채움 (M5.5+). client 가 None 이면 빈 dict.
    holdings 는 사용자 입력 메커니즘 미구현 — 빈 리스트.
    """
    from src.report.morning import build_morning_report
    dt = now_kst()
    market_stats: dict[str, Any] = {}
    if client is not None:
        try:
            from src.data.index import compute_market_stats
            market_stats = compute_market_stats(client)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[모닝] market_stats 조회 실패: {e}")
            record_error(settings.data_dir, "모닝", f"market_stats 조회 실패: {e}")
    holdings: list[dict[str, Any]] = []  # TODO: 보유 입력 메커니즘
    report = build_morning_report(market_stats, holdings, dt)
    dispatcher.send_morning(report)


@_business_day_only("사후")
def _send_afterhours(settings: Settings, dispatcher: Dispatcher,
                     client: KISClient | None = None) -> None:
    """16:00 사후 레포트 텔레그램 발송."""
    from src.data.snapshot import list_snapshots
    from src.ops.error_log import format_error_lines, read_errors
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
        "errors": [],  # 발송 직전에 channel 누적분 + 자기 자신 실패 합쳐 채움
    }
    market_stats: dict[str, Any] = {}
    if client is not None:
        try:
            from src.data.index import compute_market_stats
            market_stats = compute_market_stats(client)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[사후] market_stats 조회 실패: {e}")
            record_error(settings.data_dir, "사후", f"market_stats 조회 실패: {e}")
    candidates = load_decision_candidates(settings.data_dir, today)
    afterhours_quotes: list[dict[str, Any]] = []
    if client is not None and candidates:
        from src.data.afterhours_quotes import fetch_afterhours_quotes
        try:
            codes = [str(c.get("code", "")).zfill(6) for c in candidates if c.get("code")]
            afterhours_quotes = fetch_afterhours_quotes(client, codes)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[사후] 시간외 단일가 조회 실패: {e}")
            record_error(settings.data_dir, "사후", f"시간외 단일가 조회 실패: {e}")
    data_status["errors"] = format_error_lines(read_errors(settings.data_dir, today))
    report = build_afterhours_report(
        candidates=candidates,
        afterhours_quotes=afterhours_quotes,
        data_status=data_status,
        report_dt=dt,
        market_stats=market_stats,
    )
    dispatcher.send_afterhours(report)


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


def _today_volume_ratio(
    daily_ohlcv,
    code: str,
    today: date,
    today_volume: int,
    window: int = 20,
) -> float | None:
    """후보 종목의 오늘 거래량 비율 = today_volume / 직전 N일 평균.

    Returns:
        float (배수) 또는 None — 일봉 데이터 부족(<5일)/0 평균 시.
    """
    if today_volume <= 0 or daily_ohlcv is None or daily_ohlcv.empty:
        return None
    own = daily_ohlcv[(daily_ohlcv["code"] == code) & (daily_ohlcv["date"] < today)]
    own = own.sort_values("date").tail(window)
    if len(own) < 5:
        return None
    avg = own["volume"].mean()
    if avg <= 0 or avg != avg:
        return None
    return float(today_volume / avg)


def _send_decision_report(
    snapshot_df,
    settings: Settings,
    dispatcher: Dispatcher,
    dt: datetime,
    client: KISClient | None = None,
) -> None:
    """14:50 결정 레포트 ★."""
    theme_df = read_naver_themes(settings.data_dir)
    daily_ohlcv = read_daily_ohlcv(settings.data_dir)

    # 시장 국면 한 줄 (강세장 가정 점검)
    market_stats: dict[str, Any] = {}
    market_regime_by_date: dict[Any, bool] = {}
    if client is not None:
        try:
            from src.data.index import KOSPI_CODE, compute_market_stats, fetch_index_daily
            market_stats = compute_market_stats(client)
            kospi_daily = fetch_index_daily(client, KOSPI_CODE, days=252)
            from src.jongbae.historical import market_regime_timeline
            market_regime_by_date = market_regime_timeline(kospi_daily)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[결정] market_stats/regime 조회 실패: {e}")
            record_error(settings.data_dir, "결정", f"market_stats/regime 조회 실패: {e}")
    today_strong_market = market_stats.get("kospi_above_ma200")

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
        vol_ratio = _today_volume_ratio(daily_ohlcv, code, today, int(row.get("volume", 0)))
        layers = historical_4layer(
            daily_ohlcv,
            today_close_pos=cp,
            today=today,
            today_strong_market=today_strong_market,
            market_regime_by_date=market_regime_by_date or None,
            today_volume_ratio=vol_ratio,
        )
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

    # 14:50 시그널 (호가/체결/투자자) — 표시만, Kelly에 반영 X (자작 가중합 금지)
    if client is not None:
        from src.data.intraday_realtime import (
            fetch_asking_price,
            fetch_ccnl_strength,
            fetch_investor_flow,
        )
        for c in candidates_with_stats:
            code = str(c.get("code", "")).zfill(6)
            signals: dict[str, Any] = {}
            try:
                ap = fetch_asking_price(client, code)
                if ap:
                    signals["asking_price"] = ap
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[결정] {code} 호가 조회 실패: {e}")
            try:
                cs = fetch_ccnl_strength(client, code)
                if cs:
                    signals["ccnl_strength"] = cs
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[결정] {code} 체결강도 조회 실패: {e}")
            try:
                inv = fetch_investor_flow(client, code)
                if inv:
                    signals["investor_flow"] = inv
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[결정] {code} 투자자 매매 조회 실패: {e}")
            if signals:
                c["intraday_signals"] = signals

    report = build_decision_report(leading, candidates_with_stats, dt, market_stats=market_stats)
    save_decision_report(report, settings.data_dir, dt)
    save_decision_candidates(candidates_with_stats, settings.data_dir, dt)
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
        args=[settings, dispatcher, client],
        id="morning",
        name="모닝 레포트",
        misfire_grace_time=600,
    )

    # 09:00~10:30 장초반 모니터링은 M6 대시보드 잡(아래 _dashboard_*)이 담당.
    # 이전 _early_morning_check (60초 간격 변화감지) 는 dashboard tick(5초)
    # + 상태 머신 + editMessageText 로 대체됨.
    from apscheduler.triggers.interval import IntervalTrigger

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
        args=[settings, dispatcher, client],
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
