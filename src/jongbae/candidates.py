"""종배 후보 추출 (R4).

정량 정의:
    조건 (모두 만족):
      (a) R1, R2, R3 통과 (시장 국면은 사람 판정, 유니버스/주도테마는 호출부에서)
      (b) 일봉 종가 수익률 >= +20%
      (c) historical 유사 사례 >= 5건 (R5에서 별도 검증)

진입 우선순위:
    PRIORITY_LIMIT_UP   (1순위): 주도테마 + 일봉 +20%↑ + 상한가 도달
    PRIORITY_HIGH_PULL  (2순위): 일중 +28%↑ 찍고 +20~25% 영역으로 정리
    PRIORITY_NORMAL     (3순위): 그 외 +20%↑ 조건 만족 (주도테마 안)

제외 (애매한 케이스):
    - +28% 찍고 안 빠지고 +28%~30% 그대로 마감 (상한가 못 갔는데 자리 잡힘)
    - 일중 +30% 찍고 +5%로 떡락 (시세 죽음)
    - 비주도테마 (이 모듈 호출 전에 leading_theme로 필터링)
"""
from __future__ import annotations

from typing import Any

import pandas as pd
from loguru import logger

MIN_DAILY_RETURN = 20.0
INTRADAY_HIGH_THRESHOLD = 28.0
HIGH_PULL_RANGE = (20.0, 25.0)
DEAD_PULL_THRESHOLD = 5.0   # 일중 +28% 이후 종가 +5% 이하 → "떡락" 제외
STUCK_AT_28_RANGE = (28.0, 29.5)  # +28~29.5% 그대로 마감 → "자리잡힘" 제외

PRIORITY_LIMIT_UP = "limit_up"
PRIORITY_HIGH_PULL = "high_pull"
PRIORITY_NORMAL = "normal"
PRIORITY_EXCLUDED = "excluded"

CANDIDATE_COLUMNS = [
    "code", "name", "rank", "price", "prev_close",
    "daily_return", "intraday_high", "intraday_high_pct",
    "is_limit_up", "priority", "exclusion_reason",
]


def _intraday_high_pct(intraday_high: int, prev_close: int) -> float:
    """일중 고가의 전일 대비 수익률(%)."""
    if prev_close <= 0:
        return 0.0
    return (intraday_high - prev_close) / prev_close * 100.0


def classify_priority(row: pd.Series) -> tuple[str, str | None]:
    """단일 종목의 진입 우선순위 분류.

    Returns:
        (priority, exclusion_reason) — exclusion_reason은 priority가 EXCLUDED일 때만 채움.
    """
    daily_return = float(row.get("daily_return", 0.0))
    is_limit_up = bool(row.get("is_limit_up", False))
    intraday_high_pct = float(row.get("intraday_high_pct", 0.0))

    # 1순위: 상한가 도달
    if is_limit_up:
        return PRIORITY_LIMIT_UP, None

    # 제외 케이스 1: +30% 찍고 +5% 이하로 떡락
    if intraday_high_pct >= INTRADAY_HIGH_THRESHOLD and daily_return <= DEAD_PULL_THRESHOLD:
        return PRIORITY_EXCLUDED, f"일중 +{intraday_high_pct:.1f}% 후 종가 +{daily_return:.1f}%로 떡락"

    # 제외 케이스 2: +28~29.5% 그대로 마감 (상한가 못 갔는데 자리잡힘)
    lo, hi = STUCK_AT_28_RANGE
    if lo <= daily_return < hi and intraday_high_pct < 30.0:
        return PRIORITY_EXCLUDED, f"+{daily_return:.1f}% 그대로 마감 (상한가 미도달)"

    # 2순위: 일중 +28%↑ → 종가 +20~25%로 정리
    pull_lo, pull_hi = HIGH_PULL_RANGE
    if intraday_high_pct >= INTRADAY_HIGH_THRESHOLD and pull_lo <= daily_return <= pull_hi:
        return PRIORITY_HIGH_PULL, None

    # 3순위: 일반 +20%↑
    if daily_return >= MIN_DAILY_RETURN:
        return PRIORITY_NORMAL, None

    # +20% 미만은 후보 아님 (호출부에서 필터링됨)
    return PRIORITY_EXCLUDED, f"일봉 +{daily_return:.1f}% (<+{MIN_DAILY_RETURN}%)"


def extract_candidates(
    snapshot_df: pd.DataFrame,
    leading_theme_codes: list[str],
) -> pd.DataFrame:
    """주도테마 종목 + 일봉 +20%↑ 필터로 종배 후보 추출.

    Args:
        snapshot_df: 거래대금 순위 스냅샷 (intraday.SNAPSHOT_COLUMNS)
        leading_theme_codes: 주도테마에 속한 종목 코드 리스트

    Returns:
        CANDIDATE_COLUMNS 스키마 DataFrame.
        priority 별 정렬: limit_up → high_pull → normal → excluded
        excluded 는 디버그/레포트 표시용으로 함께 반환.
    """
    if snapshot_df.empty:
        return pd.DataFrame(columns=CANDIDATE_COLUMNS)
    if not leading_theme_codes:
        logger.debug("종배 후보 추출: 주도테마 종목 없음")
        return pd.DataFrame(columns=CANDIDATE_COLUMNS)

    in_theme = snapshot_df[snapshot_df["code"].astype(str).isin(set(leading_theme_codes))].copy()
    if in_theme.empty:
        return pd.DataFrame(columns=CANDIDATE_COLUMNS)

    in_theme["intraday_high_pct"] = in_theme.apply(
        lambda r: _intraday_high_pct(int(r.get("intraday_high", 0)), int(r.get("prev_close", 0))),
        axis=1,
    )

    priorities = in_theme.apply(classify_priority, axis=1)
    in_theme["priority"] = [p[0] for p in priorities]
    in_theme["exclusion_reason"] = [p[1] for p in priorities]

    # 일봉 +20% 미만은 명시적으로 제외 처리
    in_theme.loc[in_theme["daily_return"] < MIN_DAILY_RETURN, "priority"] = PRIORITY_EXCLUDED

    priority_order = {
        PRIORITY_LIMIT_UP: 0,
        PRIORITY_HIGH_PULL: 1,
        PRIORITY_NORMAL: 2,
        PRIORITY_EXCLUDED: 3,
    }
    in_theme["_pri_order"] = in_theme["priority"].map(priority_order)
    in_theme = in_theme.sort_values(["_pri_order", "rank"]).drop(columns=["_pri_order"])

    cols = [c for c in CANDIDATE_COLUMNS if c in in_theme.columns]
    out = in_theme[cols].reset_index(drop=True)

    accepted = out[out["priority"] != PRIORITY_EXCLUDED]
    logger.info(
        f"종배 후보 {len(accepted)}개 추출 "
        f"(상한가 {sum(out['priority'] == PRIORITY_LIMIT_UP)}, "
        f"고점풀백 {sum(out['priority'] == PRIORITY_HIGH_PULL)}, "
        f"일반 {sum(out['priority'] == PRIORITY_NORMAL)}, "
        f"제외 {sum(out['priority'] == PRIORITY_EXCLUDED)})"
    )
    return out


def accepted_candidates(candidates_df: pd.DataFrame) -> pd.DataFrame:
    """제외 케이스를 뺀 채택 후보만."""
    if candidates_df.empty:
        return candidates_df
    return candidates_df[candidates_df["priority"] != PRIORITY_EXCLUDED].reset_index(drop=True)
