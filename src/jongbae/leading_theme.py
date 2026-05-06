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
