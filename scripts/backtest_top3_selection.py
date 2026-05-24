"""종배 top3 선정 검증 — "후보가 많을 때 무엇으로 3개만 자르나" (사용자 2026-05-25).

배경: 사용자가 종배 3종목+ 사보니 시초 동시매도가 실무적으로 어려움 →
      하루 top3 만 들고 싶음. 그래서 "어떤 랭킹으로 자르면 갭상이 좋은가" 검증.

후보 풀 = 거래대금 top50 ∩ 5≤ret≤27 ∩ drop≤10 (factor_edge 와 동일).
하루 후보가 ≥4개인 날(=컷이 실제로 필요한 날)에서 선정 전략별 갭상 비교.

선정 전략:
    all       = 그날 후보 전체 (베이스라인)
    top3_tv   = 거래대금 순위 상위 3
    top3_tv15 = ret≤15 만 남기고 그 중 거래대금 상위 3 (모자라면 tv 로 채움)
    bot3_tv   = 거래대금 순위 하위 3 (대조군)

사용: python scripts/backtest_top3_selection.py
"""
from __future__ import annotations

import datetime as dt
import sys

import pandas as pd

from src.config import load_settings

MIN_RET, MAX_RET, MAX_DROP = 5.0, 27.0, 10.0


def _load(data_dir):
    o = pd.read_parquet(data_dir / "daily" / "ohlcv.parquet")
    m = pd.read_parquet(data_dir / "meta" / "stocks.parquet")
    trad = set(m["code"].astype(str))
    o["code"] = o["code"].astype(str)
    o = o.sort_values(["code", "date"]).reset_index(drop=True)
    o["ret"] = o.groupby("code")["close"].pct_change() * 100.0
    o["next_open"] = o.groupby("code")["open"].shift(-1)
    o["gap"] = (o["next_open"] - o["close"]) / o["close"] * 100.0
    return o, trad


def day_candidates(o, trad, d):
    day = o[o["date"] == d]
    day = day[day["code"].isin(trad) & (day["close"] > 0) & (day["high"] > 0) & day["ret"].notna()].copy()
    day["tvr"] = day["trading_value"].rank(ascending=False, method="first")
    day = day[day["tvr"] <= 50]
    day["drop"] = (day["high"] - day["close"]) / day["high"] * 100.0
    day = day[(day["ret"] >= MIN_RET) & (day["ret"] <= MAX_RET) & (day["drop"] <= MAX_DROP) & day["gap"].notna()]
    return day.sort_values("tvr")


def select(day, strat):
    if strat == "all":
        return day
    if strat == "top3_tv":
        return day.head(3)
    if strat == "bot3_tv":
        return day.tail(3)
    if strat == "top3_tv15":
        sweet = day[day["ret"] <= 15]
        if len(sweet) >= 3:
            return sweet.head(3)
        # 모자라면 나머지 후보를 tv 순으로 채움
        fill = day[~day["code"].isin(sweet["code"])].head(3 - len(sweet))
        return pd.concat([sweet, fill])
    raise ValueError(strat)


def run(o, trad, start, end, tag):
    dates = [d for d in sorted(o["date"].unique()) if start <= d <= end]
    strategies = ["all", "top3_tv", "top3_tv15", "bot3_tv"]
    picks = {s: [] for s in strategies}
    n_days = 0
    n_cut_days = 0
    cand_counts = []
    for d in dates:
        day = day_candidates(o, trad, d)
        if len(day) == 0:
            continue
        n_days += 1
        cand_counts.append(len(day))
        if len(day) < 4:
            continue  # 컷 불필요 — top3 분석에서 제외 (전략 차이 안 남)
        n_cut_days += 1
        for s in strategies:
            picks[s].extend(select(day, s)["gap"].tolist())

    L = [f"\n# ===== {tag} ({start}~{end}) =====",
         f"후보 있는 날 {n_days}일, 그중 **후보 ≥4개라 컷 필요한 날 {n_cut_days}일** "
         f"(평균 후보 {sum(cand_counts)/len(cand_counts):.1f}개/일, 최대 {max(cand_counts)}개)",
         f"\n아래는 **컷 필요한 {n_cut_days}일** 한정 (전략 차이가 나는 날):",
         "| 선정 전략 | 종목수 | P(갭상) | 평균갭 | 중앙갭 |",
         "|---|--:|--:|--:|--:|"]
    for s in strategies:
        g = pd.Series(picks[s])
        if len(g) == 0:
            L.append(f"| {s} | 0 | — | — | — |")
            continue
        L.append(f"| {s} | {len(g)} | {(g>0).mean()*100:.1f}% | {g.mean():+.2f}% | {g.median():+.2f}% |")
    return "\n".join(L)


def main():
    s = load_settings()
    o, trad = _load(s.data_dir)
    end = dt.date(2026, 5, 20)
    rep = (run(o, trad, dt.date(2025, 5, 8), end, "1년")
           + "\n" + run(o, trad, dt.date(2026, 2, 24), end, "3개월"))
    print(rep)
    (s.data_dir / "backtest" / "top3_selection.md").write_text(rep, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
