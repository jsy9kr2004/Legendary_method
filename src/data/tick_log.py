"""tick-level 시그널 로깅 (Phase 1).

매 2초 tick 마다 Stage 0 통과 50종목 + monitored 풀(auto/rising/manual/hold) 합집합에
대해 모든 raw 시그널 + funnel 통과/탈락 + Buy.Score 점수 breakdown 을 jsonl 한 줄로 append.

운영 흐름:
    운영 (매 tick):      data/tick_logs/raw/YYYY-MM-DD.jsonl 매 tick append
    16:00 사후 cron:     YYYY-MM-DD.jsonl → YYYY-MM-DD.parquet 변환 (별도 모듈)
    분석:                parquet 으로 pandas/duckdb 쿼리

설계 원칙 (`memory/project_long_term_vision.md`):
    "데이터는 과도하게라도 최대한 남긴다 — Phase 1 의 핵심은 사후 분석에 필요한
    모든 시그널을 timestamped 로 보존". 매수/매도 이벤트는 별도 `trades/` 디렉토리.

비용 시뮬레이션:
    50종목 × 7200 tick × 40 컬럼 ≈ 14.4M cell / 일
    jsonl raw: ~30MB / 일 (압축 X)
    parquet 변환 후: ~5MB / 일
    1년 누적 parquet: ~1.8GB
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger


# data 디렉토리 — 환경변수 DATA_DIR override 가능 (운영/테스트 분리용)
def _data_dir() -> Path:
    return Path(os.getenv("DATA_DIR", "data"))


def _tick_log_path(date: datetime) -> Path:
    """일별 jsonl 파일 경로 — data/tick_logs/raw/YYYY-MM-DD.jsonl."""
    d = _data_dir() / "tick_logs" / "raw"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{date.strftime('%Y-%m-%d')}.jsonl"


@dataclass
class TickLogRow:
    """매 tick 종목별 raw 시그널 한 묶음. JSON 직렬화 안전 (None 허용)."""

    # ── 기본 ──────────────────────────────────────────────────────────────
    ts: str                       # ISO 형식 (KST). e.g. "2026-05-15T09:32:18+09:00"
    code: str
    name: str

    # ── source / flags ───────────────────────────────────────────────────
    is_auto: bool = False
    is_rising: bool = False
    is_manual: bool = False
    is_holding: bool = False

    # ── 가격 / 거래 ──────────────────────────────────────────────────────
    price: int | None = None
    prev_close: int | None = None
    daily_return: float | None = None    # %
    is_limit_up: bool = False
    turnover: float | None = None        # %
    trading_value: int | None = None     # 원
    rank: int | None = None              # 거래대금 순위
    intraday_high: int | None = None

    # ── 모멘텀 ────────────────────────────────────────────────────────────
    vol_accel_5m: float | None = None
    vol_accel_1m: float | None = None
    recent_bar_value: int | None = None  # 최근 5분 거래대금 합계
    last_bar_value: int | None = None    # 최근 1분 거래대금

    # ── 봉 패턴 ──────────────────────────────────────────────────────────
    candle_type: str | None = None       # "bullish" / "bearish" / "doji"
    candle_upper_wick_ratio: float | None = None
    candle_lower_wick_ratio: float | None = None

    # ── 체결강도 (VP) ────────────────────────────────────────────────────
    vp: float | None = None              # 당일 누적
    vp_5ma: float | None = None
    vp_1ma: float | None = None
    buy_ratio: float | None = None       # 능동 매수 비율 (KIS 응답에 없음 — NaN 가능)

    # ── 호가 ─────────────────────────────────────────────────────────────
    bid_total_volume: int | None = None
    ask_total_volume: int | None = None
    bid_ask_ratio: float | None = None
    bid1_price: int | None = None
    ask1_price: int | None = None

    # ── 외인/기관/프로그램 (round 36) ─────────────────────────────────────
    foreign_net_buy: int | None = None             # 수량
    institution_net_buy: int | None = None
    individual_net_buy: int | None = None
    program_net_buy: int | None = None             # 프로그램은 KIS 응답에 금액 X
    foreign_net_buy_value: int | None = None       # 금액 (원)
    institution_net_buy_value: int | None = None

    # ── 수급 Δ (round 36 후속) ────────────────────────────────────────────
    investor_delta_foreign_value: int | None = None
    investor_delta_institution_value: int | None = None
    investor_delta_program_qty: int | None = None
    investor_delta_elapsed_sec: int | None = None

    # ── VWAP / MA ────────────────────────────────────────────────────────
    price_vs_vwap_pct: float | None = None
    price_vs_ma5_pct: float | None = None
    price_vs_ma20_pct: float | None = None

    # ── 다이버전스 (Buy.Div) ─────────────────────────────────────────────────
    divergence_bearish: bool = False
    divergence_bullish: bool = False
    divergence_price_change_pct: float | None = None
    divergence_vp_5ma_delta: float | None = None

    # ── 거래량 비율 (Buy.Score.d) ───────────────────────────────────────────────
    volume_ratio_vs_prev_day: float | None = None

    # ── 상한가 도달 시각 (Buy.Score.c) ──────────────────────────────────────────
    limit_up_hit_time: str | None = None  # "HHMMSS" or None

    # ── Buy.Score 매수 점수 ────────────────────────────────────────────────────
    buy_score: float | None = None
    buy_grade: str | None = None          # STRONG / WATCH / NEUTRAL / AVOID
    buy_reasons: list[str] = field(default_factory=list)

    # ── 매매법 분류 (P1-4, docs §11.1) — 로깅 전용 dry-run. 카드 표시는 검증 후 ──
    setup_label: str | None = None        # breakout / pullback / chase / none
    setup_score_breakout: float | None = None
    setup_score_pullback: float | None = None
    setup_chase_warning: bool = False

    # ── Exit.Triggers 청산 트리거 발화 상태 ─────────────────────────────────────────
    # 감시 모드: C1~C4 만 유효 (C5 는 보유 모드만)
    # 보유 모드: A1~A5, B1~B3, C1~C5 모두 유효
    trigger_a1_stop_price: bool = False
    trigger_a2_stop_bar_low: bool = False
    trigger_a3_stop_ma: bool = False
    trigger_a4_stop_time: bool = False
    trigger_a5_eod_ma_break: bool = False
    trigger_p1_take_profit_1: bool = False
    trigger_p2_take_profit_2: bool = False
    trigger_p3_trailing: bool = False
    trigger_e1_vp_below_100: bool = False
    trigger_e2_bearish_divergence: bool = False
    trigger_e3_vol_drain: bool = False
    trigger_e4_bearish_candle: bool = False
    trigger_e5_vi_failure: bool = False

    # ── funnel 통과 여부 ──────────────────────────────────────────────────
    # Stage 0 통과 종목 중 Buy.Score 풀스코어 평가까지 갔는지. round 37 이후 단일 컷.
    funnel_evaluated: bool = False        # 분봉/체결강도/호가/투자자 fetch 됐는지
    funnel_passed_rising: bool = False    # RISING_MIN_SCORE 통과해서 RISING 풀에 들어갔는지

    # ── 보유 정보 ────────────────────────────────────────────────────────
    holding_entry_price: int | None = None
    holding_entry_time: str | None = None     # ISO
    holding_elapsed_sec: int | None = None
    holding_pnl_pct: float | None = None


def append_tick_log(rows: list[TickLogRow], now: datetime) -> None:
    """tick 한 묶음을 일별 jsonl 에 append. 실패 시 로그만 남기고 운영 영향 X.

    Args:
        rows: 이 tick 의 모든 종목 row.
        now: tick 시각 (날짜 분리용).
    """
    if not rows:
        return
    path = _tick_log_path(now)
    try:
        with path.open("a", encoding="utf-8") as f:
            for row in rows:
                json.dump(asdict(row), f, ensure_ascii=False, default=str)
                f.write("\n")
    except OSError as e:
        # 디스크 풀 / 권한 등 — fail-loud (로그만, 운영 중단 X)
        logger.warning(f"[tick_log] jsonl append 실패 ({path}): {e}")


# ── trade 이벤트 (별도 디렉토리) ─────────────────────────────────────────


def _trade_log_path(date: datetime) -> Path:
    """매수/매도 이벤트 일별 jsonl — data/trades/YYYY-MM-DD.jsonl."""
    d = _data_dir() / "trades"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{date.strftime('%Y-%m-%d')}.jsonl"


@dataclass
class TradeEvent:
    """매수/매도 이벤트 (사용자 직접 또는 Exit.Triggers 트리거).

    tick_logs 와 timestamp 로 join 해서 매수/매도 시점 ± N분 시그널 분석 가능.
    CLAUDE.md "자동 매매 금지" 정책상 실주문은 사용자가 외부 HTS 에서 직접 —
    이 이벤트는 봇의 /buy /sell 명령 마킹만.
    """
    ts: str                       # ISO
    code: str
    name: str
    action: str                   # "buy" / "sell"
    price: int | None = None      # /buy 시 명시 또는 자동 보충 (last_prices)
    source: str | None = None     # "command" (사용자 /buy) / "auto" / "manual"
    trigger_fired: str | None = None  # Exit.Triggers 트리거 발화 사유 (sell 시)
    user_note: str | None = None  # 사용자 메모 ("감으로 버팀" 같은)


def append_trade_event(event: TradeEvent, now: datetime) -> None:
    """매수/매도 이벤트 jsonl append. 실패 시 로그만."""
    path = _trade_log_path(now)
    try:
        with path.open("a", encoding="utf-8") as f:
            json.dump(asdict(event), f, ensure_ascii=False, default=str)
            f.write("\n")
    except OSError as e:
        logger.warning(f"[trade_log] jsonl append 실패 ({path}): {e}")


# ── 빌더 — worker dashboard_tick 에서 호출 ───────────────────────────────


def build_tick_log_row(
    *,
    now: datetime,
    code: str,
    name: str,
    monitored: Any,                       # MonitoredStock — duck typed
    snap_row: dict[str, Any] | None,
    bars_present: bool,
    accel_5m: float,
    accel_1m: float,
    recent_bar_value: int | None,
    last_bar_value: int | None,
    candle: Any,                          # Candle object — duck typed
    vp_now: float,
    vp_5ma: float,
    vp_1ma: float,
    ccnl: dict[str, Any] | None,
    asking: dict[str, Any] | None,
    investor: dict[str, Any] | None,
    investor_delta: dict[str, Any] | None,
    vwap_pct: float,
    ma5_pct: float,
    ma20_pct: float,
    divergence: Any,                      # DivergenceState — duck typed
    volume_ratio: float,
    limit_up_hit_time: Any,               # datetime.time | None
    trigger_states: dict[str, bool],
    funnel_evaluated: bool,
    holding: Any = None,
    intraday_high_override: int | None = None,
) -> TickLogRow:
    """필드별 NaN/None 안전 변환 후 row 생성. worker 의 종목 루프 끝에서 호출.

    intraday_high_override: KIS volume-rank API stck_hgpr 가 0 으로 응답되는 결함
    회피용. worker 가 분봉 bars 최고가로 fallback 한 값을 전달하면 snap_row 의
    intraday_high (대부분 0) 대신 우선 사용. None 이면 snap_row 값 사용 (기존 동작).
    """

    def _int_safe(v: Any) -> int | None:
        if v is None:
            return None
        try:
            f = float(v)
            if f != f:  # NaN
                return None
            return int(f)
        except (TypeError, ValueError):
            return None

    def _float_safe(v: Any) -> float | None:
        if v is None:
            return None
        try:
            f = float(v)
            return None if f != f else f
        except (TypeError, ValueError):
            return None

    snap_row = snap_row or {}
    is_holding = holding is not None

    # 봉 패턴
    candle_type = getattr(candle, "type", None) if candle is not None else None
    candle_uw = _float_safe(getattr(candle, "upper_wick_ratio", None)) if candle is not None else None
    candle_lw = _float_safe(getattr(candle, "lower_wick_ratio", None)) if candle is not None else None

    # 다이버전스
    div_bear = bool(getattr(divergence, "bearish", False)) if divergence is not None else False
    div_bull = bool(getattr(divergence, "bullish", False)) if divergence is not None else False
    div_pc = _float_safe(getattr(divergence, "price_change_pct", None)) if divergence is not None else None
    div_vd = _float_safe(getattr(divergence, "vp_5ma_delta", None)) if divergence is not None else None

    # investor delta
    inv_d = investor_delta or {}

    # 보유 정보
    entry_price = None
    entry_time_iso = None
    elapsed_sec = None
    pnl_pct = None
    if is_holding:
        entry_price = _int_safe(getattr(holding, "entry_price", None))
        et = getattr(holding, "entry_time", None)
        entry_time_iso = et.isoformat() if et else None
        if et:
            elapsed_sec = int((now - et).total_seconds())
        cur = _int_safe(snap_row.get("price"))
        if cur and hasattr(holding, "pnl_pct"):
            pnl_pct = _float_safe(holding.pnl_pct(cur))

    # limit_up_hit_time → "HHMMSS"
    lut_str = None
    if limit_up_hit_time is not None:
        try:
            lut_str = limit_up_hit_time.strftime("%H%M%S")
        except (AttributeError, ValueError):
            lut_str = None

    return TickLogRow(
        ts=now.isoformat(),
        code=code,
        name=name or code,
        is_auto=bool(getattr(monitored, "is_auto", False)),
        is_rising=bool(getattr(monitored, "is_rising", False)),
        is_manual=bool(getattr(monitored, "is_manual", False)),
        is_holding=is_holding,
        # 가격
        price=_int_safe(snap_row.get("price")),
        prev_close=_int_safe(snap_row.get("prev_close")),
        daily_return=_float_safe(snap_row.get("daily_return")),
        is_limit_up=bool(snap_row.get("is_limit_up", False)),
        turnover=_float_safe(snap_row.get("turnover")),
        trading_value=_int_safe(snap_row.get("trading_value")),
        rank=_int_safe(snap_row.get("rank")),
        intraday_high=(
            intraday_high_override
            if intraday_high_override is not None and intraday_high_override > 0
            else _int_safe(snap_row.get("intraday_high"))
        ),
        # 모멘텀
        vol_accel_5m=_float_safe(accel_5m),
        vol_accel_1m=_float_safe(accel_1m),
        recent_bar_value=_int_safe(recent_bar_value),
        last_bar_value=_int_safe(last_bar_value),
        # 봉
        candle_type=candle_type,
        candle_upper_wick_ratio=candle_uw,
        candle_lower_wick_ratio=candle_lw,
        # VP
        vp=_float_safe(vp_now),
        vp_5ma=_float_safe(vp_5ma),
        vp_1ma=_float_safe(vp_1ma),
        buy_ratio=_float_safe(ccnl.get("buy_ratio") if ccnl else None),
        # 호가
        bid_total_volume=_int_safe(asking.get("bid_total_volume") if asking else None),
        ask_total_volume=_int_safe(asking.get("ask_total_volume") if asking else None),
        bid_ask_ratio=_float_safe(asking.get("bid_ask_ratio") if asking else None),
        bid1_price=_int_safe(asking.get("bid1_price") if asking else None),
        ask1_price=_int_safe(asking.get("ask1_price") if asking else None),
        # 외인/기관/프로그램
        foreign_net_buy=_int_safe(investor.get("foreign_net_buy") if investor else None),
        institution_net_buy=_int_safe(investor.get("institution_net_buy") if investor else None),
        individual_net_buy=_int_safe(investor.get("individual_net_buy") if investor else None),
        program_net_buy=_int_safe(investor.get("program_net_buy") if investor else None),
        foreign_net_buy_value=_int_safe(investor.get("foreign_net_buy_value") if investor else None),
        institution_net_buy_value=_int_safe(investor.get("institution_net_buy_value") if investor else None),
        # 수급 Δ
        investor_delta_foreign_value=_int_safe(inv_d.get("foreign_value")),
        investor_delta_institution_value=_int_safe(inv_d.get("institution_value")),
        investor_delta_program_qty=_int_safe(inv_d.get("program_qty")),
        investor_delta_elapsed_sec=_int_safe(inv_d.get("elapsed_sec")),
        # VWAP/MA
        price_vs_vwap_pct=_float_safe(vwap_pct),
        price_vs_ma5_pct=_float_safe(ma5_pct),
        price_vs_ma20_pct=_float_safe(ma20_pct),
        # 다이버전스
        divergence_bearish=div_bear,
        divergence_bullish=div_bull,
        divergence_price_change_pct=div_pc,
        divergence_vp_5ma_delta=div_vd,
        # 거래량
        volume_ratio_vs_prev_day=_float_safe(volume_ratio),
        # 상한가
        limit_up_hit_time=lut_str,
        # Buy.Score
        buy_score=_float_safe(getattr(monitored, "buy_score", None)),
        buy_grade=getattr(monitored, "buy_grade", None),
        buy_reasons=list(getattr(monitored, "buy_reasons", []) or []),
        # 매매법 분류 (P1-4) — worker 가 monitored 에 stash, buy_score 와 동일 패턴
        setup_label=getattr(monitored, "setup_label", None),
        setup_score_breakout=_float_safe(getattr(monitored, "setup_score_breakout", None)),
        setup_score_pullback=_float_safe(getattr(monitored, "setup_score_pullback", None)),
        setup_chase_warning=bool(getattr(monitored, "setup_chase_warning", False)),
        # Exit.Triggers 트리거
        trigger_a1_stop_price=bool(trigger_states.get("A1_stop_price", False)),
        trigger_a2_stop_bar_low=bool(trigger_states.get("A2_stop_bar_low", False)),
        trigger_a3_stop_ma=bool(trigger_states.get("A3_stop_ma", False)),
        trigger_a4_stop_time=bool(trigger_states.get("A4_stop_time", False)),
        trigger_a5_eod_ma_break=bool(trigger_states.get("A5_eod_ma_break", False)),
        trigger_p1_take_profit_1=bool(trigger_states.get("P1_take_profit_1", False)),
        trigger_p2_take_profit_2=bool(trigger_states.get("P2_take_profit_2", False)),
        trigger_p3_trailing=bool(trigger_states.get("P3_trailing", False)),
        trigger_e1_vp_below_100=bool(trigger_states.get("E1_vp_below_100", False)),
        trigger_e2_bearish_divergence=bool(trigger_states.get("E2_bearish_divergence", False)),
        trigger_e3_vol_drain=bool(trigger_states.get("E3_vol_drain", False)),
        trigger_e4_bearish_candle=bool(trigger_states.get("E4_bearish_candle", False)),
        trigger_e5_vi_failure=bool(trigger_states.get("E5_vi_failure", False)),
        # funnel
        funnel_evaluated=funnel_evaluated,
        funnel_passed_rising=bool(getattr(monitored, "is_rising", False)),
        # 보유
        holding_entry_price=entry_price,
        holding_entry_time=entry_time_iso,
        holding_elapsed_sec=elapsed_sec,
        holding_pnl_pct=pnl_pct,
    )
