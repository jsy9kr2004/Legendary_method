"""주도섹터/주도주 universe 게이트 (사용자 비전 1, 2026-05-27).

`docs/scalping-redesign-2026-05-27.md` §2.5 / §2.8 정의.

핵심:
    - **거래대금 30위 ∩ 회전율 30위** 교집합 종목들 = scalping universe (좁힘).
      현재 `src/common/theme.py` 의 50위 거래대금 단일 기준에서 강화.
    - 한 종목 여러 섹터 모두 카운트 → 섹터 카운트 1·2·3위 (주도섹터 + 후보).
    - ETF/ETN/리츠/스팩/펀드 제외 (filter_excluded_codes).

pure 함수 — snapshot rows / volume-rank 결과 입력, set/list 반환.
"""
from __future__ import annotations

from typing import Iterable

import pandas as pd


# 사용자 비전 (1) 의 임계 — system tuning ritual 통과 후만 변경
RANK_MAX = 30
TURNOVER_RANK_MAX = 30
TOP_SECTORS_N = 3  # 1=주도 / 2,3=후보


def intersect_universe(
    rank_series: pd.Series,
    turnover_series: pd.Series,
    rank_max: int = RANK_MAX,
    turnover_rank_max: int = TURNOVER_RANK_MAX,
) -> set[str]:
    """거래대금 rank ≤ N ∩ 회전율 상위 N 위 교집합 종목 set.

    rank_series: code → 거래대금 rank (작을수록 상위, 1=거래대금 1위).
    turnover_series: code → 회전율 (%, 클수록 상위).

    반환: 교집합 종목 코드 set.

    주의: turnover_series 는 raw 값 (예: 50.3 = 회전율 50.3%). 함수 내부에서
    rank 변환 (큰 값일수록 1위) 후 turnover_rank_max 이하 추출.
    """
    rank_pass = set(rank_series.dropna()[rank_series.dropna() <= rank_max].index)
    turnover_rank = turnover_series.dropna().rank(ascending=False, method="min")
    turnover_pass = set(turnover_rank[turnover_rank <= turnover_rank_max].index)
    return rank_pass & turnover_pass


def count_sectors_in_universe(
    universe_codes: Iterable[str],
    code_to_sectors: dict[str, list[str]],
) -> dict[str, int]:
    """universe 종목들이 속한 섹터 카운트 (한 종목 여러 섹터 모두 카운트).

    code_to_sectors: code → 그 종목이 속한 섹터 list.

    반환: 섹터 → 카운트.
    """
    counts: dict[str, int] = {}
    for code in universe_codes:
        for sector in code_to_sectors.get(code, []):
            counts[sector] = counts.get(sector, 0) + 1
    return counts


def top_sectors(sector_counts: dict[str, int], n: int = TOP_SECTORS_N) -> list[tuple[str, int]]:
    """섹터 카운트 → 상위 N 개 (sector, count) 리스트. count 동률은 알파벳순.

    [0] = 주도섹터, [1], [2] = 후보.
    """
    return sorted(sector_counts.items(), key=lambda x: (-x[1], x[0]))[:n]


def leading_stocks_in_sector(
    sector: str,
    universe_codes: Iterable[str],
    code_to_sectors: dict[str, list[str]],
    rank_series: pd.Series,
    turnover_series: pd.Series,
    daily_return_series: pd.Series,
    require_positive_return: bool = True,
) -> list[str]:
    """주도섹터 내 주도주 후보 (사용자 비전 2).

    조건: 섹터 소속 + universe + (옵션) daily_return > 0.
    정렬: 거래대금 rank 와 회전율 rank 둘 다 고려 — 거래대금 1위와 회전율 1위가
    다를 수 있어 두 종목 모두 반환 (TRANSITION 이벤트).
    """
    candidates = [
        code
        for code in universe_codes
        if sector in code_to_sectors.get(code, [])
    ]
    if require_positive_return:
        candidates = [c for c in candidates if pd.notna(daily_return_series.get(c)) and daily_return_series.get(c) > 0]
    if not candidates:
        return []
    sub_rank = rank_series.reindex(candidates).dropna().sort_values(ascending=True)
    sub_turnover = turnover_series.reindex(candidates).dropna().sort_values(ascending=False)
    leaders: list[str] = []
    if len(sub_rank) > 0:
        leaders.append(str(sub_rank.index[0]))
    if len(sub_turnover) > 0 and str(sub_turnover.index[0]) not in leaders:
        leaders.append(str(sub_turnover.index[0]))
    return leaders
