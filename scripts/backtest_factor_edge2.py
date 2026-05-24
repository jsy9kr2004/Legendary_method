"""종배 팩터 변별력 2차 — 시총 / 종목 끼 / 종가위치 (사용자 추가 제안 2026-05-25).

추가 팩터:
    8) 시총 (market cap) — close × shares. shares 적재된 종목만 (120/2731).
       영상 [3](약세장 중소형) vs [4](대형주만) 검증.
    9) 종목 끼 — 그 종목이 과거 1년 ret≥10% 였던 날들의 다음날 갭상 비율.
       유튜브 "종목마다 끼가 다르다" / 운전수 가설. 표본 ≥3 일 때만.
    10) 종가위치 = (close-low)/(high-low) — GapStats Layer3 매칭축.
        (drop_pct 와 별개: drop 은 고가대비, 종가위치는 봉 안에서의 위치)

후보 풀 = 거래대금 top50 ∩ 5≤ret≤27 ∩ drop≤10 (factor_edge.py 와 동일).
사용: python scripts/backtest_factor_edge2.py [--start --end]
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys

import pandas as pd

from src.config import load_settings

MIN_RET, MAX_RET, MAX_DROP = 5.0, 27.0, 10.0


def _load(data_dir):
    o = pd.read_parquet(data_dir / "daily" / "ohlcv.parquet")
    m = pd.read_parquet(data_dir / "meta" / "stocks.parquet")
    trad = set(m["code"].astype(str))
    shares = {str(c): int(sh) for c, sh in zip(m["code"], m["shares"]) if sh and sh > 0}
    o["code"] = o["code"].astype(str)
    o = o.sort_values(["code", "date"]).reset_index(drop=True)
    o["ret"] = o.groupby("code")["close"].pct_change() * 100.0
    o["next_open"] = o.groupby("code")["open"].shift(-1)
    o["gap"] = (o["next_open"] - o["close"]) / o["close"] * 100.0
    return o, trad, shares


def _kki(hist: pd.DataFrame, d, thr=10.0, min_n=3):
    """그 종목의 과거(d 이전) ret≥thr 날들의 다음날 갭상 비율 + 표본수."""
    past = hist[(hist["date"] < d)].tail(250)
    sim = past[(past["ret"] >= thr) & past["gap"].notna()]
    if len(sim) < min_n:
        return None, len(sim)
    return float((sim["gap"] > 0).mean()), len(sim)


def build(o, trad, shares, start, end):
    by_code = {c: g.reset_index(drop=True) for c, g in o.groupby("code")}
    dates = sorted(o["date"].unique())
    rows = []
    for di, d in enumerate(dates):
        if d < start or d > end:
            continue
        day = o[o["date"] == d]
        day = day[day["code"].isin(trad) & (day["close"] > 0) & (day["high"] > 0) & day["ret"].notna()].copy()
        day["tvr"] = day["trading_value"].rank(ascending=False, method="first")
        day = day[day["tvr"] <= 50]
        day["drop"] = (day["high"] - day["close"]) / day["high"] * 100.0
        day = day[(day["ret"] >= MIN_RET) & (day["ret"] <= MAX_RET) & (day["drop"] <= MAX_DROP)]
        for _, r in day.iterrows():
            c = r["code"]
            if pd.isna(r["gap"]):
                continue
            kki, kki_n = _kki(by_code[c], d)
            mc = r["close"] * shares[c] / 1e8 if c in shares else None  # 억원
            span = r["high"] - r["low"]
            cpos = (r["close"] - r["low"]) / span if span > 0 else None
            rows.append({
                "code": c, "tvr": int(r["tvr"]), "gap": r["gap"],
                "kki": kki, "kki_n": kki_n, "mc_eok": mc, "cpos": cpos,
            })
    return pd.DataFrame(rows)


def bucket(df, col, label, edges=None, labels=None, custom=None):
    out = [f"\n### {label}"]
    out.append("| 버킷 | N | P(갭상) | 평균갭 | 다음종가는생략 |")
    out.append("|---|--:|--:|--:|:--|")
    if custom is not None:
        df = df.copy(); df["_b"] = df[col].apply(custom); key = "_b"
    elif edges is not None:
        df = df.copy(); df["_b"] = pd.cut(df[col], edges, labels=labels); key = "_b"
    else:
        key = col
    ps = []
    for b, g in df.groupby(key, dropna=False):
        n = len(g)
        if n == 0:
            continue
        p = (g["gap"] > 0).mean() * 100
        ps.append(p)
        out.append(f"| {b} | {n} | {p:.1f}% | {g['gap'].mean():+.2f}% | |")
    if len(ps) >= 2:
        out.append(f"\n→ **스프레드 {max(ps)-min(ps):.1f}%p**")
    return "\n".join(out)


def run(o, trad, shares, start, end, tag):
    df = build(o, trad, shares, start, end)
    L = [f"\n\n# ===== {tag} ({start}~{end}) =====",
         f"후보풀 N={len(df)}, 베이스라인 P={ (df['gap']>0).mean()*100:.1f}%"]
    # 8) 시총
    cov = df["mc_eok"].notna().mean() * 100
    L.append(f"\n## 8) 시총  (shares 적재 커버리지 {cov:.0f}%)")
    mc = df[df["mc_eok"].notna()]
    L.append(bucket(mc, "mc_eok", "시총(억원)", edges=[0, 3000, 10000, 30000, 1e9],
                    labels=["<3천억", "3천억~1조", "1조~3조", "3조+"]))
    # 9) 끼
    L.append("\n## 9) 종목 끼 (과거 1년 ret≥10% 날의 갭상비율, 표본≥3)")
    L.append(bucket(df, "kki", "끼",
                    custom=lambda x: "표본부족" if x is None or pd.isna(x) else (
                        "<50%" if x < 0.5 else ("50~65%" if x < 0.65 else "65%+"))))
    # 10) 종가위치
    L.append("\n## 10) 종가위치 (close-low)/(high-low)")
    L.append(bucket(df, "cpos", "종가위치", edges=[-0.01, 0.5, 0.8, 0.95, 1.01],
                    labels=["~0.5(중하단)", "0.5~0.8", "0.8~0.95", "0.95~1.0(고가권)"]))
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full-start", default="2025-05-08")
    ap.add_argument("--m3-start", default="2026-02-24")
    ap.add_argument("--end", default="2026-05-20")
    args = ap.parse_args()
    s = load_settings()
    o, trad, shares = _load(s.data_dir)
    end = dt.date.fromisoformat(args.end)
    rep = (run(o, trad, shares, dt.date.fromisoformat(args.full_start), end, "1년")
           + run(o, trad, shares, dt.date.fromisoformat(args.m3_start), end, "3개월"))
    print(rep)
    (s.data_dir / "backtest" / "factor_edge2.md").write_text(rep, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
