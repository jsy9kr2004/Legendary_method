"""사이징 계산 (R6).

3가지 방법 모두 계산 → 레포트에 함께 표시 → Zeta가 직관 선택.

방법 1: 균등
    weight_i = 1 / N

방법 2: Kelly Criterion
    f* = p/L - q/W
      where p = 갭상 확률
            W = avg_gap_when_up (양수)
            L = avg_gap_when_dn (양수, 절대값)
            q = 1 - p

    표본 보정 (R6):
        n < 5  → 시그널 제외 (None 반환)
        n < 10 → f* × 0.3
        n < 20 → f* × 0.6
        n >= 20 → f* × 0.8 (Half Kelly 권장)

    캡: max 25% per stock
    음수 f*: 0 (베팅 안 함)

방법 3: Sharpe-like
    expected = p × avg_gap_when_up
    score    = expected / std_gap   (std_gap == 0 이면 expected)
    weight_i = score_i / sum(score)  (음수 score는 0으로 처리)

기준 Layer:
    historical.pick_sizing_layer() 결과 사용 (기본 Layer 3, fallback L2/L1).
"""
from __future__ import annotations

import math
from typing import Any

from loguru import logger

KELLY_MAX_FRACTION = 0.25
KELLY_HALF = 0.8


def _kelly_sample_factor(n: int) -> float | None:
    """표본 수에 따른 Kelly 보정 계수.

    Returns:
        n < 5 이면 None (시그널 제외).
    """
    if n < 5:
        return None
    if n < 10:
        return 0.3
    if n < 20:
        return 0.6
    return KELLY_HALF


def kelly_fraction(stats: dict[str, Any]) -> float | None:
    """단일 종목의 Kelly 분할 (표본 보정 + 캡 적용).

    Args:
        stats: layer 통계 dict (n, p, avg_gap_when_up, avg_gap_when_dn, ...)

    Returns:
        분할 비율 (0.0~0.25). 표본 부족 시 None. 음수면 0.

    구현 노트 (D1):
        고전 Kelly 는 W, L 을 "단위 베팅당 손익 비율(decimal)" 로 다룬다.
        본 코드는 historical 의 % 값을 그대로 (W=2.5 → 2.5) 대입해 결과가
        대략 [0, 0.25] 범위로 떨어지도록 한 휴리스틱이다. 이는 정통 Kelly
        와 결과 스케일이 다르며, 향후 데이터 누적 후 정통 식 (W, L 을 /100)
        과 비교 검증할 것.
    """
    n = int(stats.get("n", 0))
    factor = _kelly_sample_factor(n)
    if factor is None:
        return None

    p = float(stats.get("p", float("nan")))
    W = float(stats.get("avg_gap_when_up", float("nan")))
    L = float(stats.get("avg_gap_when_dn", float("nan")))

    if math.isnan(p) or math.isnan(W) or W <= 0:
        return 0.0

    if math.isnan(L) or L <= 0:
        # 갭하 사례 0건 → 우호적이지만 Kelly 공식이 정의 불가능.
        # sample factor 는 적용해서 표본 보정 철학 유지 (H2 수정).
        return min(KELLY_MAX_FRACTION * factor, KELLY_MAX_FRACTION)

    q = 1.0 - p
    raw = (p / L) - (q / W)
    adjusted = max(0.0, raw * factor)
    return min(adjusted, KELLY_MAX_FRACTION)


def sharpe_score(stats: dict[str, Any]) -> float:
    """단일 종목의 Sharpe-like score.

    score = (p × avg_gap_when_up) / std_gap
    std_gap == 0 → score = expected (분모 0 회피).
    음수/NaN 은 0 으로 처리.
    """
    p = float(stats.get("p", 0.0))
    W = float(stats.get("avg_gap_when_up", 0.0))
    std = float(stats.get("std_gap", 0.0))

    if math.isnan(p) or math.isnan(W) or W <= 0:
        return 0.0
    expected = p * W
    if math.isnan(std) or std <= 0:
        return max(expected, 0.0)
    return max(expected / std, 0.0)


def equal_weights(n_signals: int) -> list[float]:
    """균등 분할."""
    if n_signals <= 0:
        return []
    return [1.0 / n_signals] * n_signals


def compute_sizing(
    candidates: list[dict[str, Any]],
) -> dict[str, list[float | None]]:
    """후보 리스트 → 3가지 사이징 weights 동시 계산.

    Args:
        candidates: 각 종목 {code, name, sizing_stats: {...}, ...} 리스트.
                    sizing_stats 는 historical.pick_sizing_layer() 결과의 stats dict.

    Returns:
        {
          "equal":  [1/N, 1/N, ...],
          "kelly":  [f*_1, f*_2, ...],   # None 은 표본부족 종목
          "sharpe": [w_1, w_2, ...],     # 정규화된 가중치
        }
        세 리스트 길이는 모두 candidates 와 동일.
    """
    n = len(candidates)
    if n == 0:
        return {"equal": [], "kelly": [], "sharpe": []}

    eq = equal_weights(n)

    kelly_list: list[float | None] = [
        kelly_fraction(c.get("sizing_stats", {})) for c in candidates
    ]

    raw_scores = [sharpe_score(c.get("sizing_stats", {})) for c in candidates]
    total = sum(raw_scores)
    if total > 0:
        sharpe_w: list[float] = [s / total for s in raw_scores]
    else:
        sharpe_w = [0.0] * n

    excluded = sum(1 for k in kelly_list if k is None)
    if excluded:
        logger.info(f"Kelly 표본 부족(n<5)으로 {excluded}개 종목 제외")

    return {"equal": eq, "kelly": kelly_list, "sharpe": sharpe_w}
