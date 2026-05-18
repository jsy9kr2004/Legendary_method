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
import threading
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
                # round 32 (P1-1 wiring): 도달 시각 저장 → R14c 가산점
                _dashboard_session.limit_up_hit_times[code] = dt.time()
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
        # round 32 (P1-1 wiring): 도달 시각 저장 → R14c 가산점
        code = str(entry.get("code", ""))
        if code:
            _dashboard_session.limit_up_hit_times[code] = dt.time()
        _send_limit_up_alert(entry, settings, dispatcher, dt)


@_business_day_only("종배 시초 청산 권고")
def _send_jongbae_open_exit_recommendation(
    client: KISClient,
    settings: Settings,
    dispatcher: Dispatcher,
) -> None:
    """09:01 보유 종목 시초가 청산 권고 (round 32, P3-2 wiring).

    `data/state/holdings.json` 의 보유 종목 각각에 대해 KIS 현재가 +
    `evaluate_jongbae_open_exit` 호출 → 권고 메시지 발송. **자동 주문 X**.
    """
    from src.data.intraday import fetch_quote
    from src.jongbae.exit_triggers import load_holdings
    from src.jongbae.jongbae_exit import evaluate_jongbae_open_exit

    holdings = load_holdings()
    if not holdings:
        return

    lines = ["🌅 [종배 시초가 청산 권고]"]
    any_decision = False
    for code in holdings:
        q = fetch_quote(client, code)
        if q is None:
            lines.append(f"• {code} — 시세 조회 실패")
            continue
        price = q.get("price", 0)
        prev = q.get("prev_close", 0)
        if not price or not prev or price <= 0 or prev <= 0:
            lines.append(f"• {code} — 시세 데이터 부족")
            continue
        try:
            decision = evaluate_jongbae_open_exit(
                open_price=float(price), prev_close=float(prev),
            )
        except ValueError:
            lines.append(f"• {code} — 시세 유효성 실패")
            continue
        any_decision = True
        name = q.get("name") or code
        emoji = "🟢" if decision.action == "sell_partial" else "🟡"
        lines.append(
            f"{emoji} {name}({code}) — {decision.reason} "
            f"[매도 비중 {int(decision.partial_ratio * 100)}%]"
        )

    if not any_decision:
        return  # 보유 종목 있어도 시세 모두 실패면 푸시 X
    lines.append("")
    lines.append("_자동 주문 X — 사용자가 HTS 에서 직접 청산_")
    dispatcher.send_jongbae_open_exit("\n".join(lines))


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


@_business_day_only("지수 적재")
def _compact_tick_logs_today() -> None:
    """Phase 1 후속 cron — 오늘 tick_logs/trades jsonl → parquet 변환.

    16:15 (사후 레포트 + 지수 일봉 갱신 후) 일자별 자동 변환. jsonl 은 보존
    (delete_raw=False) — 안전망. 사용자가 disk 확보 위해 수동 삭제 또는
    `python -m src.data.tick_log_compact --delete-raw` 직접 실행.
    """
    from datetime import date as _d

    from src.data.tick_log_compact import compact_tick_logs, compact_trades

    today = _d.today()
    try:
        compact_tick_logs(today, delete_raw=False)
        compact_trades(today, delete_raw=False)
    except Exception as e:  # noqa: BLE001
        logger.error(f"[tick_log_compact] 사후 변환 실패: {e}")


def _update_index_daily_job(settings: Settings, dispatcher: Dispatcher,
                            client: KISClient | None = None) -> None:
    """16:10 KOSPI/KOSDAQ 일봉 incremental update.

    historical layer3_strong_mkt 200ma 매칭 사용 가능 날짜를 영구 누적.
    초기 1회 `python -m src.data.update_index --init` 필요.
    """
    if client is None:
        return
    from src.data.index import update_index_daily
    result = update_index_daily(client, settings.data_dir)
    total = sum(result.values())
    logger.info(f"[지수 적재] 신규 {total}건 ({result})")


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
    # KOSPI 적재본 우선 사용 → fetch fallback. 적재본은 ma200 lookback 확보용.
    market_stats: dict[str, Any] = {}
    market_regime_by_date: dict[Any, bool] = {}
    if client is not None:
        try:
            from src.data.index import KOSPI_CODE, compute_market_stats
            from src.data.index_storage import read_index_daily
            market_stats = compute_market_stats(client, data_dir=settings.data_dir)
            kospi_daily = read_index_daily(settings.data_dir, KOSPI_CODE)
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


