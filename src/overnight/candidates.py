"""종배 후보 추출 (Eod.Pick v2 — round 41 → 2026-05-19 코드 적용).

Eod.Pick v2 hard cut (탈락 조건):
    (a) 거래대금 50위 단일종목 — 호출부 universe (snapshot_df) 가 top 50.
    (b) 일봉 상승 — (e) 의 strict subset 이라 별도 코드 X.
    (c) 종가 고가-10% 이내 — apply_r4v2_post_filters 에서 hard cut.
    (e) 10% ≤ daily_return ≤ 27% — classify_priority 에서 hard cut.

Eod.Pick v2 soft 보조 지표 (표시만, 탈락 X — 2026-05-19 정정):
    (d) 52주 신고가 — apply_r4v2_post_filters 에서 is_52w_high 만 저장.
        결정 레포트 카드에 ✓/✗/— 로 표시.
    (f) Layer 표본 ≥5 — has_enough_samples. 표본 부족 시 Kelly 만 None,
        Sharpe/Equal 은 정상 산출. 카드에 ⚠ 경고만.
    그 외 (1년 ret≥10 갭상 비율 등) — historical_ret10_gap_stats 보조 정보.

진입 우선순위 (eligible 범위 내):
    PRIORITY_HIGH_PULL  (1순위): 일중 +28%↑ 찍고 +20~25% 영역으로 정리
    PRIORITY_NORMAL     (2순위): 그 외 10~27% 조건 만족

제외:
    - daily_return < 10%  → "+x.x% (<10%)"
    - daily_return > 27%  → "+x.x% (>27%, Eod.Pick v2 상한 컷)" (상한가 / 자리잡힘 포함)
    - 종가 고가 대비 -10% 초과 (apply_r4v2_post_filters (c))
    - 주도섹터는 호출부 인자로 Eod.Pick v1 호환만 — Eod.Pick v2 기본은 None (필터 우회).

정정 이력:
    - 2026-05-19 round 41 (e) 코드 적용: MIN 20→10, MAX 추가 27. 사용자 보고
      진원생명과학(011000) +29.97% 가 후보로 진입한 회귀 fix.
    - 2026-05-19 round 41 후속 (a)(c) 코드 적용: 주도섹터 필터 우회 +
      apply_r4v2_post_filters (c) 추가.
    - 2026-05-19 round 41 후속 (d)(f) hard→soft 정정: 사용자 "표본 한계 +
      (d)(f) hard cut 시 후보 0~1종목 좁아져 운영 의미 X. 보조 지표로만 활용".
      (d) 52주 신고가 / (f) Layer 표본 ≥5 hard cut 제거. 표시 + Kelly 산출
      가능여부 만 영향.
    - 2026-05-19 round 41 후속 MIN 10→5: 사용자 "ret 하한을 10에서 5로 낮춰줘".
      후보 풀 확보. 5% 미만은 단타 갭상 알파 약하다는 단편 가설 — 사용자 운영
      누적 데이터로 재검증 예정. 5일 backtest 결과상 하한 5% 가 (e) 의 본래 의도
      "단타 종배 정신" 과 충돌하지 않음.
"""
from __future__ import annotations

from typing import Any

import pandas as pd
from loguru import logger

MIN_DAILY_RETURN = 5.0        # Eod.Pick v2 (e) 하한 (round 41 후속 2026-05-19: 10→5 사용자 정정 — 후보 폭 확보)
MAX_DAILY_RETURN = 27.0       # Eod.Pick v2 (e) 상한 (round 41 신규)
MAX_DROP_FROM_HIGH_PCT = 10.0  # Eod.Pick v2 (c) 종가 고가-10% 이내
INTRADAY_HIGH_THRESHOLD = 28.0
HIGH_PULL_RANGE = (20.0, 25.0)

PRIORITY_LIMIT_UP = "limit_up"     # round 41 이후 classify_priority 는 반환 X (상한가는 27% 초과로 자동 제외). 상수는 storage / 다른 알림 경로 호환 위해 유지.
PRIORITY_HIGH_PULL = "high_pull"
PRIORITY_NORMAL = "normal"
PRIORITY_EXCLUDED = "excluded"

CANDIDATE_COLUMNS = [
    "code", "name", "rank", "volume_rank", "turnover_rank",
    "price", "prev_close",
    "daily_return", "intraday_high", "intraday_low", "intraday_high_pct",
    "volume", "trading_value",
    "is_limit_up", "market_cap", "turnover",
    "priority", "exclusion_reason",
]


def _intraday_high_pct(intraday_high: int, prev_close: int) -> float:
    """일중 고가의 전일 대비 수익률(%)."""
    if prev_close <= 0:
        return 0.0
    return (intraday_high - prev_close) / prev_close * 100.0


