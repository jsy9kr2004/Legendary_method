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
    LIMIT_UP_WATCH_TOP_N: 거래대금 상위 몇 개 종목을 감시할지 (기본 50, Eod.Pick v2 (a))
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

import httpx
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

from src.calendar_kr import is_business_day
from src.config import KST, Settings, load_settings, now_kst
from src.data.intraday import fetch_volume_rank
from src.data.snapshot import save_snapshot
from src.data.storage import read_daily_ohlcv, read_naver_themes, read_stock_master
from src.overnight.candidates import accepted_candidates, extract_candidates
from src.overnight.gap_stats import (
    close_position,
    historical_4layer,
    has_enough_samples,
    pick_sizing_layer,
)
from src.common.theme import (
    codes_in_leading_themes,
    identify_early_morning_leaders,
    identify_leading_stocks,
    identify_leading_themes,
)
from src.common.limit_up import detect_new_limit_up, filter_limit_up_from_snapshot
from src.overnight.sizing import compute_sizing
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
# Eod.Pick v2 (a) round 41 — 종배 후보 universe = 거래대금 50위.
# `LIMIT_UP_WATCH_TOP_N` 환경변수로 오버라이드 가능 (상한가 폴링 + 결정 레포트
# 동일 스냅샷 사용).
_WATCH_TOP_N = int(os.getenv("LIMIT_UP_WATCH_TOP_N", "50"))


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
    """매일 장 시작 전 글로벌 상태 초기화 + 단타 정책 holdings 일일 reset."""
    global _already_limit_up, _watch_codes, _prev_leading_themes, _prev_leading_stocks
    _already_limit_up = set()
    _watch_codes = []
    _prev_leading_themes = []
    _prev_leading_stocks = []
    # 단타 정책: 매일 빈 상태로 시작 (round 40). idempotent — 데몬 첫 가동 시에도
    # run() 에서 호출되므로 중복 안전.
    from src.scalping.exit.triggers import maybe_reset_holdings
    did_reset = maybe_reset_holdings(now_kst())
    if did_reset:
        logger.info("[리셋] 일별 글로벌 상태 + holdings.json 초기화 완료 (archive 백업)")
    else:
        logger.info("[리셋] 일별 글로벌 상태 초기화 완료 (holdings 는 오늘 이미 처리됨)")


# ── 휴장일 가드 데코레이터 ──────────────────────────────────────────────────


