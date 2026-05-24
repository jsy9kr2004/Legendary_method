"""종배 universe 비교 backtest — 거래대금 vs 거래량 축 + ret 컷 민감도.

목적 (사용자 #5, 2026-05-24):
    여태 적재한 전체 영업일 (2025-05-07 ~) 기준으로
    "거래대금 30위 + 거래량 30위" vs "거래대금 50위 + 거래량 50위" 후보 리스트를
    비교. 동시에 #3 의 27% 상한 / 5% 하한 컷 민감도도 같은 데이터로 측정.

데이터:
    - data/daily/ohlcv.parquet (전종목 일봉: code/date/open/high/low/close/
      volume/trading_value/change_rate). ret = 종목별 close pct_change ×100.
    - data/meta/stocks.parquet (master — code/name, ETF/ETN/리츠/스팩 제외 통과).

축 정의:
    - 거래대금(trading_value, 원): 종배 universe 정답 축 (CLAUDE.md).
    - 거래량(volume, 주): 2번째 축 후보 (사용자 #5). 저가주/ETF 편향 주의.
    - 회전율(turnover=거래대금/시총): master.shares 가 120종목만 차 있어
      전 종목 historical backtest 불가 → 본 스크립트에서는 제외 (별도 round).

갭상 측정:
    gap_pct       = (next_open  - today_close) / today_close × 100
    hold_close_pct= (next_close - today_close) / today_close × 100

사용:
    python scripts/backtest_universe_compare.py
    python scripts/backtest_universe_compare.py --start 2025-05-08 --end 2026-05-20
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import pandas as pd

from src.config import load_settings

MAX_DROP_FROM_HIGH_PCT = 10.0


def _load_data(data_dir: Path) -> tuple[pd.DataFrame, dict[str, str], set[str]]:
    ohlcv = pd.read_parquet(data_dir / "daily" / "ohlcv.parquet")
    master = pd.read_parquet(data_dir / "meta" / "stocks.parquet")
    name_map = dict(zip(master["code"].astype(str), master["name"].astype(str)))
    tradable = set(master["code"].astype(str).tolist())
    ohlcv["code"] = ohlcv["code"].astype(str)
    ohlcv = ohlcv.sort_values(["code", "date"]).reset_index(drop=True)
    ohlcv["ret"] = ohlcv.groupby("code")["close"].pct_change() * 100.0
    return ohlcv, name_map, tradable


def _is_52w_high(ohlcv_by_code: dict[str, pd.DataFrame], code: str, date: dt.date,
                 high: float) -> bool | None:
    df = ohlcv_by_code.get(code)
    if df is None:
        return None
    prior = df[df["date"] < date].tail(250)
    if len(prior) < 60:
        return None
    return bool(high > prior["close"].max())


def _day_universe(day_df: pd.DataFrame, tradable: set[str]) -> pd.DataFrame:
    df = day_df[
        day_df["code"].isin(tradable)
        & (day_df["close"] > 0)
        & (day_df["high"] > 0)
        & day_df["ret"].notna()
    ].copy()
    df["tv_rank"] = df["trading_value"].rank(ascending=False, method="first").astype(int)
    df["vol_rank"] = df["volume"].rank(ascending=False, method="first").astype(int)
    df["drop_pct"] = (df["high"] - df["close"]) / df["high"] * 100.0
    return df


def _select(df: pd.DataFrame, *, tv_top: int, vol_top: int | None,
            ret_lo: float, ret_hi: float | None) -> pd.DataFrame:
    """한 날짜 universe DF 에서 config 조건 통과 종목."""
    sel = df[df["tv_rank"] <= tv_top]
    if vol_top is not None:
        sel = sel[sel["vol_rank"] <= vol_top]
    sel = sel[sel["ret"] >= ret_lo]
    if ret_hi is not None:
        sel = sel[sel["ret"] <= ret_hi]
    sel = sel[sel["drop_pct"] <= MAX_DROP_FROM_HIGH_PCT]
    return sel


def _measure(sel: pd.DataFrame, next_lookup: pd.DataFrame, date: dt.date) -> list[dict]:
    rows = []
    for _, r in sel.iterrows():
        code = str(r["code"])
        if code not in next_lookup.index:
            continue
        tclose = r["close"]
        if tclose <= 0:
            continue
        nopen = next_lookup.loc[code, "open"]
        nclose = next_lookup.loc[code, "close"]
        rows.append({
            "date": date, "code": code,
            "tv_rank": int(r["tv_rank"]), "vol_rank": int(r["vol_rank"]),
            "ret": float(r["ret"]), "drop_pct": float(r["drop_pct"]),
            "close": tclose, "high": r["high"],
            "gap_pct": (nopen - tclose) / tclose * 100.0,
            "hold_close_pct": (nclose - tclose) / tclose * 100.0,
        })
    return rows


CONFIGS = [
    # label, tv_top, vol_top, ret_lo, ret_hi  (현재 룰 컷 = drop<=10 공통)
    ("A. 거래대금 top30 (현재 룰 @30)",        30, None, 5.0, 27.0),
    ("B. 거래대금 top50 (현재 룰 @50 = 운영)", 50, None, 5.0, 27.0),
    ("C. 거래대금∩거래량 top30",               30, 30,   5.0, 27.0),
    ("D. 거래대금∩거래량 top50",               50, 50,   5.0, 27.0),
    # --- #3 ret 컷 민감도 (거래대금 top50 고정) ---
    ("E. top50  5≤ret≤27  (=B 기준)",          50, None, 5.0, 27.0),
    ("F. top50  5≤ret  (27% 상한 제거)",       50, None, 5.0, None),
    ("G. top50  0<ret≤27 (5% 하한 제거)",       50, None, 0.01, 27.0),
    ("H. top50  0<ret  (상·하한 모두 제거)",    50, None, 0.01, None),
]


def _stats(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0}
    df = pd.DataFrame(rows)
    n = len(df)
    return {
        "n": n,
        "p_up": (df["gap_pct"] > 0).sum() / n * 100.0,
        "avg_gap": df["gap_pct"].mean(),
        "med_gap": df["gap_pct"].median(),
        "max_gap": df["gap_pct"].max(),
        "min_gap": df["gap_pct"].min(),
        "avg_hold": df["hold_close_pct"].mean(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2025-05-08")
    parser.add_argument("--end", default="2026-05-20")
    parser.add_argument("--out", default=None, help="markdown 출력 경로")
    args = parser.parse_args()

    settings = load_settings()
    ohlcv, name_map, tradable = _load_data(settings.data_dir)
    ohlcv_by_code = {c: g for c, g in ohlcv.groupby("code")}
    dates = sorted(ohlcv["date"].unique().tolist())
    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)

    results: dict[str, list[dict]] = {label: [] for label, *_ in CONFIGS}

    for i, d in enumerate(dates):
        if d < start or d > end:
            continue
        nxt = dates[i + 1] if i + 1 < len(dates) else None
        if nxt is None:
            continue
        day_df = ohlcv[ohlcv["date"] == d]
        uni = _day_universe(day_df, tradable)
        if uni.empty:
            continue
        next_lookup = ohlcv[ohlcv["date"] == nxt].set_index("code")[["open", "close"]]
        next_lookup = next_lookup[~next_lookup.index.duplicated()]
        for label, tv_top, vol_top, ret_lo, ret_hi in CONFIGS:
            sel = _select(uni, tv_top=tv_top, vol_top=vol_top, ret_lo=ret_lo, ret_hi=ret_hi)
            results[label].extend(_measure(sel, next_lookup, d))

    # 52주 신고가 부착 (후보만)
    for rows in results.values():
        for r in rows:
            r["is_52w_high"] = _is_52w_high(ohlcv_by_code, r["code"], r["date"], r["high"])
            r["name"] = name_map.get(r["code"], "?")

    # ---- 리포트 ----
    lines: list[str] = []
    lines.append(f"# 종배 universe 비교 backtest\n")
    lines.append(f"- 기간: **{start} ~ {end}** (다음날 갭 측정 가능 영업일)")
    lines.append(f"- 일봉 종목수: ~{int(ohlcv[ohlcv['date']==dates[-2]].shape[0])}/day, master 통과 {len(tradable)}")
    lines.append(f"- 공통 컷: 종가 고가-10% 이내 (drop≤10%)")
    lines.append(f"- ⚠ 거래량(volume,주) ≠ 거래대금(trading_value,원). 회전율은 shares 미적재로 제외.\n")

    lines.append("## 요약 비교\n")
    lines.append("| config | N | P(갭상) | 평균갭 | 중앙갭 | 최대갭 | 최악갭 | 다음종가 |")
    lines.append("|---|--:|--:|--:|--:|--:|--:|--:|")
    for label, *_ in CONFIGS:
        s = _stats(results[label])
        if s["n"] == 0:
            lines.append(f"| {label} | 0 | — | — | — | — | — | — |")
            continue
        lines.append(
            f"| {label} | {s['n']} | {s['p_up']:.1f}% | {s['avg_gap']:+.2f}% | "
            f"{s['med_gap']:+.2f}% | {s['max_gap']:+.2f}% | {s['min_gap']:+.2f}% | {s['avg_hold']:+.2f}% |"
        )

    # 27% 상한 제거로 새로 들어온 종목 (F - E)
    e_keys = {(r["date"], r["code"]) for r in results["E. top50  5≤ret≤27  (=B 기준)"]}
    f_extra = [r for r in results["F. top50  5≤ret  (27% 상한 제거)"]
               if (r["date"], r["code"]) not in e_keys]
    lines.append(f"\n## 27% 상한 제거 시 추가 포착 종목 (F−E): {len(f_extra)}개\n")
    if f_extra:
        s = _stats(f_extra)
        lines.append(f"이 추가분만의 통계: N={s['n']} P(갭상)={s['p_up']:.1f}% 평균갭={s['avg_gap']:+.2f}% 다음종가={s['avg_hold']:+.2f}%\n")
        lines.append("| date | code | name | ret | 거래대금순위 | gap | hold | 52wH |")
        lines.append("|---|---|---|--:|--:|--:|--:|:--:|")
        for r in sorted(f_extra, key=lambda x: (x["date"], -x["ret"])):
            h = {True: "✓", False: "✗", None: "—"}[r["is_52w_high"]]
            lines.append(
                f"| {r['date']} | {r['code']} | {r['name']} | {r['ret']:+.1f}% | "
                f"{r['tv_rank']} | {r['gap_pct']:+.2f}% | {r['hold_close_pct']:+.2f}% | {h} |"
            )

    # 5% 하한 제거로 새로 들어온 종목 (G - E)
    g_extra = [r for r in results["G. top50  0<ret≤27 (5% 하한 제거)"]
               if (r["date"], r["code"]) not in e_keys]
    lines.append(f"\n## 5% 하한 제거 시 추가 포착 종목 (G−E): {len(g_extra)}개\n")
    if g_extra:
        s = _stats(g_extra)
        lines.append(f"이 추가분만의 통계 (0<ret<5%): N={s['n']} P(갭상)={s['p_up']:.1f}% 평균갭={s['avg_gap']:+.2f}% 다음종가={s['avg_hold']:+.2f}%\n")

    # 거래량 축 추가가 떨군 종목 (B - D)
    d_keys = {(r["date"], r["code"]) for r in results["D. 거래대금∩거래량 top50"]}
    b_dropped = [r for r in results["B. 거래대금 top50 (현재 룰 @50 = 운영)"]
                 if (r["date"], r["code"]) not in d_keys]
    lines.append(f"\n## 거래량 top50 ∩ 으로 추가 탈락한 종목 (B−D): {len(b_dropped)}개\n")
    if b_dropped:
        s = _stats(b_dropped)
        lines.append(f"거래대금 top50 였지만 거래량 top50 밖이라 탈락한 후보들의 통계: "
                     f"N={s['n']} P(갭상)={s['p_up']:.1f}% 평균갭={s['avg_gap']:+.2f}% 다음종가={s['avg_hold']:+.2f}%\n")

    report = "\n".join(lines)
    print(report)

    out = Path(args.out) if args.out else settings.data_dir / "backtest" / "universe_compare.md"
    out.write_text(report, encoding="utf-8")
    # 후보 CSV 도 저장 (config B 기준 — 운영 룰)
    pd.DataFrame(results["B. 거래대금 top50 (현재 룰 @50 = 운영)"]).to_csv(
        settings.data_dir / "backtest" / "universe_compare_B_top50.csv", index=False)
    print(f"\n[저장] {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
