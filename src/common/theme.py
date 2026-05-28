"""주도테마/주도주 식별 (Theme, Theme.Leader).

v0 (Sonnet 1차 구현, 폐기 예정):
    "거래대금 30위 ≥ 3종목" — 대형주 편향으로 부적합.

v1 (M5.5, 한국 단타 통설):
    1. 거래대금 상위 LEADING_SECTOR_TOP_N(=50) 종목 추출 (master 필터로
       ETF/펀드/리츠/스팩 사전 제외)
    2. 각 종목의 네이버 테마 리스트 조회 (한 종목 = 다중 테마)
    3. 테마별로 (a) breadth: +5%↑ 종목 수
                  (b) avg_return: 동일가중 평균 상승률
                  (c) turnover_sum: 회전율 합계
       세 지표를 z-score 정규화 후 가중합 → theme_score
    4. theme_score 상위 LEADING_SECTOR_COUNT(=3) = 주도섹터

주도주 (Theme.Leader):
    (가) 정통 (post-limit-up, 결정 레포트용): 주도섹터 내 first-mover 상한가
        도달 종목. `identify_leading_stocks` (기존 유지).
    (나) 고주파 (pre-limit-up, M6 모니터링용): 주도섹터 내 **회전율 1위**.
        시총 정규화로 대형주 자동 배제. `identify_early_morning_leaders`.

함정 방지:
    "종가 기준 거래대금 순위로 보면 빨리 상한가 친 진짜 주도주가 누락됨"
    → 호출부는 시점별 누적 거래대금 스냅샷을 사용해야 한다.

호환성:
    `count_themes` / `identify_leading_themes` v0 API 는 유지 (M2 후보 추출
    파이프라인에서 사용 중). v1 신설 함수 `score_leading_sectors` 가
    M5.5 신규 정의를 구현.
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Any

import pandas as pd
from loguru import logger

from src.scalping.score.thresholds import (
    RISING_STAGE1_TURNOVER_TOP_N,
    LEADER_CANDIDATE_RANK_MAX,
    LEADER_EXCLUDE_DAILY_RETURN_PCT,
    LEADER_MIN_DAILY_RETURN_PCT,
    LEADING_SECTOR_COUNT,
    LEADING_SECTOR_TOP_N,
    LEADING_STOCK_TOP_PER_SECTOR,
    SECTOR_BREADTH_RETURN_THRESHOLD,
    SECTOR_MIN_MEMBER_COUNT,
    SECTOR_WEIGHT_BREADTH,
    SECTOR_WEIGHT_RETURN,
    SECTOR_WEIGHT_TURNOVER,
)

LEADING_THEME_THRESHOLD = 3
DEFAULT_TOP_N = 30


def count_themes(
    snapshot_df: pd.DataFrame,
    theme_mapping_df: pd.DataFrame,
    top_n: int = DEFAULT_TOP_N,
) -> Counter:
    """스냅샷의 상위 top_n 종목 기준 테마 출현 빈도 카운트.

    Args:
        snapshot_df: SNAPSHOT_COLUMNS (rank, code, name, ...) DataFrame.
        theme_mapping_df: long format (code, theme, crawled_at) DataFrame.
        top_n: 거래대금 상위 몇 위까지 볼지.

    Returns:
        Counter({theme_name: count, ...})
    """
    if snapshot_df.empty:
        return Counter()

    top = snapshot_df.sort_values("rank").head(top_n)
    top_codes = set(top["code"].astype(str).tolist())

    if theme_mapping_df.empty:
        return Counter()

    matched = theme_mapping_df[theme_mapping_df["code"].astype(str).isin(top_codes)]
    return Counter(matched["theme"].tolist())


def identify_leading_themes(
    snapshot_df: pd.DataFrame,
    theme_mapping_df: pd.DataFrame,
    threshold: int = LEADING_THEME_THRESHOLD,
    top_n: int = DEFAULT_TOP_N,
) -> list[dict[str, Any]]:
    """주도테마 식별.

    정량 정의:
        주도테마 = 거래대금 상위 top_n위 내에서 동일 테마 종목이 threshold개 이상 출현

    Returns:
        [{"theme": "전기/전선", "count": 5, "codes": ["075180", ...]}, ...]
        count 내림차순 정렬.
    """
    if snapshot_df.empty:
        logger.debug("주도테마 식별: 빈 스냅샷")
        return []

    top = snapshot_df.sort_values("rank").head(top_n)
    top_codes = top["code"].astype(str).tolist()
    top_codes_set = set(top_codes)

    if theme_mapping_df.empty:
        logger.warning("주도테마 식별: 테마 매핑 데이터 없음 (테마 크롤러 먼저 실행 필요)")
        return []

    matched = theme_mapping_df[theme_mapping_df["code"].astype(str).isin(top_codes_set)]
    counts = Counter(matched["theme"].tolist())

    leading = []
    for theme, count in counts.most_common():
        if count < threshold:
            break  # most_common 정렬되어 있으므로 break
        codes_in_theme = matched[matched["theme"] == theme]["code"].astype(str).unique().tolist()
        # rank 순서로 정렬
        rank_map = {c: i for i, c in enumerate(top_codes)}
        codes_in_theme.sort(key=lambda c: rank_map.get(c, 99999))
        leading.append({
            "theme": theme,
            "count": count,
            "codes": codes_in_theme,
        })

    logger.info(
        f"주도테마 {len(leading)}개 식별 (threshold={threshold}, top_n={top_n}): "
        f"{[t['theme'] for t in leading]}"
    )
    return leading


def codes_in_leading_themes(leading_themes: list[dict[str, Any]]) -> list[str]:
    """주도테마에 포함된 모든 종목 코드의 union (중복 제거)."""
    seen: list[str] = []
    seen_set: set[str] = set()
    for t in leading_themes:
        for code in t.get("codes", []):
            if code not in seen_set:
                seen_set.add(code)
                seen.append(code)
    return seen


def identify_leading_stocks(
    snapshot_df: pd.DataFrame,
    leading_themes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """주도주 식별 (post-limit-up) — 주도테마 내 first-mover 상한가 종목.

    이 함수는 ★ 결정 레포트 / 사후 분석용. 상한가 도달 후의 주도주 정의.
    장초반 고주파 모니터링용 (상한가 도달 *전* 매수 후보) 은
    `identify_early_morning_leaders` 사용.

    정량 정의 (CLAUDE.md):
        주도주 = 주도테마 내에서 가장 빨리 상한가에 도달한 종목 (first-mover).

    구현 (스냅샷 기준):
        스냅샷에는 first-mover 시각이 직접 안 찍히므로 'rank 가 가장 높은
        상한가 종목' 을 first-mover proxy 로 사용. (거래대금 누적이 더 빨리
        쌓인 종목 = 더 일찍 거래량 폭증 = 더 일찍 상한가 진입)

    Returns:
        [{"code", "name", "theme", "rank", "price", "daily_return"}, ...]
        한 테마당 0개 또는 1개. 주도테마 순서로 정렬.
    """
    if snapshot_df.empty or not leading_themes:
        return []

    if "is_limit_up" not in snapshot_df.columns:
        return []

    lup = snapshot_df[snapshot_df["is_limit_up"]].copy()
    if lup.empty:
        return []

    leaders: list[dict[str, Any]] = []
    seen_codes: set[str] = set()
    for theme_info in leading_themes:
        theme = theme_info["theme"]
        codes_in_theme = set(theme_info.get("codes", []))
        # 주도테마 종목 중 상한가 친 것
        cand = lup[lup["code"].astype(str).isin(codes_in_theme)]
        if cand.empty:
            continue
        # rank 가 가장 좋은(=숫자 작은) = 거래대금 누적 가장 빨리 도달
        first_mover = cand.sort_values("rank").iloc[0]
        code = str(first_mover["code"])
        if code in seen_codes:
            continue  # 한 종목이 여러 주도테마에 속해도 한 번만
        seen_codes.add(code)
        leaders.append({
            "code": code,
            "name": str(first_mover.get("name", "")),
            "theme": theme,
            "rank": int(first_mover.get("rank", 0)),
            "price": int(first_mover.get("price", 0)),
            "daily_return": float(first_mover.get("daily_return", 0.0)),
        })

    return leaders


def identify_early_morning_leaders(
    snapshot_df: pd.DataFrame,
    leading_themes: list[dict[str, Any]],
    top_per_theme: int = LEADING_STOCK_TOP_PER_SECTOR,
    rank_max: int | None = None,
    exclude_daily_return_pct: float | None = None,
    min_daily_return_pct: float | None = None,
) -> list[dict[str, Any]]:
    """장초반 고주파 모니터링용 주도주 식별 (pre-limit-up). M5.5 재정의.

    목적 (사용자 명시):
        상한가는 거래정지(매수/매도 불가) 이므로, 상한가 도달 *전* 진입을
        노린다. 따라서 주도주 = 곧 상한가에 도달할 가능성이 높은 종목.

    정의 (Theme.Leader(나) M5.5):
        (1) 주도섹터 내,
        (2) **회전율(거래대금/시총) 상위** top_per_theme 종목.

    거래대금 절대값으로 1위 잡으면 항상 하이닉스/삼전이 나옴 → 회전율로
    시총 정규화하여 단타 자금이 실제로 들어온 종목 식별.
    상승률/거래대금 절대값은 표시만 (점수화 X — 검증 안 된 자작 공식 회피).

    한 종목이 여러 주도섹터에 속하면 themes 리스트에 합쳐서 한 번만 등장.

    Args:
        snapshot_df: SNAPSHOT_COLUMNS DataFrame. `turnover` 컬럼 필수.
                     (없거나 모두 NaN 이면 fallback 으로 거래대금 절대값 사용)
        leading_themes: identify_leading_themes() 결과 또는 score_leading_sectors().
        top_per_theme: 각 주도섹터에서 회전율 상위 몇 개 볼지.

    Returns:
        [{
            "code", "name", "themes": [...], "rank", "price",
            "daily_return", "is_limit_up", "turnover", "trading_value",
            "market_cap",
        }, ...]
        회전율 내림차순 정렬.
    """
    if snapshot_df.empty or not leading_themes:
        return []

    if rank_max is None:
        rank_max = LEADER_CANDIDATE_RANK_MAX
    if exclude_daily_return_pct is None:
        exclude_daily_return_pct = LEADER_EXCLUDE_DAILY_RETURN_PCT
    if min_daily_return_pct is None:
        min_daily_return_pct = LEADER_MIN_DAILY_RETURN_PCT

    # 후보 자격 (사용자 명시):
    #   (1) 절대 거래대금 N위 안 — 회전율만 보면 시총 작은 노이즈 종목이 1위로 잡힘.
    #   (2) min < 일일 상승률 < max — 상한가 도달/임박은 매수 불가, 하한가/하락은
    #       인버스 매매를 안 하므로 후보 X. 종배는 상한가 *전* 진입 + 상승 중인 종목.
    eligible_df = snapshot_df
    if "rank" in eligible_df.columns and rank_max > 0:
        eligible_df = eligible_df[eligible_df["rank"] <= rank_max]
    if "daily_return" in eligible_df.columns:
        dr = eligible_df["daily_return"].fillna(0.0)
        eligible_df = eligible_df[
            (dr > min_daily_return_pct) & (dr < exclude_daily_return_pct)
        ]
    if eligible_df.empty:
        return []

    has_turnover = (
        "turnover" in eligible_df.columns
        and eligible_df["turnover"].notna().any()
    )
    sort_key = "turnover" if has_turnover else "trading_value"
    if not has_turnover:
        logger.warning(
            "회전율 데이터 없음 — 거래대금 절대값으로 fallback. "
            "시총 적재(M5.5) 확인 필요."
        )

    by_code: dict[str, dict[str, Any]] = {}

    for theme_info in leading_themes:
        theme = theme_info.get("theme")
        if not theme:
            continue
        theme_codes = set(theme_info.get("codes", []))
        if not theme_codes:
            continue

        in_theme = eligible_df[eligible_df["code"].astype(str).isin(theme_codes)].copy()
        if in_theme.empty:
            continue

        # 회전율 상위 (NaN 은 -inf 처리)
        in_theme["_sort_key"] = in_theme[sort_key].fillna(float("-inf"))
        top = in_theme.sort_values("_sort_key", ascending=False).head(top_per_theme)

        for _, row in top.iterrows():
            code = str(row["code"])
            if code in by_code:
                if theme not in by_code[code]["themes"]:
                    by_code[code]["themes"].append(theme)
                continue
            by_code[code] = {
                "code": code,
                "name": str(row.get("name", "")),
                "themes": [theme],
                "rank": int(row.get("rank", 0)),
                "price": int(row.get("price", 0)),
                "daily_return": float(row.get("daily_return", 0.0))
                    if pd.notna(row.get("daily_return")) else 0.0,
                "is_limit_up": bool(row.get("is_limit_up", False)),
                "turnover": float(row.get("turnover", float("nan")))
                    if pd.notna(row.get("turnover")) else float("nan"),
                "trading_value": int(row.get("trading_value", 0)),
                "market_cap": int(row.get("market_cap", 0)),
            }

    # 회전율 내림차순 (회전율 NaN 은 끝으로). fallback 인 경우 거래대금 기준.
    def _key(entry: dict[str, Any]) -> float:
        v = entry.get("turnover", float("nan"))
        if v != v or v is None:  # NaN
            return -float(entry.get("trading_value", 0))  # fallback 음수
        return -float(v)

    return sorted(by_code.values(), key=_key)


def identify_rising_candidates(
    snapshot_df: pd.DataFrame,
    top_n: int = RISING_STAGE1_TURNOVER_TOP_N,
    rank_max: int | None = None,
    exclude_daily_return_pct: float | None = None,
    min_daily_return_pct: float | None = None,
    theme_mapping_df: pd.DataFrame | None = None,
) -> list[dict[str, Any]]:
    """부상 후보 풀 — Stage 0+1 (round 21 다단계 funnel).

    snapshot 만 사용해서 무료로 1차 추림:
        Stage 0 (snapshot 필터):
            - rank ≤ rank_max (거래대금 N위 안, 기본 50)
            - min_daily_return_pct < daily_return < exclude_daily_return_pct
              (양봉만 + 상한가 임박 +29% 제외)
            - ETF/리츠/스팩은 fetch_volume_rank 단계에서 이미 master_df 로 제외됨
        Stage 1 (회전율 컷오프):
            - 회전율 내림차순 상위 top_n (기본 15)

    Stage 2~4 (모멘텀 / 체결강도 / Buy.Score 풀스코어) 는 호출자 (worker.dashboard_tick)
    가 minute_bars / ccnl / asking / investor 를 단계별 호출하면서 추가로 추림.
    본 함수는 Stage 4 의 Buy.Score 점수 매기기 전까지 후보 목록만 만든다.

    `identify_early_morning_leaders` 는 주도섹터 내 회전율 1위만 잡지만,
    그 단계 이전에 시총 대비 거래대금이 갑자기 늘어나는 종목도 보고 싶다는
    사용자 명시 요구. 주도섹터 분류 없이 거래대금 상위에서 회전율 상위 N 개.

    동일 자격 필터:
        - rank ≤ rank_max (default 50): 거래대금 절대값 50위 안
        - min < daily_return < max (default 0% < dr < 29%): 상승 중 + 매수 가능

    Args:
        snapshot_df: SNAPSHOT_COLUMNS DataFrame.
        top_n: 회전율 상위 몇 개.
        rank_max / exclude_daily_return_pct / min_daily_return_pct:
            `identify_early_morning_leaders` 와 동일 의미, None 이면 상수 사용.
        theme_mapping_df: 있으면 후보 themes 라벨 채움 ([code, theme] long format).

    Returns:
        leaders 와 동일 스키마 dict 리스트. 회전율 내림차순.
        themes 는 후보가 속한 모든 테마(있다면) 또는 [].
    """
    if snapshot_df.empty:
        return []

    if rank_max is None:
        rank_max = LEADER_CANDIDATE_RANK_MAX
    if exclude_daily_return_pct is None:
        exclude_daily_return_pct = LEADER_EXCLUDE_DAILY_RETURN_PCT
    if min_daily_return_pct is None:
        min_daily_return_pct = LEADER_MIN_DAILY_RETURN_PCT

    eligible_df = snapshot_df
    if "rank" in eligible_df.columns and rank_max > 0:
        eligible_df = eligible_df[eligible_df["rank"] <= rank_max]
    if "daily_return" in eligible_df.columns:
        dr = eligible_df["daily_return"].fillna(0.0)
        eligible_df = eligible_df[
            (dr > min_daily_return_pct) & (dr < exclude_daily_return_pct)
        ]
    if eligible_df.empty:
        return []

    has_turnover = (
        "turnover" in eligible_df.columns
        and eligible_df["turnover"].notna().any()
    )
    sort_key = "turnover" if has_turnover else "trading_value"
    pool = eligible_df.copy()
    pool["_sort_key"] = pool[sort_key].fillna(float("-inf"))
    top = pool.sort_values("_sort_key", ascending=False).head(top_n)

    # 종목 코드 → 테마 목록 (선택)
    code_to_themes: dict[str, list[str]] = {}
    if theme_mapping_df is not None and not theme_mapping_df.empty:
        grouped = theme_mapping_df.groupby("code")["theme"].apply(list)
        code_to_themes = grouped.to_dict()

    out: list[dict[str, Any]] = []
    for _, row in top.iterrows():
        code = str(row["code"])
        out.append({
            "code": code,
            "name": str(row.get("name", "")),
            "themes": code_to_themes.get(code, []),
            "rank": int(row.get("rank", 0)),
            "price": int(row.get("price", 0)),
            "daily_return": float(row.get("daily_return", 0.0))
                if pd.notna(row.get("daily_return")) else 0.0,
            "is_limit_up": bool(row.get("is_limit_up", False)),
            "turnover": float(row.get("turnover", float("nan")))
                if pd.notna(row.get("turnover")) else float("nan"),
            "trading_value": int(row.get("trading_value", 0)),
            "market_cap": int(row.get("market_cap", 0)),
        })
    return out


# ── Theme v1: 테마 z-score 합산 (M5.5) ──────────────────────────────────────────


def _zscore(values: list[float]) -> list[float]:
    """간단 z-score. 표준편차 0 이면 모두 0 반환."""
    if not values:
        return []
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / len(values)
    std = math.sqrt(var)
    if std == 0:
        return [0.0] * len(values)
    return [(v - mean) / std for v in values]


def score_leading_sectors(
    snapshot_df: pd.DataFrame,
    theme_mapping_df: pd.DataFrame,
    top_n: int = LEADING_SECTOR_TOP_N,
    sector_count: int = LEADING_SECTOR_COUNT,
    breadth_return_threshold: float = SECTOR_BREADTH_RETURN_THRESHOLD,
) -> list[dict[str, Any]]:
    """주도섹터 식별 v1 (M5.5) — 테마별 z-score 합산.

    정량 정의:
        1. 거래대금 상위 top_n 종목 추출 (스냅샷이 이미 master 필터링됨)
        2. 각 테마에 속한 종목으로 (a) breadth, (b) avg_return, (c) turnover_sum 집계
        3. 테마간 z-score 정규화 → 가중합
        4. 상위 sector_count 개를 주도섹터로 채택

    Args:
        snapshot_df: SNAPSHOT_COLUMNS (turnover 포함).
        theme_mapping_df: long format (code, theme).
        top_n: 거래대금 상위 몇 위까지 집계 대상.
        sector_count: 채택할 주도섹터 수.
        breadth_return_threshold: breadth 정의 임계 (+X%↑).

    Returns:
        [{
            "theme": "전기/전선",
            "score": 4.21,
            "breadth": 4,            # +X%↑ 종목 수
            "avg_return": 18.2,      # 동일가중 평균 상승률 (%)
            "turnover_sum": 53.1,    # 회전율 합계 (%)
            "member_count": 6,       # 테마 구성종목 수 (스냅샷 내)
            "codes": ["075180", ...] # rank 오름차순
        }, ...]
        score 내림차순.
    """
    if snapshot_df.empty:
        return []
    if theme_mapping_df.empty:
        logger.warning("주도섹터 식별: 테마 매핑 데이터 없음")
        return []

    top = snapshot_df.sort_values("rank").head(top_n).copy()
    top["code"] = top["code"].astype(str)
    top_codes = set(top["code"].tolist())

    mapping = theme_mapping_df.copy()
    mapping["code"] = mapping["code"].astype(str)
    matched = mapping[mapping["code"].isin(top_codes)]
    if matched.empty:
        return []

    # 종목 메타 빠른 조회
    by_code = top.set_index("code")
    rank_map = {c: int(by_code.at[c, "rank"]) for c in top_codes}

    # 테마별 집계
    theme_groups = matched.groupby("theme")["code"].apply(list)

    raw: list[dict[str, Any]] = []
    for theme, codes in theme_groups.items():
        codes = [c for c in codes if c in top_codes]
        if len(codes) < SECTOR_MIN_MEMBER_COUNT:
            continue

        members = by_code.loc[codes]
        rets = members["daily_return"].fillna(0.0).astype(float).tolist()
        tos = members["turnover"].astype(float).fillna(0.0).tolist()

        breadth = sum(1 for r in rets if r >= breadth_return_threshold)
        avg_return = sum(rets) / len(rets)
        turnover_sum = sum(tos)

        raw.append({
            "theme": str(theme),
            "breadth": breadth,
            "avg_return": float(avg_return),
            "turnover_sum": float(turnover_sum),
            "member_count": len(codes),
            "codes": sorted(codes, key=lambda c: rank_map.get(c, 99999)),
        })

    if not raw:
        return []

    # 테마간 z-score 정규화
    breadth_z = _zscore([float(r["breadth"]) for r in raw])
    return_z = _zscore([r["avg_return"] for r in raw])
    turnover_z = _zscore([r["turnover_sum"] for r in raw])

    for i, r in enumerate(raw):
        r["score"] = (
            SECTOR_WEIGHT_BREADTH * breadth_z[i]
            + SECTOR_WEIGHT_RETURN * return_z[i]
            + SECTOR_WEIGHT_TURNOVER * turnover_z[i]
        )
        # leading_themes 호환성을 위해 'count' 도 채워둠 (= breadth + +5%이상 종목 수)
        r["count"] = r["breadth"]

    raw.sort(key=lambda x: x["score"], reverse=True)
    leading = raw[:sector_count]

    logger.info(
        f"주도섹터 v1 식별 {len(leading)}개: "
        f"{[(t['theme'], round(t['score'], 2)) for t in leading]}"
    )
    return leading


# ── 단저단고 surface 룰 (2026-05-29) ───────────────────────────────────────────


def select_leaders_and_candidates(
    snapshot_df: pd.DataFrame,
    leading_sectors: list[dict[str, Any]],
    exclude_daily_return_pct: float | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """주도섹터별 주도주 + 후보 식별 (단저단고 패러다임 surface 룰).

    각 섹터에 대해:
      - 주도주 (leader): 거래대금 1위 ∪ 회전율 1위
          같은 종목 → leaders 1종목 / 다른 종목 → 공동 주도주 2종목 (후보 평가 X).
      - 주도주 후보 (candidate): 거래대금 2위 == 회전율 2위 (같을 때만).
          주도주 공동 케이스에선 평가 X. 다른 종목이면 후보 없음.

    필터 (매수 가능한 종목만):
      - 일봉 +29% 이상 종목 제외 (상한가 도달 = 매수 불가)
      - is_limit_up True 제외
      - 일봉 음봉(daily_return < 0)도 허용 — 단저단고는 모멘텀 반대 진입 가능

    섹터간 종목 중복: 첫 출현 섹터에 귀속. surface_sector_name 에 첫 섹터명 고정.

    Args:
        snapshot_df: SNAPSHOT_COLUMNS DataFrame.
        leading_sectors: score_leading_sectors() 결과 (Top sector_count). 각 dict
                        의 "theme" / "codes" 사용.
        exclude_daily_return_pct: 상한가 임박 제외 임계 (None=LEADER_EXCLUDE_DAILY_RETURN_PCT).

    Returns:
        (leaders, candidates). 각 dict 스키마는 `identify_early_morning_leaders`
        와 호환 + 신규 키:
            - sector_role: "leader" | "candidate"
            - surface_sector_name: str (첫 출현 섹터명, 카드 테마 라인용)
            - themes: [surface_sector_name] (호환성, state.update_auto_leaders 가
              themes 키를 참조)
    """
    if snapshot_df.empty or not leading_sectors:
        return [], []

    if exclude_daily_return_pct is None:
        exclude_daily_return_pct = LEADER_EXCLUDE_DAILY_RETURN_PCT

    df = snapshot_df.copy()
    df["code"] = df["code"].astype(str)
    if "daily_return" in df.columns:
        dr = df["daily_return"].fillna(0.0)
        df = df[dr < exclude_daily_return_pct]
    if "is_limit_up" in df.columns:
        df = df[~df["is_limit_up"].fillna(False).astype(bool)]
    if df.empty:
        return [], []

    def _row_to_entry(
        row: pd.Series, sector_name: str, role: str, sector_rank: int,
    ) -> dict[str, Any]:
        return {
            "code": str(row["code"]),
            "name": str(row.get("name", "")),
            "rank": int(row["rank"]) if pd.notna(row.get("rank")) else 0,
            "price": int(row.get("price", 0)) if pd.notna(row.get("price")) else 0,
            "daily_return": float(row.get("daily_return", 0.0))
                if pd.notna(row.get("daily_return")) else 0.0,
            "is_limit_up": bool(row.get("is_limit_up", False)),
            "turnover": float(row.get("turnover", float("nan")))
                if pd.notna(row.get("turnover")) else float("nan"),
            "trading_value": int(row.get("trading_value", 0))
                if pd.notna(row.get("trading_value")) else 0,
            "market_cap": int(row.get("market_cap", 0))
                if pd.notna(row.get("market_cap")) else 0,
            "sector_role": role,
            "sector_rank": sector_rank,  # 1=주도섹터, 2=2위, 3=3위
            "surface_sector_name": sector_name,
            "themes": [sector_name],
        }

    leaders: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    seen_codes: set[str] = set()

    # leading_sectors 는 score 내림차순 정렬됨 → enumerate 가 1=주도섹터, 2=2위, 3=3위
    for sector_idx, sector in enumerate(leading_sectors, start=1):
        sector_name = str(sector.get("theme", ""))
        sector_codes = {str(c) for c in sector.get("codes", [])}
        if not sector_name or not sector_codes:
            continue

        in_sector = df[df["code"].isin(sector_codes)]
        if in_sector.empty:
            continue

        # 거래대금 rank: 작은 게 1위, NaN 은 큰 값으로 정렬 끝으로
        rank_series = in_sector["rank"].fillna(99999) if "rank" in in_sector.columns else None
        by_rank = in_sector.assign(_rk=rank_series).sort_values("_rk", ascending=True)
        # 회전율: 큰 게 1위, NaN 은 -inf
        turnover_series = (
            in_sector["turnover"].fillna(float("-inf"))
            if "turnover" in in_sector.columns else None
        )
        by_turnover = in_sector.assign(_to=turnover_series).sort_values("_to", ascending=False)

        trading_top1 = by_rank.iloc[0]
        turnover_top1 = by_turnover.iloc[0]
        trading_top1_code = str(trading_top1["code"])
        turnover_top1_code = str(turnover_top1["code"])

        if trading_top1_code == turnover_top1_code:
            # 단일 주도주
            if trading_top1_code not in seen_codes:
                leaders.append(_row_to_entry(trading_top1, sector_name, "leader", sector_idx))
                seen_codes.add(trading_top1_code)

            # 후보 평가
            if len(by_rank) >= 2 and len(by_turnover) >= 2:
                trading_top2 = by_rank.iloc[1]
                turnover_top2 = by_turnover.iloc[1]
                if str(trading_top2["code"]) == str(turnover_top2["code"]):
                    cand_code = str(trading_top2["code"])
                    if cand_code not in seen_codes:
                        candidates.append(_row_to_entry(trading_top2, sector_name, "candidate", sector_idx))
                        seen_codes.add(cand_code)
        else:
            # 공동 주도주 — 후보 평가 X
            for row, code in [
                (trading_top1, trading_top1_code),
                (turnover_top1, turnover_top1_code),
            ]:
                if code not in seen_codes:
                    leaders.append(_row_to_entry(row, sector_name, "leader", sector_idx))
                    seen_codes.add(code)

    logger.info(
        f"단저단고 surface 식별 — leaders={len(leaders)} candidates={len(candidates)} "
        f"(top sectors={len(leading_sectors)})"
    )
    return leaders, candidates