def _business_day_only(label: str) -> Callable:
    """잡 함수에 휴장일 가드 + KIS 일시적 HTTP 오류 격리 적용.

    HTTP 5xx / 네트워크 단절 (httpx.HTTPError) 은 KIS 서버 측 일시 장애로
    간주, **logger.warning 만** 남기고 record_error / telegram_error 둘 다 skip.
    이유: 단일 종목 5xx 가 폴링 사이클을 죽일 때마다 텔레그램 푸시 폭주 +
    사후 레포트의 [알려진 이슈] 섹션 도배. fetcher 레벨 (intraday.py 등)에서
    이미 종목 단위로 격리하지만 미래 회귀를 막기 위한 safety net.

    그 외 예외 (KISApiError, KeyError, AttributeError 등)는 기존대로 fail-loud.
    """
    def deco(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not is_business_day(now_kst().date()):
                logger.debug(f"[{label}] 스킵 — 주말/휴장일")
                return None
            try:
                return fn(*args, **kwargs)
            except httpx.HTTPError as e:
                # KIS 서버 5xx / 네트워크 단절 — 일시 장애. 푸시/누적 로그 skip.
                logger.warning(
                    f"[{label}] KIS HTTP 일시 오류: {type(e).__name__}: {e}"
                )
                return None
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

    # master_df 전달 — 비주식 (ETF/ETN/리츠/스팩 + 신주인수권 등 letter 코드)
    # 필터링. 미전달 시 KIS volume-rank 원본에 letter 코드 (0167A0 등) 가 섞여
    # 상한가 폴링이 그 종목들에 inquire-price 호출 → KIS 500 반환 → 폴링 사이클
    # 종료 (2026-05-19 round 후속).
    df = fetch_volume_rank(client, top_n=_WATCH_TOP_N, master_df=_dashboard_master_df)
    if df.empty:
        logger.warning(f"[스냅샷] {label}: 데이터 없음 (휴장일 또는 API 오류)")
        record_error(settings.data_dir, f"스냅샷 {label}", "데이터 없음 (휴장일 또는 API 오류)")
        return

    save_snapshot(df, settings.data_dir, dt)
    logger.info(f"[스냅샷] {label}: {len(df)}종목 저장")

    # 감시 종목 갱신 (상한가 폴링용). 6자리 숫자가 아닌 코드 (warrants/rights 등)
    # 는 KIS inquire-price 가 500 반환하므로 한 번 더 방어적으로 제외.
    _watch_codes = [c for c in df["code"].tolist() if str(c).isdigit() and len(str(c)) == 6]

    # 스냅샷에서 바로 상한가 종목 체크 → 즉시 알림
    lup_df = filter_limit_up_from_snapshot(df)
    if not lup_df.empty:
        for _, row in lup_df.iterrows():
            code = str(row["code"])
            if code not in _already_limit_up:
                _already_limit_up.add(code)
                # round 32 (P1-1 wiring): 도달 시각 저장 → Buy.Score.c 가산점
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
        # round 32 (P1-1 wiring): 도달 시각 저장 → Buy.Score.c 가산점
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
    """09:00~09:30 보유 종목 라이브 청산 지원 (2026-05-25 강화).

    `data/state/holdings.json` 보유 종목 각각에 KIS 현재가/시가/일중고가 +
    `evaluate_overnight_exit_live` → 권고 + 고점대비 되돌림 표시. **자동 주문 X**.

    시초 1회가 아니라 아침 다회 체크인 (09:00/10/20/30) — 청산 타이밍(~9%p)이
    선별(+0.7%)보다 13배 큰 변수라 fade 를 잡으려면 시초 이후도 봐야 함
    (backtest_recent_kelly / memory project-eod-factor-edge). 검증 임계값(≤1/1-6/≥6%)
    을 '현재가' 에 재평가 — 새 자작 임계값 X.
    """
    from src.data.intraday import fetch_quote
    from src.scalping.exit.triggers import load_holdings
    from src.overnight.exit import evaluate_overnight_exit_live, format_overnight_exit_line

    holdings = load_holdings()
    if not holdings:
        return

    hhmm = now_kst().strftime("%H:%M")
    lines = [f"🌅 [종배 청산 지원 — {hhmm}]"]
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
            ctx = evaluate_overnight_exit_live(
                prev_close=float(prev),
                current=float(price),
                intraday_high=float(q.get("intraday_high") or price),
                open_price=float(q["open"]) if q.get("open") else None,
            )
        except ValueError:
            lines.append(f"• {code} — 시세 유효성 실패")
            continue
        any_decision = True
        name = q.get("name") or code
        lines.append(format_overnight_exit_line(name, code, ctx))

    if not any_decision:
        return  # 보유 종목 있어도 시세 모두 실패면 푸시 X
    lines.append("")
    lines.append("_자동 주문 X — 사용자가 HTS 에서 직접 청산. 매도 시점은 본인 판단._")
    dispatcher.send_jongbae_open_exit("\n".join(lines))


@_business_day_only("종배 막판 진입 점검")
def _send_eod_entry_monitor(
    client: KISClient,
    settings: Settings,
    dispatcher: Dispatcher,
) -> None:
    """15:00/10/20 막판 진입 점검 — 14:50 top3 후보의 막판 신호 표시 (2026-05-25).

    영상 통설: 장 막판(3시~3시30분) 흔들림 확인 후 진입. 무너지면 매수 보류,
    매수세 역전/버팀 확인 시 진입. **자동 주문 X — 표시만.** 새 매수 hard rule 없이
    검증 단타 신호(체결강도 VP / 점상한가 / 고점대비 되돌림) 정보 제공. 종배 채널.
    """
    from src.data.intraday import fetch_quote
    from src.data.intraday_realtime import fetch_ccnl_strength
    from src.overnight.eod_entry import build_eod_entry_context, format_eod_entry_line
    from src.report.decision import load_decision_candidates

    today = now_kst().date()
    cands = load_decision_candidates(settings.data_dir, today)
    accepted = [c for c in (cands or []) if c.get("priority") != "excluded"]
    accepted = sorted(accepted, key=lambda c: (c.get("rank") or 9999))[:5]
    if not accepted:
        return

    hhmm = now_kst().strftime("%H:%M")
    lines = [
        f"🛎 [종배 막판 진입 점검 — {hhmm}]",
        "_14:50 후보 막판 흔들림. 무너지면 매수 보류 / 매수세·버팀 확인 시 진입. 자동주문 X._",
        "",
    ]
    any_ok = False
    for c in accepted:
        code = str(c.get("code", "")).zfill(6)
        q = fetch_quote(client, code)
        if q is None:
            continue
        price = q.get("price", 0)
        prev = q.get("prev_close", 0)
        if not price or not prev or price <= 0 or prev <= 0:
            continue
        vp = None
        try:
            cs = fetch_ccnl_strength(client, code)
            if cs:
                v = cs.get("ccnl_strength")
                vp = float(v) if (v is not None and v == v) else None
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[막판점검] {code} 체결강도 실패: {e}")
        try:
            ctx = build_eod_entry_context(
                prev_close=float(prev),
                price=float(price),
                intraday_high=float(q.get("intraday_high") or price),
                is_limit_up=bool(q.get("is_limit_up")),
                vp=vp,
            )
        except ValueError:
            continue
        any_ok = True
        lines.append(format_eod_entry_line(
            q.get("name") or code, code, ctx, is_top3=bool(c.get("is_top3")),
        ))

    if not any_ok:
        return
    dispatcher.send_eod_entry("\n".join(lines))


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


def _record_eod_forward_outcomes(settings: Settings) -> None:
    """종배 forward 로깅 — 직전 영업일 14:50 후보 + 오늘 실현 갭 join (2026-05-25).

    16:40 (일봉 incremental 16:30 + 사후 후) 실행. 직전 영업일(D) 결정의 outcome 은
    오늘(D+1) 갭이므로 D 저장 후보 + 현재 daily_ohlcv 로 outcome 기록. 수급/체결강도
    등 backtest 불가 신호의 미래 factor_edge 분석 + 청산 envelope 실측 누적.
    """
    from src.overnight.forward_log import backfill_pending_outcomes

    if not is_business_day(now_kst().date()):
        return
    daily = read_daily_ohlcv(settings.data_dir)
    if daily is None or daily.empty:
        logger.warning("[forward] 일봉 부재 — outcome 기록 skip")
        return
    # self-heal: 주식 일봉 incremental 이 데몬 cron 이 아니라(./go update/start) 16:40 에
    # 오늘 바가 없을 수 있음 → 미기록 결정일을 모두 재시도. daily 갱신되면 다음 실행 기록.
    try:
        backfill_pending_outcomes(daily, settings.data_dir)
    except Exception as e:  # noqa: BLE001
        logger.error(f"[forward] outcome 기록 실패: {e}")


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


def _enrich_candidates_with_quote(
    candidate_dicts: list[dict[str, Any]],
    client: KISClient,
) -> None:
    """결정 후보 dict 리스트의 누락된 OHLCV 필드를 fetch_quote 로 보강 (in-place).

    KIS volume-rank 응답이 stck_prdy_clpr / stck_hgpr / acml_tr_pbmn 등을 빈 값으로
    주는 케이스가 있어 snapshot 기반 후보 dict 의 prev_close/intraday_high/trading_value
    등이 0 으로 채워짐. 결정 레포트에서는 후보 수가 적으니 (1~5) 종목별 fetch_quote
    추가 1콜로 정확한 값 확보. snapshot 의 rank/themes 는 그대로 보존하고,
    시총/회전율은 snapshot 값이 0(master 파싱 깨짐)일 때만 fetch_quote 값으로 보강.
    intraday_high_pct 도 보강된 prev_close/intraday_high 로 재계산.
    """
    from src.data.intraday import fetch_quote
    from src.overnight.candidates import _intraday_high_pct

    _MISSING_TARGETS = (
        "prev_close", "intraday_high", "intraday_low",
        "trading_value", "volume", "price", "daily_return", "is_limit_up",
        # 2026-05-24: 시총/회전율도 보강 대상에 추가. 스냅샷 master 시총이 0(파싱
        # 깨짐)이면 fetch_quote 의 hts_avls / 회전율 역산값으로 채운다.
        "market_cap", "turnover",
    )

    def _is_missing(v: Any) -> bool:
        if v is None:
            return True
        if isinstance(v, bool):
            return False
        if isinstance(v, (int, float)):
            return v == 0 or v != v  # 0 or NaN
        return False

    # daily_ohlcv fallback 용 — fetch_quote 도 0 으로 줄 때 어제 종가 사용
    daily_ohlcv = None
    try:
        # 2026-05-24 fix: read_daily_ohlcv 는 src.data.storage 에 있다 (src.data.daily
        # 아님). 이 import 가 ImportError 로 조용히 삼켜져 daily_ohlcv=None 이 되면서
        # prev_close fallback 이 영영 안 돌았고, 그 결과 결정 레포트 일봉이 "0 → X"
        # 로 깨져 나왔다 (사용자가 05-21 에 정정 요청했던 버그가 미배포 상태였음).
        daily_ohlcv = read_daily_ohlcv(load_settings().data_dir)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[결정] daily_ohlcv fallback 로드 실패: {e}")

    for base in candidate_dicts:
        code = str(base.get("code", "")).zfill(6)
        if not code:
            continue
        q = fetch_quote(client, code)
        if q is None:
            logger.warning(f"[결정] {code} fetch_quote 응답 None — prev_close/intraday_high 보강 불가")
            continue
        for field in _MISSING_TARGETS:
            if _is_missing(base.get(field)) and not _is_missing(q.get(field)):
                base[field] = q[field]

        # fetch_quote 도 prev_close=0 인 경우 daily_ohlcv 어제 종가로 fallback
        # (사용자 정정 2026-05-21: 종배 레포트에서 일봉 "0 → 75,900" 으로 표시되는 문제)
        if _is_missing(base.get("prev_close")) and daily_ohlcv is not None and not daily_ohlcv.empty:
            today = base.get("_today_date")  # 호출자가 미리 채워넣을 수 있게 hook
            own = daily_ohlcv[daily_ohlcv["code"] == code]
            if not own.empty:
                # 가장 최근 거래일 종가 (today 미입력 시 last row 사용)
                own_sorted = own.sort_values("date")
                last_close = int(own_sorted.iloc[-1].get("close") or 0)
                if last_close > 0:
                    base["prev_close"] = last_close
                    logger.info(
                        f"[결정] {code} prev_close fetch_quote 0 → daily_ohlcv fallback {last_close:,}"
                    )
                else:
                    logger.warning(
                        f"[결정] {code} prev_close daily_ohlcv fallback 도 0 — 표시 깨짐 예상"
                    )

        # daily_return 재계산 — prev_close 보강 후 일치성 확보
        try:
            price = int(base.get("price") or 0)
            prev = int(base.get("prev_close") or 0)
            if price > 0 and prev > 0:
                base["daily_return"] = (price - prev) / prev * 100.0
        except (TypeError, ValueError):
            pass

        # intraday_high_pct 재계산 (보강 후 값으로)
        try:
            high = int(base.get("intraday_high") or 0)
            prev = int(base.get("prev_close") or 0)
            if high > 0 and prev > 0:
                base["intraday_high_pct"] = _intraday_high_pct(high, prev)
        except (TypeError, ValueError):
            pass


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
            from src.overnight.gap_stats import market_regime_timeline
            market_regime_by_date = market_regime_timeline(kospi_daily)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[결정] market_stats/regime 조회 실패: {e}")
            record_error(settings.data_dir, "결정", f"market_stats/regime 조회 실패: {e}")
    today_strong_market = market_stats.get("kospi_above_ma200")

    leading = identify_leading_themes(snapshot_df, theme_df)
    # Eod.Pick v2 (a) round 41 — 결정 후보 universe 는 주도섹터 우회, 전체 snapshot (top 50)
    # 사용. 주도테마는 레포트 헤더의 [최종 주도테마] 섹션 표시용으로만 식별.
    # docs/scalping-strategy.md line 206 참조.
    candidates_df = extract_candidates(snapshot_df, leading_theme_codes=None)
    accepted = accepted_candidates(candidates_df)

    # 진단 로깅 (2026-05-19 round 41 후속) — 사용자 보고: "후보가 top 50 밖 종목"
    # 의심 시 snapshot 의 실제 KIS rank / 후보 선정 결과 확인용.
    logger.info(
        f"[결정] 진단: snapshot {len(snapshot_df)}종목 "
        f"(KIS rank 범위 {snapshot_df['rank'].min() if not snapshot_df.empty else 'N/A'}"
        f"~{snapshot_df['rank'].max() if not snapshot_df.empty else 'N/A'})"
    )
    if not snapshot_df.empty:
        top10 = snapshot_df.sort_values("rank").head(10)
        for _, r in top10.iterrows():
            logger.info(
                f"  rank={int(r['rank']):>3} {str(r.get('code','')):>6} "
                f"{str(r.get('name','')):<10} ret={float(r.get('daily_return') or 0):+.2f}% "
                f"value={int(r.get('trading_value') or 0)/1e8:.1f}억"
            )
    if not candidates_df.empty:
        excluded = candidates_df[candidates_df["priority"] == "excluded"]
        if not excluded.empty:
            logger.info(f"[결정] 제외된 종목 ({len(excluded)}개):")
            for _, r in excluded.head(20).iterrows():
                reason = r.get("exclusion_reason", "?")
                logger.info(
                    f"  rank={int(r['rank']):>3} {str(r.get('code','')):>6} "
                    f"{str(r.get('name','')):<10} ret={float(r.get('daily_return') or 0):+.2f}% "
                    f"← {reason}"
                )
    logger.info(f"[결정] 채택 후보: {len(accepted)}종목")

    # KIS volume-rank 응답이 일부 종목에서 prev_close / intraday_high / trading_value
    # 필드를 비워서 주는 케이스가 있음 (2026-05-19 사용자 보고 — 진원생명과학 011000
    # 상한가 종목의 prev_close=0, intraday_high=0, trading_value=0 → 표시 깨짐 +
    # close_position 계산 깨짐 → Layer 3 매칭 불가). 결정 레포트는 후보 N 이 작으니
    # (보통 1~5) 후보별로 fetch_quote 추가 호출해서 누락 필드 보강. snapshot 의
    # rank/turnover/themes 는 살리고 OHLCV 만 덮어씀.
    accepted_dicts: list[dict[str, Any]] = [row.to_dict() for _, row in accepted.iterrows()]
    if client is not None and accepted_dicts:
        _enrich_candidates_with_quote(accepted_dicts, client)

    # Eod.Pick v2 (c) 종가 고가-10% 이내 + (d) 52주 신고가 post-filter (round 41).
    # fetch_quote 보강 후 intraday_high / price 가 0 아닌 상태에서 적용.
    today = dt.date()
    from src.overnight.candidates import apply_r4v2_post_filters
    if accepted_dicts and not daily_ohlcv.empty:
        before = len(accepted_dicts)
        accepted_dicts = apply_r4v2_post_filters(accepted_dicts, daily_ohlcv, today)
        logger.info(
            f"[결정] Eod.Pick v2 (c)(d) post-filter: {before}→{len(accepted_dicts)}종목"
        )

    from src.overnight.nxt import is_nxt_tradable, load_nxt_tradable
    _nxt_set = load_nxt_tradable(settings.data_dir)

    candidates_with_stats: list[dict[str, Any]] = []
    for row in accepted_dicts:
        code = str(row.get("code", "")).zfill(6)
        close = int(row.get("price") or 0)
        high_raw = int(row.get("intraday_high") or 0)
        high = high_raw if high_raw > 0 else close
        low_raw = int(row.get("intraday_low") or 0)
        low = low_raw if low_raw > 0 else int(close * 0.85)
        prev_close_val = int(row.get("prev_close") or 0) or close
        cp = close_position(
            open_p=float(prev_close_val),
            high=float(high),
            low=float(low),
            close=float(close),
        )
        vol_ratio = _today_volume_ratio(daily_ohlcv, code, today, int(row.get("volume") or 0))
        # 종목별 layer (사용자 정정 2026-05-21): code 인자로 해당 종목 historical 만.
        # cross-stock pool 은 별도 (시장 평균 reference, footer 표시 — 아래 코드 참조).
        layers = historical_4layer(
            daily_ohlcv,
            today_close_pos=cp,
            today=today,
            today_strong_market=today_strong_market,
            market_regime_by_date=market_regime_by_date or None,
            today_volume_ratio=vol_ratio,
            code=code,
        )
        sizing_layer_name, sizing_stats = pick_sizing_layer(layers)

        # Eod.Pick v2 (f) Layer 표본 ≥5 — round 41 후속 2026-05-19: hard cut → soft.
        # 표본 부족도 후보 유지 + Kelly 가 None 으로 나오는 것만 사용자에게 표시.
        # 사이즈 결정은 사용자가 Sharpe/Equal/직관 으로 판단.
        sample_sufficient = has_enough_samples(sizing_stats)
        if not sample_sufficient:
            logger.info(f"[결정] {code} Eod.Pick v2 (f) 표본 부족 (n<5) — soft 경고, 후보 유지")

        themes = (
            theme_df[theme_df["code"] == code]["theme"].tolist()
            if not theme_df.empty
            else []
        )
        # Eod.Pick v2 보조 지표 — 1년 ret≥10% 횟수 + 갭상 비율 (round 41 ④)
        # 사용자 정정 2026-05-21: 4 기간 × 3 임계 = 12 케이스 매트릭스 추가
        from src.overnight.gap_stats import (
            candle_count_aux,
            historical_aux_matrix,
            historical_ret10_gap_stats,
        )
        ret10_aux = historical_ret10_gap_stats(daily_ohlcv, code, today)
        aux_matrix = historical_aux_matrix(daily_ohlcv, code, today)
        candle_aux = candle_count_aux(daily_ohlcv, code, today)  # 표시 전용 보조

        c: dict[str, Any] = dict(row)
        c["themes"] = themes
        c["layers"] = layers
        c["sizing_layer"] = sizing_layer_name
        c["sizing_stats"] = sizing_stats
        c["historical_aux"] = ret10_aux
        c["historical_aux_matrix"] = aux_matrix  # 12 cells: (period, ret_th)
        c["candle_aux"] = candle_aux  # 양봉/장대양봉 카운트 (표시 전용)
        c["nxt_tradable"] = is_nxt_tradable(code, _nxt_set)  # NXT 가능/불가/추정
        c["sample_sufficient"] = sample_sufficient
        candidates_with_stats.append(c)

    sizing_results = compute_sizing(candidates_with_stats)
    for i, c in enumerate(candidates_with_stats):
        c["sizing"] = {
            "kelly":  sizing_results["kelly"][i],
            "sharpe": sizing_results["sharpe"][i],
            "equal":  sizing_results["equal"][i],
        }

    # Eod.Sizing v2 (2026-05-25): 거래대금순위 버킷 rolling Kelly — 검증된 사이징.
    # per-stock historical(끼)/신고가/시총은 노이즈(factor_edge backtest) → 거래대금순위로
    # 조건부화. 절대 비중(계좌 대비, 현금=1-Σ) + top3 내 상대 강약.
    # (memory project-eod-factor-edge / scripts/backtest_factor_edge.py)
    try:
        from src.overnight.sizing_bucket import build_bucket_stats, compute_bucket_sizing
        _master_df = read_stock_master(settings.data_dir)
        _tradable = (
            set(_master_df["code"].astype(str))
            if _master_df is not None and not _master_df.empty
            else set()
        )
        _bucket_stats = build_bucket_stats(daily_ohlcv, today, _tradable)
        _bsize = compute_bucket_sizing(candidates_with_stats, _bucket_stats)
        for i, c in enumerate(candidates_with_stats):
            c.setdefault("sizing", {})
            c["sizing"]["kelly_bucket"] = _bsize["kelly_abs"][i]
            c["sizing"]["kelly_bucket_rel"] = _bsize["kelly_rel"][i]
            c["sizing_bucket"] = _bsize["buckets"][i]
        logger.info(
            f"[결정] 버킷 사이징 — 투입 {_bsize['invested']*100:.0f}% / 현금 {_bsize['cash']*100:.0f}%"
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[결정] 버킷 사이징 실패 — 생략: {e}")

    # 거래대금순위 정렬 + top3 플래그 (사용자 hold-3: 시초 동시매도 부담 → top3 만 매수).
    # 검증: scripts/backtest_top3_selection.py (거래대금 top3 갭상 1년 55%/3개월 64%).
    candidates_with_stats.sort(key=lambda c: (c.get("rank") or 9999))
    for i, c in enumerate(candidates_with_stats):
        c["rank_in_report"] = i + 1
        c["is_top3"] = i < 3

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
                    # 2026-05-22: 종목별 외인/기관/프로그램 일별 누적 — N일 평균 비교용.
                    # KIS 일별 endpoint (FHPTJ04160001 등) 가 시간 제한으로 새벽 차단되어
                    # 자체 누적이 즉시 작동 안전망. 같은 날 재실행 안전 (덮어쓰기).
                    from src.data.investor_daily import (
                        append_today_stock,
                        get_nday_avg_stock,
                    )
                    try:
                        append_today_stock(code, inv, today)
                        avg = get_nday_avg_stock(code, today)
                        if avg:
                            signals["investor_nday_avg"] = avg
                    except Exception as e2:  # noqa: BLE001
                        logger.warning(f"[결정] {code} 투자자 누적 저장/평균 실패: {e2}")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[결정] {code} 투자자 매매 조회 실패: {e}")
            if signals:
                c["intraday_signals"] = signals

    # 시장 평균 layer reference — cross-stock pool 한 번만 (사용자 정정 2026-05-21).
    # 후보별 layer 는 종목별로 계산되므로, 시장 평균 비교용으로 footer 에 표시.
    market_layers = None
    try:
        # 시장 평균은 first 후보의 close_pos 무관하게 Layer 1/2 만 의미 있음 (Layer 3 은
        # 후보별 close_pos 매칭이라 종목별로만 의미). dummy close_pos=0.5 로 호출 후
        # Layer 1/2 만 사용.
        market_layers = historical_4layer(
            daily_ohlcv,
            today_close_pos=0.5,
            today=today,
            code=None,  # cross-stock pool
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[결정] 시장 평균 layer 계산 실패: {e}")

    # Phase 2 (2026-05-22): 시장 외인/기관/프로그램 일별 — KOSPI + KOSDAQ 각 1회
    # KIS endpoint 호출 (300일 / 30일 응답). 결정 레포트 footer 에 시장 vs 20일 평균 표시.
    market_summaries: dict[str, Any] = {}
    if client is not None:
        try:
            from src.data.intraday_realtime import fetch_market_summary
            from src.data.investor_daily import append_today_market
            for mkt in ("KOSPI", "KOSDAQ"):
                summary = fetch_market_summary(client, mkt, n_days=20)
                if summary:
                    market_summaries[mkt] = summary
                    # 자체 누적 (fallback 안전망 — KIS endpoint 변경 시).
                    today_d = summary.get("today") or {}
                    append_today_market(mkt, {
                        "foreign_net_buy": today_d.get("foreign_qty", 0),
                        "institution_net_buy": today_d.get("institution_qty", 0),
                        "program_net_buy": today_d.get("program_qty", 0),
                    }, today)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[결정] 시장 외인기관프로그램 조회 실패: {e}")

    # 후보 3거래일 추이 (거래대금/회전율/수급 + 순위 변동) — 2026-05-24 사용자 요청.
    # 수급 추이는 investor_daily 누적을 읽으므로 위 14:50 시그널 fetch(=오늘 행 append)
    # 이후에 실행해야 오늘 값이 포함된다.
    try:
        from src.overnight.candidate_trends import attach_candidate_trends
        attach_candidate_trends(candidates_with_stats, daily_ohlcv, settings.data_dir, today)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[결정] 후보 추이 계산 실패 — 추이 생략: {e}")

    report = build_decision_report(
        leading, candidates_with_stats, dt,
        market_stats=market_stats,
        market_layers=market_layers,
        market_summaries=market_summaries or None,
    )
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

    # 데몬 첫 가동 시 holdings 일일 reset 보장 (round 40, 단타 정책).
    # 08:30 cron 을 놓친 시각에 가동돼도 첫 가동 시 reset. idempotent — 장중
    # 재기동 시에는 today == last_reset 으로 skip 되어 보유 상태 안전.
    from src.scalping.exit.triggers import maybe_reset_holdings
    if maybe_reset_holdings(now_kst()):
        logger.info("[리셋] 데몬 가동 시 holdings.json 초기화 완료 (archive 백업)")

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

    # 09:01/10/20/30 종배 라이브 청산 지원 (2026-05-25 강화 — 다회 체크인)
    # 청산 타이밍(~9%p)이 선별(+0.7%)보다 13배 큰 변수라 시초 1회로는 fade 를 못 잡음.
    # 검증 임계값(≤1/1-6/≥6%)을 현재가에 라이브 재평가 + 고점대비 되돌림 표시.
    # 보유 종목 없으면 no-op. (NXT 프리장 08:00~ 청산 지원은 KIS NXT API 검증 후 v1.)
    scheduler.add_job(
        _send_jongbae_open_exit_recommendation,
        trigger=CronTrigger(day_of_week="mon-fri", hour=9, minute="1,10,20,30"),
        args=[client, settings, dispatcher],
        id="jongbae_open_exit",
        name="종배 라이브 청산 지원",
        misfire_grace_time=300,
    )

    # 15:00/10/20 종배 막판 진입 점검 (2026-05-25) — 14:50 top3 후보 막판 신호 표시.
    # 영상 통설: 장 막판 흔들림 확인 후 진입. 무너지면 보류. 자동주문 X. 종배 채널.
    scheduler.add_job(
        _send_eod_entry_monitor,
        trigger=CronTrigger(day_of_week="mon-fri", hour=15, minute="0,10,20"),
        args=[client, settings, dispatcher],
        id="eod_entry_monitor",
        name="종배 막판 진입 점검",
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

    # 16:40 종배 forward 로깅 — 직전 영업일 14:50 후보 + 오늘 실현 갭 join.
    # 수급/체결강도(backtest 불가) 미래 factor_edge 분석 + 청산 envelope 누적.
    scheduler.add_job(
        _record_eod_forward_outcomes,
        trigger=CronTrigger(day_of_week="mon-fri", hour=16, minute=40),
        args=[settings],
        id="eod_forward_log",
        name="종배 forward 로깅",
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
