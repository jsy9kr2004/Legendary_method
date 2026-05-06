"""주도테마 식별 (R3).

정량 정의:
    1. 시점 t의 거래대금 상위 N(=30)위 종목 추출
    2. 각 종목의 네이버 테마 리스트 조회 (한 종목 = 다중 테마)
    3. 테마별 출현 빈도 카운트
    4. 빈도 >= LEADING_THEME_THRESHOLD(=3) 인 테마를 주도테마로 식별

테마 분류 우선순위:
    - 코드 내부 판정: 네이버 금융 테마 (data/meta/naver_themes.parquet)
    - WICS 중분류는 레포트에 병기만 (M0 TODO)

함정 방지:
    "종가 기준 거래대금 순위로 보면 빨리 상한가 친 진짜 주도주가 누락됨"
    → 호출부는 시점별 누적 거래대금 스냅샷을 사용해야 한다.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

import pandas as pd
from loguru import logger

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
    top_per_theme: int = 2,
) -> list[dict[str, Any]]:
    """장초반 고주파 모니터링용 주도주 식별 (pre-limit-up).

    목적 (사용자 명시):
        상한가는 거래정지(매수/매도 불가) 이므로, 상한가 도달 *전* 진입을
        노린다. 따라서 주도주 = 곧 상한가에 도달할 가능성이 높은 종목.

    정의:
        (1) 주도섹터 내,
        (2) 주도섹터 내 거래대금 상위 (rank 가 좋은) top_per_theme 종목, OR
        (3) 주도섹터 내 상승률(daily_return) 상위 top_per_theme 종목.

    (2)와 (3)이 일시적으로 다를 수 있어 한 테마에서 둘 이상의 주도주가
    나올 수 있고, 각 주도섹터 ↔ 주도주 가 1:1 매핑이 아니다.
    한 종목이 여러 주도섹터에 속하면 themes 리스트에 합쳐서 한 번만 등장.

    Args:
        snapshot_df: SNAPSHOT_COLUMNS DataFrame (rank, code, daily_return, ...).
        leading_themes: identify_leading_themes() 결과.
        top_per_theme: 각 테마에서 거래대금/상승률 각각 상위 몇 개 볼지.

    Returns:
        [{
            "code", "name", "themes": [...], "rank", "price",
            "daily_return", "is_limit_up",
            "criterion": "volume" | "return" | "both",
        }, ...]
        같은 종목이 여러 테마/기준에 걸치면 한 번만 등장 (themes 합치고
        criterion 은 'both' 로 격상).
    """
    if snapshot_df.empty or not leading_themes:
        return []

    # 종목별 누적 정보
    by_code: dict[str, dict[str, Any]] = {}

    for theme_info in leading_themes:
        theme = theme_info["theme"]
        theme_codes = set(theme_info.get("codes", []))
        if not theme_codes:
            continue

        in_theme = snapshot_df[snapshot_df["code"].astype(str).isin(theme_codes)].copy()
        if in_theme.empty:
            continue

        # 거래대금 상위 (rank 작을수록 좋음)
        vol_top = in_theme.sort_values("rank").head(top_per_theme)
        vol_codes = set(vol_top["code"].astype(str).tolist())

        # 상승률 상위 (NaN 은 -inf 처리해서 끝으로 보냄)
        ret_top = in_theme.assign(
            _ret_key=in_theme["daily_return"].fillna(float("-inf"))
        ).sort_values("_ret_key", ascending=False).head(top_per_theme)
        ret_codes = set(ret_top["code"].astype(str).tolist())

        union_codes = vol_codes | ret_codes

        for _, row in in_theme[in_theme["code"].astype(str).isin(union_codes)].iterrows():
            code = str(row["code"])
            in_vol = code in vol_codes
            in_ret = code in ret_codes
            new_criterion = "both" if (in_vol and in_ret) else ("volume" if in_vol else "return")

            if code in by_code:
                # 기존 항목 업데이트 — 테마 합치기, criterion 격상
                entry = by_code[code]
                if theme not in entry["themes"]:
                    entry["themes"].append(theme)
                if entry["criterion"] != "both" and entry["criterion"] != new_criterion:
                    entry["criterion"] = "both"
                # rank/price 등은 첫 등장 값 유지 (스냅샷 기준 동일)
            else:
                by_code[code] = {
                    "code": code,
                    "name": str(row.get("name", "")),
                    "themes": [theme],
                    "rank": int(row.get("rank", 0)),
                    "price": int(row.get("price", 0)),
                    "daily_return": float(row.get("daily_return", 0.0))
                        if pd.notna(row.get("daily_return")) else 0.0,
                    "is_limit_up": bool(row.get("is_limit_up", False)),
                    "criterion": new_criterion,
                }

    # rank 순 정렬 (가장 거래대금 상위 종목 먼저)
    return sorted(by_code.values(), key=lambda x: x["rank"])