def _load_dashboard_data(settings: Settings) -> None:
    """데이터 캐시(마스터/테마/일봉) 로드. run() 시작과 평일 09:00 cron 시 호출."""
    global _dashboard_master_df, _dashboard_theme_df, _dashboard_daily_df
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


@_business_day_only("모니터링 자동 시작")
def _dashboard_start(client: KISClient, settings: Settings) -> None:
    """평일 09:00 자동 ON — 데이터 캐시 재로드 + session.set_on().

    polling thread 는 scheduler.run() 시점에 24h 상시 가동 중. 여기서는 다시
    띄우지 않음. /off 로 끈 다음날 09:00 에 다시 자동 ON 되는 흐름.
    """
    from src.dashboard.worker import reset_daily

    logger.info("[M6] 모니터링 자동 시작 (평일 09:00)")
    if not settings.monitoring_telegram_cards_enabled:
        logger.info(
            "[M6] 텔레그램 카드 발송 비활성 (MONITORING_TELEGRAM_CARDS_ENABLED=0). "
            "PWA 대시보드만 갱신. 카드 send/edit/delete skip → tick 시간 단축."
        )
    _load_dashboard_data(settings)
    reset_daily(_dashboard_session)
    _dashboard_message_ids.clear()


def _dashboard_tick_job(client: KISClient, settings: Settings) -> None:
    """3초마다 호출 — 모니터링 한 사이클 (round 18).

    가드:
        1) session.paused 가 False (사용자가 /on 으로 켰거나 09:00 cron 으로 자동 ON)
        2) 텔레그램 설정 존재
        3) master/theme 캐시 로드됨 (없으면 force_on 시 lazy 로딩)

    평일/주말 가드 X — 사용자가 24h 임의 시점에 /on 으로 켤 수 있음 (정책).
    주말/휴장일에 켜놓아도 KIS 시세는 변동 없으니 카드는 정적으로 유지.
    """
    if _dashboard_session.paused:
        # /off 직후 첫 tick — 카드 메시지 정리 1회.
        if (
            _dashboard_session.off_cleanup_pending
            and settings.telegram_bot_token
            and settings.telegram_chat_id
        ):
            try:
                from src.dashboard.worker import cleanup_messages
                cleanup_messages(
                    token=settings.telegram_bot_token,
                    chat_id=settings.telegram_chat_id,
                    session=_dashboard_session,
                    message_ids=_dashboard_message_ids,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[M6] /off 카드 정리 실패: {e}")
            _dashboard_session.off_cleanup_pending = False
        return
    # master/theme 미로딩 상태에서 사용자가 /on (force_on) 으로 강제 켜면
    # dashboard_start 를 lazy 호출해서 데이터 로딩.
    if _dashboard_master_df is None or _dashboard_theme_df is None:
        if _dashboard_session.force_on:
            logger.info("[M6] force_on — _dashboard_start lazy 호출")
            _dashboard_start(client, settings)
    if not (settings.telegram_bot_token and settings.telegram_chat_id):
        return
    if _dashboard_master_df is None or _dashboard_theme_df is None:
        # 09:00 자동 cron 전이거나 마스터/테마 파일 미존재 — tick 미실행.
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
        send_telegram_cards=settings.monitoring_telegram_cards_enabled,
    )


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

    # M6 모니터링: 데이터 캐시 1회 로드 + 봇 명령 polling thread 24h 상시 가동.
    # daemon 시작 시점이 평일 09:00 자동 cron 전/후라면 OFF 시작 — 사용자가 /on
    # 으로 명시 ON 또는 다음 평일 09:00 cron 으로 자동 ON.
    _load_dashboard_data(settings)
    _n = now_kst()
    _auto_on_window = is_business_day(_n.date()) and (
        _n.hour == 9 or (_n.hour == 10 and _n.minute <= 30)
    )
    if not _auto_on_window:
        _dashboard_session.paused = True
    global _dashboard_command_thread, _dashboard_command_stop
    if settings.telegram_bot_token and settings.telegram_chat_id:
        from src.dashboard.worker import start_command_thread
        _dashboard_command_thread, _dashboard_command_stop = start_command_thread(
            _dashboard_session,
            settings.telegram_bot_token,
            settings.telegram_chat_id,
        )
        logger.info(
            f"[M6] 봇 명령 polling thread 24h 상시 가동 (현재 모니터링: "
            f"{'OFF' if _dashboard_session.paused else 'ON'})"
        )
    else:
        logger.warning("[M6] 텔레그램 미설정 — 명령 thread 미시작")

    # 매일 08:30 글로벌 상태 리셋 (월~금)
    scheduler.add_job(
        _reset_state,
        trigger=CronTrigger(day_of_week="mon-fri", hour=8, minute=30),
        id="state_reset",
        name="일별 상태 리셋",
        misfire_grace_time=600,
    )

    # 09:01 종배 시초가 청산 권고 (round 32, P3-2 wiring)
    # KRX 09:00 단일가 형성 직후 KIS 시세 안정화 1분 후. 보유 종목 없으면 no-op.
    scheduler.add_job(
        _send_jongbae_open_exit_recommendation,
        trigger=CronTrigger(day_of_week="mon-fri", hour=9, minute=1),
        args=[client, settings, dispatcher],
        id="jongbae_open_exit",
        name="종배 시초 청산 권고",
        misfire_grace_time=300,
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

    # 16:00 사후 레포트 (텔레그램)
    scheduler.add_job(
        _send_afterhours,
        trigger=CronTrigger(day_of_week="mon-fri", hour=16, minute=0),
        args=[settings, dispatcher, client],
        id="afterhours",
        name="사후 레포트",
        misfire_grace_time=1800,
    )

    # 16:10 지수 일봉 incremental (사후 발송 후 KOSPI/KOSDAQ 그날치 적재)
    scheduler.add_job(
        _update_index_daily_job,
        trigger=CronTrigger(day_of_week="mon-fri", hour=16, minute=10),
        args=[settings, dispatcher, client],
        id="index_daily_update",
        name="지수 일봉 업데이트",
        misfire_grace_time=3600,
    )

    # 16:15 tick_logs / trades jsonl → parquet 변환 (Phase 1 사후).
    # jsonl 매 tick append 라 운영 중 손실 ≤ 1 tick. 사후 parquet 으로 압축
    # → 분석은 parquet 으로 pandas/duckdb. delete_raw=False (안전망).
    scheduler.add_job(
        _compact_tick_logs_today,
        trigger=CronTrigger(day_of_week="mon-fri", hour=16, minute=15),
        id="tick_log_compact",
        name="tick_log jsonl→parquet",
        misfire_grace_time=3600,
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

    # ── M6 모니터링 대시보드 ─────────────────────────────────────────────────
    # 평일 09:00 자동 ON (사용자가 미리 끄지 않았으면) — 데이터 재로드 + paused=False.
    # 자동 OFF cron 폐지 (round 18) — /off 명령으로만 종료. 사용자가 임의 시점에
    # 켜고 끄도록.
    scheduler.add_job(
        _dashboard_start,
        trigger=CronTrigger(day_of_week="mon-fri", hour=9, minute=0),
        args=[client, settings],
        id="dashboard_start",
        name="모니터링 자동 시작",
        misfire_grace_time=120,
    )
    # tick: 2초 간격 (CLAUDE.md 스펙 "1~2초 갱신"). session.paused / 캐시 / 텔레그램
    # 설정 가드는 job 안에서.
    scheduler.add_job(
        _dashboard_tick_job,
        trigger=IntervalTrigger(seconds=2),
        args=[client, settings],
        id="dashboard_tick",
        name="모니터링 tick (2s)",
        misfire_grace_time=4,
        max_instances=1,
        coalesce=True,
    )

    # ── M7 PWA 대시보드 (옵션) ───────────────────────────────────────────────
    # DASHBOARD_PWA_ENABLED=1 일 때만 uvicorn 을 별도 daemon thread 로 시작.
    # session 은 scheduler/worker 와 공유 (_dashboard_session). FastAPI 가 worker
    # tick 이 채운 last_payloads 를 WebSocket 으로 broadcast.
    # bind: 기본 127.0.0.1. Tailscale 검증 시 환경변수 DASHBOARD_PWA_HOST 로
    # 100.x.x.x 또는 0.0.0.0 지정 (dashboard-pwa.md §2.3 참조).
    pwa_server = None
    pwa_thread = None
    if os.environ.get("DASHBOARD_PWA_ENABLED", "").lower() in ("1", "true", "yes"):
        try:
            import uvicorn  # noqa: WPS433

            from src.dashboard.api import create_app

            pwa_host = os.environ.get("DASHBOARD_PWA_HOST", "127.0.0.1")
            pwa_port = int(os.environ.get("DASHBOARD_PWA_PORT", "8000"))
            pwa_app = create_app(_dashboard_session)
            pwa_config = uvicorn.Config(
                pwa_app, host=pwa_host, port=pwa_port,
                log_level="info", access_log=False,
            )
            pwa_server = uvicorn.Server(pwa_config)

            def _run_pwa() -> None:
                import asyncio
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(pwa_server.serve())
                finally:
                    loop.close()

            pwa_thread = threading.Thread(
                target=_run_pwa, name="pwa-uvicorn", daemon=True,
            )
            pwa_thread.start()
            logger.info(
                f"[M7] PWA 대시보드 시작 — http://{pwa_host}:{pwa_port}/ "
                "(Tailscale 인터페이스로 외부 접근)"
            )
        except Exception as e:  # noqa: BLE001
            logger.error(f"[M7] PWA 시작 실패 — fallback 텔레그램 단독 운영: {e}")
            pwa_server = None

    def _shutdown(signum, frame):
        logger.info("종료 시그널 수신 — 스케줄러 셧다운")
        scheduler.shutdown(wait=False)
        if _dashboard_command_stop is not None:
            _dashboard_command_stop.set()
        if pwa_server is not None:
            pwa_server.should_exit = True  # uvicorn graceful stop
            if pwa_thread is not None and pwa_thread.is_alive():
                pwa_thread.join(timeout=3.0)  # WS 클라이언트 graceful close 대기
        if settings.telegram_bot_token and settings.telegram_chat_id:
            try:
                from src.dashboard.worker import cleanup_messages
                cleanup_messages(
                    token=settings.telegram_bot_token,
                    chat_id=settings.telegram_chat_id,
                    session=_dashboard_session,
                    message_ids=_dashboard_message_ids,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[M6] 종료 시 카드 정리 실패: {e}")
        client.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # ── 모니터링 catch-up ──────────────────────────────────────────────
    # scheduler 가 09:00~10:30 사이에 (재)시작되면 dashboard_start cron 은
    # 이미 지나가서 misfire_grace_time(120s)도 못 잡고 missed 된다.
    # 그 결과 _dashboard_master_df/_dashboard_theme_df 가 None → tick 잡이
    # 5초마다 돌긴 하지만 가드(L567-568)에서 return → 메시지 0건.
    # 시작 시 모니터링 시간대면 즉시 _dashboard_start 호출해서 catch up.
    _dt_now = now_kst()
    if is_business_day(_dt_now.date()):
        _start_t = _dt_now.replace(hour=9, minute=0, second=0, microsecond=0)
        _stop_t = _dt_now.replace(hour=10, minute=30, second=0, microsecond=0)
        if _start_t <= _dt_now < _stop_t:
            logger.info(
                f"[M6] 모니터링 시간대 안에서 시작됨 ({_dt_now:%H:%M:%S}) — "
                "_dashboard_start catch-up 호출"
            )
            try:
                _dashboard_start(client, settings)
            except Exception as e:  # noqa: BLE001
                logger.error(f"[M6] catch-up _dashboard_start 실패: {e}")

    try:
        scheduler.start()
    except Exception as e:
        logger.error(f"스케줄러 오류: {e}")
        client.close()
        raise


if __name__ == "__main__":
    run()