def classify_priority(row: pd.Series) -> tuple[str, str | None]:
    """단일 종목의 진입 우선순위 분류 (Eod.Pick v2 (e)).

    Returns:
        (priority, exclusion_reason) — exclusion_reason은 priority가 EXCLUDED일 때만 채움.
    """
    daily_return = float(row.get("daily_return", 0.0))
    intraday_high_pct = float(row.get("intraday_high_pct", 0.0))

    # Eod.Pick v2 (e) 상한 컷 — 상한가(+30%) / 자리잡힘(+28~29.5%) 모두 여기서 제외.
    if daily_return > MAX_DAILY_RETURN:
        return PRIORITY_EXCLUDED, f"일봉 +{daily_return:.1f}% (>{MAX_DAILY_RETURN:.0f}%, Eod.Pick v2 상한 컷)"

    # Eod.Pick v2 (e) 하한 컷
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
    leading_theme_codes: list[str] | None = None,
) -> pd.DataFrame:
    """Eod.Pick v2 종배 후보 추출 — 거래대금 50위 단일종목 + 10% ≤ ret ≤ 27% 컷.

    Args:
        snapshot_df: 거래대금 순위 스냅샷 (intraday.SNAPSHOT_COLUMNS).
            Eod.Pick v2 (a) 거래대금 50위 universe 는 호출부에서 `top_n=50` 으로
            fetch 한 스냅샷을 그대로 사용 (이 함수는 추가 rank 컷 X).
        leading_theme_codes: Eod.Pick v1 호환 인자. **None / 빈 리스트 = Eod.Pick v2 (round 41)
            기본 동작 — 주도섹터 필터 우회, 전체 universe 사용** (`docs/scalping-strategy.md`
            line 206: "결정 후보 universe 컷에서는 Eod.Pick v2 가 Theme 를 우회").
            list 가 주어지면 Eod.Pick v1 동작 — 그 코드들만 후보로 잡음 (backward-compat).

    Returns:
        CANDIDATE_COLUMNS 스키마 DataFrame.
        priority 별 정렬: high_pull → normal → excluded
        (limit_up 은 Eod.Pick v2 (e) 상한 컷에 자동 제외 — round 41 이후 비활성)
        excluded 는 디버그/레포트 표시용으로 함께 반환.
    """
    if snapshot_df.empty:
        return pd.DataFrame(columns=CANDIDATE_COLUMNS)

    if leading_theme_codes:
        # Eod.Pick v1 backward-compat: 주도섹터 필터 적용
        in_theme = snapshot_df[
            snapshot_df["code"].astype(str).isin(set(leading_theme_codes))
        ].copy()
    else:
        # Eod.Pick v2 (a) 기본: 주도섹터 필터 우회 — 전체 스냅샷 universe (호출부가
        # top 50 으로 잘라 넘김). docs/scalping-strategy.md round 41.
        in_theme = snapshot_df.copy()

    if in_theme.empty:
        return pd.DataFrame(columns=CANDIDATE_COLUMNS)

    in_theme["intraday_high_pct"] = in_theme.apply(
        lambda r: _intraday_high_pct(int(r.get("intraday_high", 0)), int(r.get("prev_close", 0))),
        axis=1,
    )

    priorities = in_theme.apply(classify_priority, axis=1)
    in_theme["priority"] = [p[0] for p in priorities]
    in_theme["exclusion_reason"] = [p[1] for p in priorities]

    # Eod.Pick v2 (e) — 10% ≤ ret ≤ 27% 범위 외는 명시적으로 제외 (round 41)
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


def apply_r4v2_post_filters(
    candidate_dicts: list[dict[str, Any]],
    daily_ohlcv: pd.DataFrame,
    today: Any,
) -> list[dict[str, Any]]:
    """Eod.Pick v2 post-filter — fetch_quote 보강 후 OHLCV 확정 상태에서 적용.

    Hard cut (탈락):
        (c) 종가 고가-10% 이내 — `(intraday_high - price) / intraday_high <= 10%`.
            떡락 패턴 (긴 윗꼬리) 제외.

    Soft indicator (표시만, 탈락 X — round 41 후속 2026-05-19 사용자 정정):
        (d) 52주 신고가 — `intraday_high > max(past 250 daily close)`.
            카드 보조 표시. 신고가 미달성 종목도 후보 유지.
        (f) Layer 표본 ≥ 5 — 본 함수 범위 X. 호출부의 has_enough_samples 가
            hard cut 하던 로직도 같은 round 에서 soft 로 전환.

    이유: 5일 backtest 표본 한계 + (d)(f) hard cut 적용 시 결과 후보 0~1종목
    수준으로 좁아져 운영 의미 X. 보조 지표로만 활용하는 게 의사결정에 더 유용
    (사용자 판단).

    Args:
        candidate_dicts: accepted 후보 dict list. fetch_quote 보강 후 권장 (intraday_high
            / price 가 0/NaN 이 아닌 상태). 0 이면 (c) skip.
        daily_ohlcv: 전체 종목 일봉.
        today: 기준 날짜 (`datetime.date`).

    Returns:
        (c) hard cut 통과 후보 list. 각 dict 에 r4v2_check (dict) 추가 — 표시용:
            {"close_within_10pct_high": bool|None, "is_52w_high": bool|None}.
    """
    from src.overnight.gap_stats import is_52w_high  # 순환 import 회피

    survived: list[dict[str, Any]] = []
    for c in candidate_dicts:
        code = str(c.get("code", "")).zfill(6)
        price = c.get("price") or 0
        high = c.get("intraday_high") or 0
        check: dict[str, Any] = {
            "close_within_10pct_high": None,
            "is_52w_high": None,
        }

        # (c) 종가 고가-10% 이내 — hard cut
        if high > 0 and price > 0:
            drop_pct = (high - price) / high * 100.0
            check["close_within_10pct_high"] = bool(drop_pct <= MAX_DROP_FROM_HIGH_PCT)
            if not check["close_within_10pct_high"]:
                c["r4v2_check"] = check
                c["exclusion_reason"] = (
                    f"종가 고가 대비 -{drop_pct:.1f}% (Eod.Pick v2 (c) -10% 컷)"
                )
                c["priority"] = PRIORITY_EXCLUDED
                continue

        # (d) 52주 신고가 — soft (표시만). True/False/None 다 후보 유지.
        check["is_52w_high"] = is_52w_high(daily_ohlcv, code, today, high)

        c["r4v2_check"] = check
        survived.append(c)

    if not survived:
        logger.info("[Eod.Pick v2] post-filter (c) 통과 종목 없음")
    return survived
