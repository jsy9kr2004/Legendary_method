"""Eod.Pick 하한(MIN_DAILY_RETURN)·상한(MAX) backtest — 1년치 top50 유니버스.

질문: 일봉 상승률 하한을 5%→0%로 바꾸면 다음날 시초 갭상 엣지가 좋아지나?
대조: 레전드 픽 60개가 아니라 시스템 자체 유니버스(거래대금 top50) 전체 1년치로 robust 검증.

각 거래일:
    1. 종목 유니버스 = stocks.parquet (KOSPI+KOSDAQ 주식; ETF/ETN 등 제외)
    2. 거래대금 상위 50
    3. 후보 = floor ≤ 일봉상승률 ≤ cap  AND  종가 ≥ 고가×0.9 (Eod.Pick v2 (c))
    4. 수익 = 다음 거래일 시가 / 오늘 종가 - 1  (시초 매도 가정)

집계: 후보 수, 갭상확률, 평균/중앙 갭. 사용자 hold-3 반영해 '거래대금순위 top3' 별도 집계.
"""
from __future__ import annotations

import pandas as pd

CLOSE_HIGH_FILTER = 0.90  # 종가 ≥ 고가×0.9 (고가-10% 이내)


def load() -> pd.DataFrame:
    o = pd.read_parquet("data/daily/ohlcv.parquet")
    o["code"] = o["code"].astype(str).str.zfill(6)
    o["date"] = o["date"].astype(str)
    stocks = pd.read_parquet("data/meta/stocks.parquet")
    eq = set(stocks["code"].astype(str).str.zfill(6))  # 주식 유니버스 (ETF 등 제외)
    o = o[o["code"].isin(eq)].copy()
    o = o.sort_values(["code", "date"])
    o["ret"] = o.groupby("code")["close"].pct_change() * 100.0
    o["next_open"] = o.groupby("code")["open"].shift(-1)
    o["gap"] = (o["next_open"] - o["close"]) / o["close"] * 100.0
    return o


def run(o: pd.DataFrame, floor: float, cap: float) -> dict:
    rows_all = []
    rows_top3 = []
    days = sorted(o["date"].unique())
    for d in days:
        day = o[o["date"] == d]
        day = day.dropna(subset=["ret", "gap", "high", "close"])
        if day.empty:
            continue
        top50 = day.sort_values("trading_value", ascending=False).head(50)
        cand = top50[
            (top50["ret"] >= floor)
            & (top50["ret"] <= cap)
            & (top50["close"] >= top50["high"] * CLOSE_HIGH_FILTER)
        ].copy()
        if cand.empty:
            continue
        # 거래대금순위 내림차순 정렬 (운영과 동일)
        cand = cand.sort_values("trading_value", ascending=False)
        rows_all.append(cand[["date", "code", "ret", "gap"]])
        rows_top3.append(cand.head(3)[["date", "code", "ret", "gap"]])

    allc = pd.concat(rows_all) if rows_all else pd.DataFrame(columns=["gap"])
    top3 = pd.concat(rows_top3) if rows_top3 else pd.DataFrame(columns=["gap"])
    n_days = allc["date"].nunique() if len(allc) else 0

    def agg(df: pd.DataFrame) -> dict:
        if df.empty:
            return dict(n=0, wr=float("nan"), mean=float("nan"), med=float("nan"))
        return dict(
            n=len(df),
            wr=(df["gap"] > 0).mean() * 100,
            mean=df["gap"].mean(),
            med=df["gap"].median(),
        )

    return {
        "floor": floor, "cap": cap, "trading_days": n_days,
        "all": agg(allc), "top3": agg(top3),
        "cand_per_day": len(allc) / n_days if n_days else 0,
    }


def main() -> None:
    o = load()
    print(f"데이터: {o['date'].min()} ~ {o['date'].max()}  ({o['date'].nunique()} 거래일, {o['code'].nunique()} 종목)")
    print("수익 정의: 다음 거래일 시가/오늘 종가-1 (시초 매도). top50 유니버스, 종가≥고가×0.9.\n")

    configs = [
        ("현행 (5~27%)", 5.0, 27.0),
        ("하한 3 (3~27%)", 3.0, 27.0),
        ("하한 0 (0~27%)", 0.0, 27.0),
        ("하락 포함 (-100~27%)", -100.0, 27.0),
        ("하한 0·상한 해제 (0~100%)", 0.0, 100.0),
    ]

    hdr = f"{'설정':<26}{'후보/일':>7}  │ {'전체: n':>6} {'갭상%':>6} {'평균갭':>7} {'중앙갭':>7}  │ {'top3: n':>7} {'갭상%':>6} {'평균갭':>7} {'중앙갭':>7}"
    print(hdr)
    print("─" * len(hdr))
    for label, floor, cap in configs:
        r = run(o, floor, cap)
        a, t = r["all"], r["top3"]
        print(
            f"{label:<26}{r['cand_per_day']:>7.1f}  │ "
            f"{a['n']:>6d} {a['wr']:>5.0f}% {a['mean']:>+6.2f}% {a['med']:>+6.2f}%  │ "
            f"{t['n']:>7d} {t['wr']:>5.0f}% {t['mean']:>+6.2f}% {t['med']:>+6.2f}%"
        )

    print("\n참고: '하한 0'에서 0~5% 구간만 떼어 본 추가 엣지")
    days = sorted(o["date"].unique())
    seg = []
    for d in days:
        day = o[o["date"] == d].dropna(subset=["ret", "gap", "high", "close"])
        if day.empty:
            continue
        top50 = day.sort_values("trading_value", ascending=False).head(50)
        c = top50[(top50["ret"] >= 0) & (top50["ret"] < 5)
                  & (top50["close"] >= top50["high"] * CLOSE_HIGH_FILTER)]
        if len(c):
            seg.append(c[["gap"]])
    s = pd.concat(seg) if seg else pd.DataFrame(columns=["gap"])
    if len(s):
        print(f"  0~5% 버킷 (현행이 버리는 구간): n={len(s)}  갭상 {(s['gap']>0).mean()*100:.0f}%  "
              f"평균 {s['gap'].mean():+.2f}%  중앙 {s['gap'].median():+.2f}%")


if __name__ == "__main__":
    main()
