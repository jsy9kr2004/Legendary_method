"""거래대금순위 버킷 조건부 Kelly 사이징 (Eod.Sizing v2 — 2026-05-25).

factor_edge backtest 결론 (`scripts/backtest_factor_edge.py`, memory project-eod-factor-edge):
    종배 후보 중 다음날 갭상을 **robust 하게 가르는 유일한 일봉 팩터 = 거래대금순위**.
    per-stock historical(끼)·신고가·시총·종가위치는 노이즈(창 사이 뒤집힘).
    → 사이징도 per-stock Layer 대신 **거래대금순위 버킷의 rolling-window p/W/L** 로 Kelly.

버킷 (factor_edge 단조성 확인 구간):
    1~10위 / 11~25위 / 26~50위

rolling window:
    최근 N 거래일 (기본 90 ≈ 4개월). 강세장 1레짐 고정 회피 + 현 장세 추종.
    사용자 정정 (2026-05-25): "최근 3개월에 한국 증시 변화 많았다" → 짧은 창으로 현
    레짐 반영. 단 표본은 kelly_sample_factor 가 보정.

산출:
    각 후보의 거래대금순위 → 해당 버킷 stats → `sizing.kelly_fraction(stats)` 재사용.
    → 절대 비중(f_i, 현금 = 1 - Σf_i) + top3 내 상대 비중(f_i / Σf).

청산 envelope 주의 (memory): 선별 엣지(+0.7%)보다 청산 타이밍 폭(~9%p)이 13배 큼.
    본 모듈은 "얼마 실을지"만. "언제 팔지"(진짜 레버리지)는 Eod.Exit 영역.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd
from loguru import logger

from src.overnight.sizing import kelly_fraction

# factor_edge 단조성 버킷
BUCKETS: list[tuple[int, int, str]] = [
    (1, 10, "1~10위"),
    (11, 25, "11~25위"),
    (26, 50, "26~50위"),
]
DEFAULT_LOOKBACK_DAYS = 90

# 후보 풀 정의 (candidates.py 와 동일 hard cut — 사이징 학습 universe 일치)
_MIN_RET = 5.0
_MAX_RET = 27.0
_MAX_DROP = 10.0
_TOP_N = 50


def rank_bucket(rank: int | float | None) -> str | None:
    """거래대금순위 → 버킷 라벨. 50위 밖/None 이면 None."""
    if rank is None or (isinstance(rank, float) and pd.isna(rank)):
        return None
    r = int(rank)
    for lo, hi, label in BUCKETS:
        if lo <= r <= hi:
            return label
    return None


def build_bucket_stats(
    daily_ohlcv: pd.DataFrame,
    as_of: dt.date,
    tradable_codes: set[str],
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> dict[str, dict[str, Any]]:
    """거래대금순위 버킷별 갭 통계 (rolling, no-lookahead).

    Args:
        daily_ohlcv: 전종목 일봉 (code/date/open/high/low/close/trading_value).
        as_of: 사이징 기준일. **이 날 이전(strict <) 데이터만 학습** (lookahead 차단).
        tradable_codes: master 통과 종목 (ETF/스팩 등 제외).
        lookback_days: rolling 창 거래일 수.

    Returns:
        {bucket_label: {n, p, avg_gap_when_up, avg_gap_when_dn, std_gap}}.
        스키마는 sizing.kelly_fraction / sharpe_score 가 그대로 먹도록 맞춤.
    """
    if daily_ohlcv.empty:
        return {}
    df = daily_ohlcv.copy()
    df["code"] = df["code"].astype(str)
    df = df.sort_values(["code", "date"]).reset_index(drop=True)
    df["ret"] = df.groupby("code")["close"].pct_change() * 100.0
    df["next_open"] = df.groupby("code")["open"].shift(-1)
    df["gap"] = (df["next_open"] - df["close"]) / df["close"] * 100.0

    all_dates = sorted(d for d in df["date"].unique() if d < as_of)
    window_dates = set(all_dates[-lookback_days:])
    if not window_dates:
        return {}

    rows: list[dict[str, Any]] = []
    for d in window_dates:
        day = df[df["date"] == d]
        day = day[
            day["code"].isin(tradable_codes)
            & (day["close"] > 0)
            & (day["high"] > 0)
            & day["ret"].notna()
            & day["gap"].notna()
        ].copy()
        if day.empty:
            continue
        day["tv_rank"] = day["trading_value"].rank(ascending=False, method="first")
        day = day[day["tv_rank"] <= _TOP_N]
        day["drop_pct"] = (day["high"] - day["close"]) / day["high"] * 100.0
        day = day[
            (day["ret"] >= _MIN_RET)
            & (day["ret"] <= _MAX_RET)
            & (day["drop_pct"] <= _MAX_DROP)
        ]
        for _, r in day.iterrows():
            b = rank_bucket(r["tv_rank"])
            if b is not None:
                rows.append({"bucket": b, "gap": float(r["gap"])})

    if not rows:
        return {}
    pool = pd.DataFrame(rows)
    stats: dict[str, dict[str, Any]] = {}
    for b, g in pool.groupby("bucket"):
        gaps = g["gap"]
        up = gaps[gaps > 0]
        dn = gaps[gaps <= 0]
        stats[b] = {
            "n": int(len(gaps)),
            "p": float((gaps > 0).mean()),
            "avg_gap_when_up": float(up.mean()) if len(up) else 0.0,
            "avg_gap_when_dn": float(abs(dn.mean())) if len(dn) else 0.0,
            "std_gap": float(gaps.std()) if len(gaps) > 1 else 0.0,
        }
    return stats


def compute_bucket_sizing(
    candidates: list[dict[str, Any]],
    bucket_stats: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """후보별 거래대금순위 버킷 Kelly 비중 (절대 + 상대 + 현금).

    Args:
        candidates: 각 dict 에 "rank"(거래대금순위) 필요.
        bucket_stats: build_bucket_stats 결과.

    Returns:
        {
          "kelly_abs":  [f_i ...],     # 계좌 대비 절대 비중 (None=표본부족/버킷없음)
          "kelly_rel":  [w_i ...],     # 후보 합 대비 상대 (강약), Σ=1
          "buckets":    [label ...],
          "invested":   Σf_i,
          "cash":       1 - Σf_i,
        }
    """
    n = len(candidates)
    if n == 0:
        return {"kelly_abs": [], "kelly_rel": [], "buckets": [], "invested": 0.0, "cash": 1.0}

    abs_w: list[float | None] = []
    buckets: list[str | None] = []
    for c in candidates:
        b = rank_bucket(c.get("rank"))
        buckets.append(b)
        st = bucket_stats.get(b) if b else None
        abs_w.append(kelly_fraction(st) if st else None)

    invested = sum(w for w in abs_w if w)
    if invested > 0:
        rel = [(w / invested) if w else 0.0 for w in abs_w]
    else:
        rel = [0.0] * n

    miss = sum(1 for w in abs_w if w is None)
    if miss:
        logger.info(f"[bucket sizing] 버킷/표본 없어 비중 None: {miss}/{n}")
    return {
        "kelly_abs": abs_w,
        "kelly_rel": rel,
        "buckets": buckets,
        "invested": min(invested, 1.0),
        "cash": max(0.0, 1.0 - invested),
    }
