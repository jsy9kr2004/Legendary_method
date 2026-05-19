"""종배 후보 추출 (R4 v2 — round 41 → 2026-05-19 코드 적용).

정량 정의 (R4 v2 (e) 부분 적용):
    조건 (모두 만족):
      (a) R1, R2, R3 통과 (시장 국면은 사람 판정, 유니버스/주도테마는 호출부에서)
      (b) **일봉 종가 수익률 10% ≤ ret ≤ 27%** ← R4 v2 (e)
      (c) historical 유사 사례 >= 5건 (R5에서 별도 검증)

    상한가 (+30%≈) 및 +28~29.5% "자리잡힘" 케이스는 상한 27% 컷에 의해 자동 제외.
    `(a) 거래대금 50위 단일종목` `(c) 종가 고가-10% 이내` `(d) 52주 신고가`
    `(f) Layer 표본 ≥5` 는 별도 round 작업 — plan.md round 41 TODO 참조.

진입 우선순위 (eligible 범위 내):
    PRIORITY_HIGH_PULL  (1순위): 일중 +28%↑ 찍고 +20~25% 영역으로 정리
    PRIORITY_NORMAL     (2순위): 그 외 10~27% 조건 만족

제외:
    - daily_return < 10%  → "+x.x% (<10%)"
    - daily_return > 27%  → "+x.x% (>27%, R4 v2 상한 컷)" (상한가 / 자리잡힘 포함)
    - 비주도테마 (이 모듈 호출 전에 leading_theme로 필터링)

정정 이력:
    - 2026-05-19 round 41 (e) 코드 적용: MIN 20→10, MAX 추가 27. 사용자 보고 —
      진원생명과학(011000) +29.97% 가 후보로 진입한 회귀 fix.
      `STUCK_AT_28_RANGE` / `DEAD_PULL_THRESHOLD` 제거 (모두 새 컷 범위 밖이라
      자동 제외). `PRIORITY_LIMIT_UP` 상수는 backward-compat 위해 유지하지만
      classify_priority 가 더는 반환하지 않음 (상한가 종목은 +30%≈ → 27% 초과).
"""
from __future__ import annotations

from typing import Any

import pandas as pd
from loguru import logger

MIN_DAILY_RETURN = 10.0   # R4 v2 (e) 하한 (round 41 이전: 20.0)
MAX_DAILY_RETURN = 27.0   # R4 v2 (e) 상한 (round 41 신규)
INTRADAY_HIGH_THRESHOLD = 28.0
HIGH_PULL_RANGE = (20.0, 25.0)

PRIORITY_LIMIT_UP = "limit_up"     # round 41 이후 classify_priority 는 반환 X (상한가는 27% 초과로 자동 제외). 상수는 storage / 다른 알림 경로 호환 위해 유지.
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
    """단일 종목의 진입 우선순위 분류 (R4 v2 (e)).

    Returns:
        (priority, exclusion_reason) — exclusion_reason은 priority가 EXCLUDED일 때만 채움.
    """
    daily_return = float(row.get("daily_return", 0.0))
    intraday_high_pct = float(row.get("intraday_high_pct", 0.0))

    # R4 v2 (e) 상한 컷 — 상한가(+30%) / 자리잡힘(+28~29.5%) 모두 여기서 제외.
    if daily_return > MAX_DAILY_RETURN:
        return PRIORITY_EXCLUDED, f"일봉 +{daily_return:.1f}% (>{MAX_DAILY_RETURN:.0f}%, R4 v2 상한 컷)"

    # R4 v2 (e) 하한 컷
    if daily_return < MIN_DAILY_RETURN:
        return PRIORITY_EXCLUDED, f"일봉 +{daily_return:.1f}% (<{MIN_DAILY_RETURN:.0f}%)"

    # 1순위 (eligible 내): 일중 +28%↑ → 종가 +20~25%로 정리
    pull_lo, pull_hi = HIGH_PULL_RANGE
    if intraday_high_pct >= INTRADAY_HIGH_THRESHOLD and pull_lo <= daily_return <= pull_hi:
        return PRIORITY_HIGH_PULL, None

    # 2순위: 그 외 10~27% 일반
    return PRIORITY_NORMAL, None


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

    # R4 v2 (e) — 10% ≤ ret ≤ 27% 범위 외는 명시적으로 제외 (round 41)
    in_theme.loc[
        (in_theme["daily_return"] < MIN_DAILY_RETURN)
        | (in_theme["daily_return"] > MAX_DAILY_RETURN),
        "priority",
    ] = PRIORITY_EXCLUDED

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
