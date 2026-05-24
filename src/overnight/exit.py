"""종배 청산 시초가 룰 (round 30, P3-2).

`docs/scalping-strategy.md` 종배 청산 정책 참조.

Exit.Triggers (장중 보유 모니터링) 과 분리된 모듈 — 다음날 09:00 KRX 시초가 형성
직후 1회 판정용. 종배 매매 모델의 본질적 청산 시점.

통설 (WikiDocs 종가베팅, brokdam 광전자 6/4 케이스):
    시초 ≤ +1% (또는 마이너스)  → 갭 실패, 전량 매도 (보유 무의미)
    시초 +1% ~ +6%               → 정상 갭, 전량 익절 정석
    시초 ≥ +6%                  → 강한 갭, 30~50% 분할 익절 후 관망

자동 매매 금지 (CLAUDE.md):
    이 모듈도 권고만 반환. 실주문 X. 09:00 텔레그램 알림 메시지로
    "전량 매도 권고" / "분할 익절 권고 (40%)" 를 사용자에게 표시.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from src.scalping.score.thresholds import (
    JONGBAE_OPEN_FULL_SELL_MAX_PCT,
    JONGBAE_OPEN_PARTIAL_RATIO,
    JONGBAE_OPEN_PARTIAL_SELL_MIN_PCT,
)

JongbaeExitAction = Literal["sell_all", "sell_partial"]


def _classify_gap(gap_pct: float) -> tuple[JongbaeExitAction, float]:
    """갭률(%) → (action, partial_ratio). 검증 임계값 단일 출처.

    ≤ FULL_SELL_MAX  → 전량 (갭 미발생/소멸)
    ≥ PARTIAL_MIN    → 분할 (강한 갭, 일부 관망)
    그 사이          → 전량 (정상 갭 익절)
    """
    if gap_pct <= JONGBAE_OPEN_FULL_SELL_MAX_PCT:
        return "sell_all", 1.0
    if gap_pct >= JONGBAE_OPEN_PARTIAL_SELL_MIN_PCT:
        return "sell_partial", JONGBAE_OPEN_PARTIAL_RATIO
    return "sell_all", 1.0


@dataclass(frozen=True)
class JongbaeExitDecision:
    """시초가 청산 판정 결과 (09:00 1회).

    Attributes:
        action: "sell_all" (전량) 또는 "sell_partial" (분할).
        partial_ratio: 매도 비중 0.0 ~ 1.0. sell_all 이면 1.0.
        open_gap_pct: 시초가 / 전일종가 - 1 (%).
        reason: 카드/알림용 한 줄 사유.
    """
    action: JongbaeExitAction
    partial_ratio: float
    open_gap_pct: float
    reason: str


def evaluate_jongbae_open_exit(
    open_price: float,
    prev_close: float,
) -> JongbaeExitDecision:
    """다음날 09:00 KRX 시초가 형성 직후 청산 판정 (round 30, P3-2).

    Args:
        open_price: 다음날 시초가 (원). KRX 단일가 09:00 형성.
        prev_close: 매수일 종가 (원). 종배 진입 기준가.

    Returns:
        JongbaeExitDecision: 전량/분할/사유.

    Raises:
        ValueError: open_price 또는 prev_close 가 0 이하.
    """
    if open_price <= 0 or prev_close <= 0:
        raise ValueError(
            f"open_price={open_price}, prev_close={prev_close} — 양수 필요"
        )

    gap_pct = (open_price - prev_close) / prev_close * 100.0
    action, ratio = _classify_gap(gap_pct)
    if action == "sell_partial":
        reason = (
            f"시초 +{gap_pct:.2f}% — 강한 갭, "
            f"{int(ratio * 100)}% 익절 후 관망"
        )
    elif gap_pct <= JONGBAE_OPEN_FULL_SELL_MAX_PCT:
        reason = f"시초 {gap_pct:+.2f}% — 갭 미발생, 전량 매도"
    else:
        reason = f"시초 +{gap_pct:.2f}% — 정상 갭, 전량 익절 정석"
    return JongbaeExitDecision(
        action=action, partial_ratio=ratio, open_gap_pct=gap_pct, reason=reason,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 라이브 청산 지원 (2026-05-25) — 시초 1회가 아니라 09:00~ 다회 체크인.
#
# 배경 (backtest_recent_kelly.py / memory project-eod-factor-edge):
#   top3 매도 시점 envelope = 시초 +0.7% / 일중최저 -3.7% / 일중최고 +5.5% — 폭 ~9%p.
#   "어디서 파느냐" 가 "무엇을 고르느냐"(+0.7%) 보다 13배 큰 변수. 청산 타이밍이 본 게임.
#
# 정직한 제약 (ritual): 청산 타이밍은 분봉 히스토리 부재로 backtest 불가 →
#   **새 자작 임계값 금지.** 검증된 ≤1/1-6/≥6% 룰을 '시초' 대신 '현재가' 에 라이브
#   재평가(같은 임계값) + 고점 대비 되돌림을 '정보로 표시'. 매도 시점은 사람이 결정.
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class OvernightExitContext:
    """라이브(09:00~) 청산 지원 컨텍스트.

    Attributes:
        open_gap_pct: 시초 vs 전일종가 (open 미상이면 NaN).
        current_gap_pct: 현재가 vs 전일종가 (= 평가손익).
        pullback_from_high_pct: (현재-일중고가)/고가 (≤0, 고점 대비 되돌림).
        runup_from_open_pct: (현재-시초)/시초 (open 미상이면 NaN).
        decision: 검증 룰을 **현재가** 에 적용한 결과 (시초→현재 fade 자동 반영).
        note: 표시용 한 줄 (현재/시초/고점대비 — 임계값 아님, 정보).
    """
    open_gap_pct: float
    current_gap_pct: float
    pullback_from_high_pct: float
    runup_from_open_pct: float
    decision: JongbaeExitDecision
    note: str


def evaluate_overnight_exit_live(
    prev_close: float,
    current: float,
    intraday_high: float,
    open_price: float | None = None,
) -> OvernightExitContext:
    """09:00~ 라이브 청산 지원 — 검증 임계값을 현재가에 재평가 + 되돌림 표시.

    시초 +7%(분할 권고) 였다가 현재 +2%로 빠지면 → 현재가 기준 '전량 매도' 로
    자동 전환 (fade 포착). **새 임계값 X — 기존 ≤1/1-6/≥6% 를 현재가에 적용.**

    Args:
        prev_close: 매수일 종가 (진입 기준가).
        current: 현재가.
        intraday_high: 당일 일중 고가. current 보다 작게 들어오면 current 로 보정.
        open_price: 당일 시초가 (선택). 있으면 시초 갭/시초 대비 상승 표시.

    Returns:
        OvernightExitContext.

    Raises:
        ValueError: prev_close 또는 current 가 0 이하.
    """
    if prev_close <= 0 or current <= 0:
        raise ValueError(f"prev_close={prev_close}, current={current} — 양수 필요")

    cur_gap = (current - prev_close) / prev_close * 100.0
    action, ratio = _classify_gap(cur_gap)

    hi = intraday_high if (intraday_high and intraday_high > current) else current
    pull = (current - hi) / hi * 100.0 if hi > 0 else 0.0  # ≤ 0

    if open_price and open_price > 0:
        open_gap = (open_price - prev_close) / prev_close * 100.0
        runup = (current - open_price) / open_price * 100.0
    else:
        open_gap = float("nan")
        runup = float("nan")

    if action == "sell_partial":
        guide = f"강한 갭 — {int(ratio * 100)}% 익절 후 관망"
    elif cur_gap <= JONGBAE_OPEN_FULL_SELL_MAX_PCT:
        guide = "갭 미발생/소멸 — 전량 매도"
    else:
        guide = "정상 갭 — 전량 익절"
    decision = JongbaeExitDecision(
        action=action, partial_ratio=ratio, open_gap_pct=cur_gap, reason=guide,
    )

    note_parts = [f"현재 {cur_gap:+.2f}%"]
    if open_gap == open_gap:  # not NaN
        note_parts.append(f"시초 {open_gap:+.2f}%")
    note_parts.append(f"고점대비 {pull:.1f}%")
    note = " · ".join(note_parts)

    return OvernightExitContext(
        open_gap_pct=open_gap,
        current_gap_pct=cur_gap,
        pullback_from_high_pct=pull,
        runup_from_open_pct=runup,
        decision=decision,
        note=note,
    )


def format_overnight_exit_line(name: str, code: str, ctx: OvernightExitContext) -> str:
    """라이브 청산 카드 한 종목 (텔레그램 표시용).

    자동 주문 X — 권고 + 정보만. 매도 시점은 사용자가 결정.
    """
    emoji = "🟢" if ctx.decision.action == "sell_partial" else "🟡"
    return (
        f"{emoji} {name}({code}) — {ctx.decision.reason} "
        f"[매도 {int(ctx.decision.partial_ratio * 100)}%]\n"
        f"   {ctx.note}"
    )
