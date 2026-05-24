"""종배 후보 내 팩터 변별력(univariate edge) 측정 — "어떤 지표를 얼마나 강하게 볼까".

목적 (사용자 2026-05-25 메인 질문):
    hard cut(거래대금/ret/drop)을 통과한 "후보 풀" 안에서, 각 보조지표가
    다음날 갭상을 실제로 얼마나 가르는지(변별력)를 단변량으로 측정한다.
    자작 가중합을 만들기 전에, **데이터가 각 지표를 스스로 순위 매기게** 하는 1단계.

방법:
    - 후보 풀 = 거래대금 top50 ∩ (5 ≤ ret ≤ 27) ∩ (drop≤10%)  [현재 운영 룰]
    - 각 팩터를 버킷으로 쪼개 버킷별 N / P(갭상) / 평균갭 / 다음종가 측정.
    - "스프레드" = 버킷 간 P(갭상) 최대-최소. 클수록 변별력 큰 팩터(주의: 단변량,
      교란 가능 + 다중비교라 효과크기+N 같이 봐야 함).
    - 별도로 ret>27 (점상한가 영역) 은 매수가능성 caveat 와 함께 참고 표시.

backtestable 팩터 (일봉만으로):
    drop_pct, 거래대금순위, 거래량순위, 52주신고가, 연속상승일, 최근10일 장대양봉수.
NOT backtestable (forward only — 별도 표시):
    회전율(시총 120종목만), 수급(외인/기관/프로그램), 체결강도/분봉가속/봉형태/호가.

사용: python scripts/backtest_factor_edge.py
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys

import pandas as pd

from src.config import load_settings

MIN_RET, MAX_RET, MAX_DROP = 5.0, 27.0, 10.0


def _load(data_dir):
    ohlcv = pd.read_parquet(data_dir / "daily" / "ohlcv.parquet")
    master = pd.read_parquet(data_dir / "meta" / "stocks.parquet")
    name = dict(zip(master["code"].astype(str), master["name"].astype(str)))
    trad = set(master["code"].astype(str))
    ohlcv["code"] = ohlcv["code"].astype(str)
    ohlcv = ohlcv.sort_values(["code", "date"]).reset_index(drop=True)
    ohlcv["ret"] = ohlcv.groupby("code")["close"].pct_change() * 100.0
    return ohlcv, name, trad


def _consec_up(hist: pd.DataFrame, idx: int) -> int:
    """idx(오늘) 포함, 거꾸로 ret>0 연속 일수."""
    n = 0
    i = idx
    while i >= 0 and pd.notna(hist.iloc[i]["ret"]) and hist.iloc[i]["ret"] > 0:
        n += 1
        i -= 1
    return n


def _big_candles_10d(hist: pd.DataFrame, idx: int, thr: float = 10.0) -> int:
    """최근 10거래일(오늘 포함) 중 ret≥thr 인 날 수."""
    lo = max(0, idx - 9)
    window = hist.iloc[lo:idx + 1]
    return int((window["ret"] >= thr).sum())


def build_pool(ohlcv, trad, start, end):
    by_code = {c: g.reset_index(drop=True) for c, g in ohlcv.groupby("code")}
    code_idx = {c: {d: i for i, d in enumerate(g["date"].tolist())} for c, g in by_code.items()}
    dates = sorted(ohlcv["date"].unique())
    rows = []
    for di, d in enumerate(dates):
        if d < start or d > end or di + 1 >= len(dates):
            continue
        nxt = dates[di + 1]
        day = ohlcv[ohlcv["date"] == d]
        day = day[day["code"].isin(trad) & (day["close"] > 0) & (day["high"] > 0) & day["ret"].notna()].copy()
        day["tv_rank"] = day["trading_value"].rank(ascending=False, method="first")
        day["vol_rank"] = day["volume"].rank(ascending=False, method="first")
        day = day[day["tv_rank"] <= 50]
        day["drop_pct"] = (day["high"] - day["close"]) / day["high"] * 100.0
        nl = ohlcv[ohlcv["date"] == nxt].set_index("code")["open"]
        nl = nl[~nl.index.duplicated()]
        ncl = ohlcv[ohlcv["date"] == nxt].set_index("code")["close"]
        ncl = ncl[~ncl.index.duplicated()]
        for _, r in day.iterrows():
            code = r["code"]
            ret = r["ret"]
            over27 = ret > MAX_RET
            in_pool = (MIN_RET <= ret <= MAX_RET) and (r["drop_pct"] <= MAX_DROP)
            if not (in_pool or over27):
                continue
            if code not in nl.index:
                continue
            hist = by_code[code]
            idx = code_idx[code].get(d)
            prior = hist[hist["date"] < d].tail(250)
            is52 = bool(r["high"] > prior["close"].max()) if len(prior) >= 60 else None
            rows.append({
                "date": d, "code": code, "ret": ret, "over27": over27,
                "in_pool": in_pool, "locked": bool(r["high"] == r["close"]),
                "drop_pct": r["drop_pct"], "tv_rank": int(r["tv_rank"]),
                "vol_rank": int(r["vol_rank"]), "is52": is52,
                "consec_up": _consec_up(hist, idx) if idx is not None else None,
                "big10": _big_candles_10d(hist, idx) if idx is not None else None,
                "gap": (nl.loc[code] - r["close"]) / r["close"] * 100.0,
                "hold": (ncl.loc[code] - r["close"]) / r["close"] * 100.0 if code in ncl.index else float("nan"),
            })
    return pd.DataFrame(rows)


def _bucket_stats(df, label, bucket_col):
    out = [f"\n### {label}"]
    out.append(f"| 버킷 | N | P(갭상) | 평균갭 | 중앙갭 | 다음종가 |")
    out.append("|---|--:|--:|--:|--:|--:|")
    ps = []
    for b, g in df.groupby(bucket_col, dropna=False):
        n = len(g)
        if n == 0:
            continue
        p = (g["gap"] > 0).mean() * 100
        ps.append((p, n))
        out.append(f"| {b} | {n} | {p:.1f}% | {g['gap'].mean():+.2f}% | {g['gap'].median():+.2f}% | {g['hold'].mean():+.2f}% |")
    if len(ps) >= 2:
        spread = max(p for p, _ in ps) - min(p for p, _ in ps)
        out.append(f"\n→ **P(갭상) 스프레드: {spread:.1f}%p** (변별력 지표)")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2025-05-08")
    ap.add_argument("--end", default="2026-05-20")
    args = ap.parse_args()
    s = load_settings()
    ohlcv, name, trad = _load(s.data_dir)
    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)
    df = build_pool(ohlcv, trad, start, end)

    pool = df[df["in_pool"]].copy()  # 현재 룰 후보 풀 (5≤ret≤27)
    L = []
    L.append("# 종배 후보 팩터 변별력 (univariate edge)\n")
    L.append(f"- 기간 {start}~{end}, 후보 풀 = 거래대금 top50 ∩ 5≤ret≤27 ∩ drop≤10")
    L.append(f"- 후보 풀 N = **{len(pool)}**, 베이스라인 P(갭상) = **{(pool['gap']>0).mean()*100:.1f}%**, 평균갭 **{pool['gap'].mean():+.2f}%**\n")
    L.append("⚠ 단변량 marginal(교란 가능) + 다중비교 + 강세장 1개 레짐. 효과크기+N 같이 볼 것.")

    # ret 구간 (hard cut 안에서)
    pool["ret_b"] = pd.cut(pool["ret"], [5, 10, 15, 20, 25, 27], include_lowest=True)
    L.append(_bucket_stats(pool, "1) ret 구간 (5~27, hard cut 내부)", "ret_b"))

    pool["drop_b"] = pd.cut(pool["drop_pct"], [-0.01, 1, 3, 5, 10], labels=["0~1%(고가마감)", "1~3%", "3~5%", "5~10%"])
    L.append(_bucket_stats(pool, "2) drop_pct (종가가 고가에서 얼마나 빠졌나)", "drop_b"))

    pool["tv_b"] = pd.cut(pool["tv_rank"], [0, 10, 25, 50], labels=["1~10위", "11~25위", "26~50위"])
    L.append(_bucket_stats(pool, "3) 거래대금 순위", "tv_b"))

    pool["vol_b"] = pd.cut(pool["vol_rank"], [0, 50, 200, 100000], labels=["거래량 top50", "51~200위", "200위+"])
    L.append(_bucket_stats(pool, "4) 거래량 순위 (전체)", "vol_b"))

    L.append(_bucket_stats(pool, "5) 52주 신고가", "is52"))

    pool["cu_b"] = pool["consec_up"].apply(lambda x: "1일" if x == 1 else ("2일" if x == 2 else ("3일+" if x and x >= 3 else "?")))
    L.append(_bucket_stats(pool, "6) 연속 상승일 (오늘 포함)", "cu_b"))

    pool["b10_b"] = pool["big10"].apply(lambda x: "1번째" if x == 1 else ("2번째" if x == 2 else ("3번째+" if x and x >= 3 else "?")))
    L.append(_bucket_stats(pool, "7) 최근10일 장대양봉(ret≥10%) 수 = 오늘이 N번째", "b10_b"))

    # 점상한가 영역 참고
    over = df[df["over27"]].copy()
    if len(over):
        lk = over[over["locked"]]; bb = over[~over["locked"]]
        L.append("\n## [참고] ret>27 점상한가 영역 (hard cut 밖)")
        L.append(f"- 점상한가(KRX 매수불가): N={len(lk)} P(갭상)={(lk['gap']>0).mean()*100:.1f}% 평균갭={lk['gap'].mean():+.2f}%")
        L.append(f"- 상한가 이탈(매수가능): N={len(bb)} P(갭상)={(bb['gap']>0).mean()*100:.1f}% 평균갭={bb['gap'].mean():+.2f}%")

    rep = "\n".join(L)
    print(rep)
    out = s.data_dir / "backtest" / "factor_edge.md"
    out.write_text(rep, encoding="utf-8")
    print(f"\n[저장] {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
