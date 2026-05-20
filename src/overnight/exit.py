"""종배 청산 시초가 룰 (round 30, P3-2).

`docs/jongbae-strategy.md` 종배 청산 정책 참조.

R15 (장중 보유 모니터링) 과 분리된 모듈 — 다음날 09:00 KRX 시초가 형성
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

    if gap_pct <= JONGBAE_OPEN_FULL_SELL_MAX_PCT:
        return JongbaeExitDecision(
            action="sell_all",
            partial_ratio=1.0,
            open_gap_pct=gap_pct,
            reason=f"시초 {gap_pct:+.2f}% — 갭 미발생, 전량 매도",
        )
    if gap_pct >= JONGBAE_OPEN_PARTIAL_SELL_MIN_PCT:
        return JongbaeExitDecision(
            action="sell_partial",
            partial_ratio=JONGBAE_OPEN_PARTIAL_RATIO,
            open_gap_pct=gap_pct,
            reason=(
                f"시초 +{gap_pct:.2f}% — 강한 갭, "
                f"{int(JONGBAE_OPEN_PARTIAL_RATIO * 100)}% 익절 후 관망"
            ),
        )
    return JongbaeExitDecision(
        action="sell_all",
        partial_ratio=1.0,
        open_gap_pct=gap_pct,
        reason=f"시초 +{gap_pct:.2f}% — 정상 갭, 전량 익절 정석",
    )
