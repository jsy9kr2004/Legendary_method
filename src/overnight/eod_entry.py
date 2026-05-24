"""종배 막판 진입 점검 (15:00~15:30) — 표시 전용 (2026-05-25).

14:50 결정은 "후보"고, 영상 통설은 **장 막판(3시~3시30분) 흔들림을 보고 진입**하라고 함
(매수세 역전 확인, 무너지면 매수 X). 점상한가 이탈 여부도 막판에 갈림.

정직한 제약 (ritual): 막판 매수 타이밍은 분봉 히스토리 부재로 backtest 불가 →
    **새 종배 매수 hard rule 금지.** 검증된 단타 신호(체결강도 VP / 점상한가 / 고점대비
    되돌림)를 **정보로 표시**, 진입 여부/시점은 사람이 결정. M6 라이브 카드 아님 —
    14:50 top3 후보 대상 discrete 체크인 (15:00/10/20).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EodEntryContext:
    """막판 진입 점검 컨텍스트 (표시 전용)."""
    gap_now_pct: float            # 현재 vs 전일종가 (현재 일봉 등락률)
    is_limit_up: bool
    pullback_from_high_pct: float  # 현재 vs 일중고가 (≤0)
    vp: float | None              # 체결강도 (100=균형, >100 매수우위)
    vp_5ma: float | None
    vol_accel: float | None       # 분봉 거래대금 가속 (옵션)
    weak_candle: bool | None      # 윗꼬리 음봉 등 (옵션)
    summary: str                  # 한 줄 요약 (임계값 아님 — 정보)


def build_eod_entry_context(
    prev_close: float,
    price: float,
    intraday_high: float,
    is_limit_up: bool,
    vp: float | None = None,
    vp_5ma: float | None = None,
    vol_accel: float | None = None,
    weak_candle: bool | None = None,
) -> EodEntryContext:
    """막판 진입 점검 컨텍스트 조립 (표시 전용, hard rule X).

    Raises:
        ValueError: prev_close 또는 price 가 0 이하.
    """
    if prev_close <= 0 or price <= 0:
        raise ValueError(f"prev_close={prev_close}, price={price} — 양수 필요")
    gap = (price - prev_close) / prev_close * 100.0
    hi = intraday_high if (intraday_high and intraday_high > price) else price
    pull = (price - hi) / hi * 100.0 if hi > 0 else 0.0

    cues: list[str] = [f"현재 {gap:+.1f}%"]
    if is_limit_up:
        cues.append("🔒상한가 유지")
    else:
        cues.append(f"고점대비 {pull:.1f}%")
    if vp is not None:
        arrow = ""
        if vp_5ma is not None:
            arrow = "↑" if vp > vp_5ma else "↓"
        cues.append(f"VP {vp:.0f}{arrow}")
    if vol_accel is not None:
        cues.append(f"가속 {vol_accel:.1f}x")
    if weak_candle:
        cues.append("⚠윗꼬리음봉")
    summary = " · ".join(cues)

    return EodEntryContext(
        gap_now_pct=gap,
        is_limit_up=bool(is_limit_up),
        pullback_from_high_pct=pull,
        vp=vp,
        vp_5ma=vp_5ma,
        vol_accel=vol_accel,
        weak_candle=weak_candle,
        summary=summary,
    )


def format_eod_entry_line(name: str, code: str, ctx: EodEntryContext, is_top3: bool = False) -> str:
    """막판 진입 점검 한 종목 줄 (텔레그램)."""
    star = "⭐" if is_top3 else "▸"
    return f"{star} {name}({code}) — {ctx.summary}"
