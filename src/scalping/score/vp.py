"""체결강도 (Volume Power, VP) — R10.

`docs/jongbae-strategy.md` R10 참조. 정정 이력 round 13.

정의:
    VP = 능동 매수체결량 / 능동 매도체결량 × 100   (당일 누적)
    VP = 100  → 매수체결 = 매도체결 (균형선)
    VP > 100  → 매수 우세 (체결 기준; 호가 잔량과 별개)

장중 시계열은 메모리 deque 로 보관 (영구 적재는 v1). 5MA/20MA 는
deque tail 평균으로 산출. 워밍업이 부족하면 NaN.

호가 잔량과의 관계: 호가는 메인에서 보조로 강등 — 허매수/스푸핑/시장가 매도
함정. VP 가 매수 강도 메인 시그널.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Deque

from src.scalping.score.thresholds import (
    VP_BALANCED,
    VP_MA_LONG_MINUTES,
    VP_MA_SHORT_MINUTES,
    VP_STRONG_THRESHOLD,
    VP_WEAK_THRESHOLD,
)


@dataclass
class VPSeries:
    """단일 종목 VP 시계열 (장중 메모리).

    1초 단위 sample 가정. 20분 윈도우 = maxlen 1200.
    실제 fetch 주기가 더 길면 (예: 3초) `maxlen` 자동으로 충분.
    """
    samples: Deque[tuple[datetime, float]] = field(
        default_factory=lambda: deque(maxlen=1200)
    )

    def push(self, ts: datetime, vp: float) -> None:
        """새 VP 샘플 추가. NaN/None 은 무시."""
        if vp is None or vp != vp:  # NaN
            return
        self.samples.append((ts, float(vp)))

    def latest(self) -> float:
        """현재 VP (가장 최근 샘플). 없으면 NaN."""
        if not self.samples:
            return float("nan")
        return self.samples[-1][1]

    def ma(self, now: datetime, window_minutes: int) -> float:
        """`now` 기준 직전 `window_minutes` 분의 평균 VP.

        샘플이 windows 의 50% 미만이면 NaN (워밍업 미달).
        """
        if not self.samples:
            return float("nan")
        cutoff = now - timedelta(minutes=window_minutes)
        in_window = [v for ts, v in self.samples if ts >= cutoff]
        if not in_window:
            return float("nan")
        # 워밍업 가드: 최소 절반 이상의 샘플은 있어야 신뢰
        # (1초 1샘플 가정. 실제 주기에 따라 보수적으로 작동)
        min_samples = max(1, window_minutes * 30)  # 분당 30 = 2초 1샘플도 OK
        # 실측 sample rate 추정
        if len(self.samples) >= 2:
            span = (self.samples[-1][0] - self.samples[0][0]).total_seconds()
            if span > 0:
                rate_per_sec = len(self.samples) / span
                min_samples = max(1, int(window_minutes * 60 * rate_per_sec * 0.5))
        if len(in_window) < min_samples:
            return float("nan")
        return sum(in_window) / len(in_window)

    def ma_1(self, now: datetime) -> float:
        """1분 이동평균. 카드 표시용 — 5MA 대비 더 빠른 약화 인지.
        트리거(R15 C1)는 여전히 5MA 기준 — 1MA 는 노이즈 가능성으로 정보 표시용.
        """
        return self.ma(now, 1)

    def ma_5(self, now: datetime) -> float:
        return self.ma(now, VP_MA_SHORT_MINUTES)

    def ma_20(self, now: datetime) -> float:
        return self.ma(now, VP_MA_LONG_MINUTES)


# ── 임계 판정 (R14 매수 점수 / R15 매도 트리거 입력) ─────────────────────────


def is_vp_strong(vp: float, vp_5ma: float) -> bool:
    """R14 +2: VP > 110 AND VP_5MA > 100. 강한 매수 체결 우세."""
    if vp != vp or vp_5ma != vp_5ma:
        return False
    return vp > VP_STRONG_THRESHOLD and vp_5ma > VP_BALANCED


def is_vp_weak(vp: float) -> bool:
    """R14 -2: VP < 100. 매수 압력 약함."""
    if vp != vp:
        return False
    return vp < VP_WEAK_THRESHOLD


def crossed_below_balanced(prev_5ma: float, cur_5ma: float) -> bool:
    """R15 C1: VP_5MA 가 100 을 하향 돌파했는지.

    이전 tick 에서 ≥ 100 이었고 이번에 < 100. 멱등하지 않음 — 호출자가 한번만
    발화하도록 boolean 기록 필요.
    """
    if prev_5ma != prev_5ma or cur_5ma != cur_5ma:
        return False
    return prev_5ma >= VP_BALANCED and cur_5ma < VP_BALANCED
