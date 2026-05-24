"""5/18~ 최근 구간 top3 + Kelly 종목별 비중 검증 + 시초/최저/최고 매도 envelope.

사용자 2026-05-25 요청:
    (1) 5/18 부터 이후 전부 (일봉 가능 끝 = 5/20 진입 → 5/21 갭).
    (2) 시초가뿐 아니라 다음날 일중 최저(최악)/최고(최선) 매도 수익률도.
        → 사람은 시초에 완벽히 못 파니 실행 범위를 보여줌.
    (3) top3 종목별 Kelly 비중 (절대 = 현금포함 / 상대 = top3 내 강약).

룰: 후보 = 거래대금 top50 ∩ 5≤ret≤27 ∩ drop≤10, 거래대금순위 정렬 top3.
    Kelly 비중 = 거래대금순위 버킷의 과거 p/W/L (학습 ≤2026-05-14, no-lookahead).
사용: python scripts/backtest_recent_kelly.py
"""
from __future__ import annotations

import datetime as dt
import sys

import pandas as pd

from src.config import load_settings


def _load(dd):
    o = pd.read_parquet(dd / "daily" / "ohlcv.parquet")
    m = pd.read_parquet(dd / "meta" / "stocks.parquet")
    nm = dict(zip(m["code"].astype(str), m["name"].astype(str)))
    trad = set(m["code"].astype(str))
    o["code"] = o["code"].astype(str)
    o = o.sort_values(["code", "date"]).reset_index(drop=True)
    o["ret"] = o.groupby("code")["close"].pct_change() * 100.0
    for col in ("open", "low", "high"):
        o[f"n_{col}"] = o.groupby("code")[col].shift(-1)
    o["g_open"] = (o["n_open"] - o["close"]) / o["close"] * 100.0
    o["g_low"] = (o["n_low"] - o["close"]) / o["close"] * 100.0
    o["g_high"] = (o["n_high"] - o["close"]) / o["close"] * 100.0
    return o, nm, trad


def universe(o, trad, d):
    day = o[o["date"] == d]
    day = day[day["code"].isin(trad) & (day["close"] > 0) & (day["high"] > 0) & day["ret"].notna()].copy()
    day["tvr"] = day["trading_value"].rank(ascending=False, method="first")
    day = day[day["tvr"] <= 50]
    day["drop"] = (day["high"] - day["close"]) / day["high"] * 100.0
    day = day[(day["ret"] >= 5) & (day["ret"] <= 27) & (day["drop"] <= 10) & day["g_open"].notna()]
    return day.sort_values("tvr")


def bkt(t):
    return "1~10위" if t <= 10 else ("11~25위" if t <= 25 else "26~50위")


def kelly(p, W, L, n):
    if not (0 < p < 1) or W <= 0 or L <= 0:
        return 0.0
    f = p / L - (1 - p) / W
    fac = 0.8 if n >= 20 else (0.6 if n >= 10 else (0.3 if n >= 5 else 0))
    return max(0.0, min(0.25, f * fac))


def build_rule(o, trad, end_train):
    rows = []
    for d in sorted(o["date"].unique()):
        if d > end_train:
            continue
        for _, r in universe(o, trad, d).iterrows():
            rows.append({"b": bkt(r["tvr"]), "g": r["g_open"]})
    tr = pd.DataFrame(rows)
    rule = {}
    for b, g in tr.groupby("b"):
        up = g[g.g > 0]["g"]; dn = g[g.g <= 0]["g"]
        p = len(up) / len(g); W = up.mean(); L = abs(dn.mean())
        rule[b] = kelly(p, W, L, len(g))
    return rule


def main():
    s = load_settings()
    o, nm, trad = _load(s.data_dir)
    rule = build_rule(o, trad, dt.date(2026, 5, 14))
    print("거래대금순위 버킷 Kelly 비중(학습 ≤5/14):", {k: f"{v*100:.1f}%" for k, v in rule.items()})

    # --- (1)+(3) 최근 구간 5/18~ 일별 상세 ---
    recent = [d for d in sorted(o["date"].unique()) if dt.date(2026, 5, 18) <= d and o[o["date"] == d]["ret"].notna().any()]
    recent = [d for d in recent if not o[o["date"] == d]["g_open"].isna().all()]  # 다음날 존재
    agg = {"open": [], "low": [], "high": []}
    agg_eq = {"open": [], "low": [], "high": []}
    print("\n===== 최근 구간 일별 (top3 + Kelly 종목별 비중) =====")
    for d in recent:
        u = universe(o, trad, d)
        if len(u) == 0:
            continue
        t3 = u.head(3).copy()
        t3["w_abs"] = t3["tvr"].apply(lambda x: rule[bkt(x)])
        tot = t3["w_abs"].sum()
        t3["w_rel"] = t3["w_abs"] / tot if tot > 0 else 0
        print(f"\n[{d}] 후보 {len(u)}개 → top3")
        print(f"  {'종목':<12}{'거래대금순위':>8}{'Kelly절대':>9}{'top3내상대':>9} | {'시초갭':>7}{'최저갭':>7}{'최고갭':>7}")
        for _, r in t3.iterrows():
            print(f"  {nm.get(r['code'],'')[:11]:<12}{bkt(r['tvr']):>8}{r['w_abs']*100:>8.1f}%{r['w_rel']*100:>8.1f}% | "
                  f"{r['g_open']:>+6.2f}%{r['g_low']:>+6.2f}%{r['g_high']:>+6.2f}%")
        for k, col in [("open", "g_open"), ("low", "g_low"), ("high", "g_high")]:
            agg[k].append((t3["w_abs"] * t3[col]).sum())       # Kelly 절대(현금포함)
            agg_eq[k].append(t3[col].mean())                    # 균등 풀투입
        print(f"  → 계좌수익(Kelly절대): 시초 {agg['open'][-1]:+.3f}% / 최저 {agg['low'][-1]:+.3f}% / 최고 {agg['high'][-1]:+.3f}%")
        print(f"     (참고 균등풀투입 시초 {agg_eq['open'][-1]:+.3f}%)")

    n = len(agg["open"])
    print(f"\n===== 최근 {n}일 누적/평균 =====")
    for k in ("open", "low", "high"):
        ser = pd.Series(agg[k])
        cum = (1 + ser / 100).prod() - 1
        print(f"  [{k:>4} 매도] Kelly절대 일평균 {ser.mean():+.3f}% / {n}일 누적 {cum*100:+.2f}%")
    print(f"  [시초 매도] 균등풀투입 일평균 {pd.Series(agg_eq['open']).mean():+.3f}%")

    # --- (2) 스케일 envelope: 1년 / 3개월 top3 균등, 시초/최저/최고 ---
    print("\n===== 스케일 검증: top3 균등, 매도 시점별 envelope =====")
    for lab, st in [("1년", dt.date(2025, 5, 8)), ("3개월", dt.date(2026, 2, 24))]:
        rows = {"open": [], "low": [], "high": []}
        for d in sorted(o["date"].unique()):
            if not (st <= d <= dt.date(2026, 5, 20)):
                continue
            u = universe(o, trad, d)
            if len(u) < 4:
                continue
            t3 = u.head(3)
            for k, col in [("open", "g_open"), ("low", "g_low"), ("high", "g_high")]:
                rows[k] += t3[col].tolist()
        print(f"  [{lab}] N={len(rows['open'])}  "
              f"시초 평균 {pd.Series(rows['open']).mean():+.2f}% | "
              f"최저(최악) {pd.Series(rows['low']).mean():+.2f}% | "
              f"최고(최선) {pd.Series(rows['high']).mean():+.2f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
