"""R4 v2 종배 룰 backtest — daily_ohlcv 기반 (round 41 후속 3, 2026-05-19).

목적:
    round 41 본문이 "거래대금 top 50 universe 5/11~5/14 17종목 갭상 58.8%"
    결과를 제시했으나, round 41 후속 2 에서 KIS volume-rank 가 사실 거래량
    universe 였음이 드러남. round 41 본문 backtest 는 daily_ohlcv 기반이라
    거래대금 universe 가 정확했을 가능성 높음 — 본 스크립트로 재현해서 확인 +
    30 vs 50 비교.

데이터:
    - data/daily/ohlcv.parquet (전종목 일봉 — code/date/open/high/low/close/
      volume/trading_value/change_rate)
    - data/meta/stocks.parquet (master — code/name/market_cap, ETF/ETN/리츠/
      스팩 제외 jongbae_only 필터 통과 종목)

룰 (R4 v2 hard cut):
    (a) 거래대금 top N universe (단일종목 — master 통과)
    (b) ret > 0 — (e) 의 strict subset, 별도 코드 X
    (c) (high - close) / high ≤ 10%
    (e) 10% ≤ ret ≤ 27%

갭상 측정:
    gap_pct = (next_day_open - today_close) / today_close × 100
    next_day_close_pct = (next_day_close - today_close) / today_close × 100

사용:
    python scripts/backtest_r4v2.py
    python scripts/backtest_r4v2.py --start 2026-05-11 --end 2026-05-15 --topn 30 50
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import pandas as pd

from src.config import load_settings

# R4 v2 hard cut 임계값
MIN_RET = 10.0
MAX_RET = 27.0
MAX_DROP_FROM_HIGH_PCT = 10.0


def _load_data(data_dir: Path) -> tuple[pd.DataFrame, set[str]]:
    """daily_ohlcv + master 코드 로드. change_rate 가 NaN 인 경우 자체 계산.

    storage 가 change_rate 를 자동 채워주지 않으므로 (plan.md 기술 부채) 본
    스크립트에서 종목별 close pct_change 로 직접 채움. ret 정의 = (close -
    prev_close) / prev_close × 100.
    """
    ohlcv = pd.read_parquet(data_dir / "daily" / "ohlcv.parquet")
    master = pd.read_parquet(data_dir / "meta" / "stocks.parquet")
    tradable = set(master["code"].astype(str).tolist())

    # change_rate 가 NaN 이면 종목별 close pct_change 로 직접 계산
    ohlcv = ohlcv.sort_values(["code", "date"]).reset_index(drop=True)
    ohlcv["ret_calc"] = (
        ohlcv.groupby("code")["close"].pct_change() * 100.0
    )
    return ohlcv, tradable


def _next_business_day(dates: list[dt.date], current: dt.date) -> dt.date | None:
    """dates(정렬됨) 에서 current 보다 큰 첫 날."""
    for d in dates:
        if d > current:
            return d
    return None


def _apply_r4v2(
    day_df: pd.DataFrame,
    top_n: int,
    tradable_codes: set[str],
) -> pd.DataFrame:
    """그날 일봉에 R4 v2 (a)~(e) hard cut 적용 후 통과 종목 DF.

    Args:
        day_df: 한 날짜의 전종목 일봉 DF (open/high/low/close/trading_value).
        top_n: 거래대금 top N universe.
        tradable_codes: master 통과 종목 코드 set.

    Returns:
        통과 종목 DF (rank/code/ret/drop_pct 포함).
    """
    # master 필터 + 양수 종가 (suspension/0 제외)
    df = day_df[
        day_df["code"].astype(str).isin(tradable_codes)
        & (day_df["close"] > 0)
        & (day_df["high"] > 0)
    ].copy()

    # 거래대금 desc top N (a)
    df = df.sort_values("trading_value", ascending=False).head(top_n).reset_index(drop=True)
    df["rank"] = range(1, len(df) + 1)

    # ret = ret_calc (load 시 종목별 pct_change 계산). change_rate 는 storage 가
    # 채워주지 않아 NaN 인 경우 많음 (plan.md 기술 부채).
    df["ret"] = df["ret_calc"].astype(float)

    # (c) 종가 고가 -10% 이내
    df["drop_pct"] = (df["high"] - df["close"]) / df["high"] * 100.0

    # hard cut 적용
    passed = df[
        (df["ret"] >= MIN_RET)
        & (df["ret"] <= MAX_RET)
        & (df["drop_pct"] <= MAX_DROP_FROM_HIGH_PCT)
    ].copy()
    return passed


def _measure_gap(
    passed_today: pd.DataFrame,
    next_day_df: pd.DataFrame,
) -> pd.DataFrame:
    """today 통과 종목 × next_day 시가/종가 join → gap_pct 측정."""
    next_lookup = next_day_df.set_index("code")[["open", "close"]]
    out_rows = []
    for _, r in passed_today.iterrows():
        code = str(r["code"])
        if code not in next_lookup.index:
            continue
        nxt_open = next_lookup.loc[code, "open"]
        nxt_close = next_lookup.loc[code, "close"]
        today_close = r["close"]
        if today_close <= 0:
            continue
        gap = (nxt_open - today_close) / today_close * 100.0
        hold_close = (nxt_close - today_close) / today_close * 100.0
        out_rows.append({
            "date": r["date"],
            "code": code,
            "rank": r["rank"],
            "ret": r["ret"],
            "drop_pct": r["drop_pct"],
            "close": today_close,
            "next_open": nxt_open,
            "next_close": nxt_close,
            "gap_pct": gap,
            "hold_close_pct": hold_close,
        })
    return pd.DataFrame(out_rows)


def _backtest_for_topn(
    ohlcv: pd.DataFrame,
    tradable_codes: set[str],
    start: dt.date,
    end: dt.date,
    top_n: int,
) -> pd.DataFrame:
    """[start, end] 범위에서 R4 v2 백테스트 — top_n universe."""
    dates_sorted: list[dt.date] = sorted(ohlcv["date"].unique().tolist())
    result_frames: list[pd.DataFrame] = []
    for d in dates_sorted:
        if d < start or d > end:
            continue
        nxt = _next_business_day(dates_sorted, d)
        if nxt is None:
            continue  # 마지막 영업일 — 다음날 갭 측정 불가
        day_df = ohlcv[ohlcv["date"] == d]
        next_day_df = ohlcv[ohlcv["date"] == nxt]
        passed = _apply_r4v2(day_df, top_n, tradable_codes)
        if passed.empty:
            continue
        gap_df = _measure_gap(passed, next_day_df)
        if not gap_df.empty:
            result_frames.append(gap_df)
    if not result_frames:
        return pd.DataFrame(columns=[
            "date", "code", "rank", "ret", "drop_pct", "close",
            "next_open", "next_close", "gap_pct", "hold_close_pct",
        ])
    return pd.concat(result_frames, ignore_index=True)


def _summary(label: str, df: pd.DataFrame) -> None:
    print(f"\n===== {label} =====")
    if df.empty:
        print("  통과 종목 없음")
        return
    n = len(df)
    gap_up = (df["gap_pct"] > 0).sum()
    p_gap_up = gap_up / n * 100.0
    avg_gap = df["gap_pct"].mean()
    median_gap = df["gap_pct"].median()
    max_gap = df["gap_pct"].max()
    min_gap = df["gap_pct"].min()
    avg_hold = df["hold_close_pct"].mean()
    print(f"  통과 종목 N      = {n}")
    print(f"  갭상 확률 (>0%)  = {p_gap_up:.1f}% ({gap_up}/{n})")
    print(f"  평균 갭률        = {avg_gap:+.2f}%")
    print(f"  중앙 갭률        = {median_gap:+.2f}%")
    print(f"  최대 갭상        = {max_gap:+.2f}%")
    print(f"  최악 갭하락      = {min_gap:+.2f}%")
    print(f"  다음날 종가 평균  = {avg_hold:+.2f}%")
    print()
    print(f"  종목별 (date, code, ret, gap_pct, hold_close_pct):")
    for _, r in df.iterrows():
        print(
            f"    {r['date']} {r['code']:<8} ret={r['ret']:+5.2f}% "
            f"drop={r['drop_pct']:5.2f}% "
            f"gap={r['gap_pct']:+5.2f}% hold={r['hold_close_pct']:+5.2f}%"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2026-05-11", help="시작 날짜 YYYY-MM-DD")
    parser.add_argument("--end", default="2026-05-15", help="종료 날짜 (포함) YYYY-MM-DD")
    parser.add_argument(
        "--topn", nargs="+", type=int, default=[30, 50],
        help="비교할 거래대금 top_n 리스트 (기본: 30 50)",
    )
    args = parser.parse_args()

    settings = load_settings()
    print(f"data_dir = {settings.data_dir}")
    ohlcv, tradable = _load_data(settings.data_dir)
    print(f"daily ohlcv: {len(ohlcv)} 행, master 통과 코드: {len(tradable)}개")
    print(f"날짜 범위: {ohlcv['date'].min()} ~ {ohlcv['date'].max()}")

    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)

    print(f"\nR4 v2 hard cut:")
    print(f"  (a) 거래대금 top N  (compare: {args.topn})")
    print(f"  (b) ret > 0  (= e 의 strict subset)")
    print(f"  (c) (high - close) / high ≤ {MAX_DROP_FROM_HIGH_PCT}%")
    print(f"  (e) {MIN_RET}% ≤ ret ≤ {MAX_RET}%")
    print(f"  master 통과 (ETF/ETN/리츠/스팩/펀드/우선주 제외)")
    print(f"\nBacktest 기간: {start} ~ {end}")

    results: dict[int, pd.DataFrame] = {}
    for top_n in args.topn:
        df = _backtest_for_topn(ohlcv, tradable, start, end, top_n)
        results[top_n] = df

    for top_n, df in results.items():
        _summary(f"top_n = {top_n}", df)

    # 비교 (top_n 별로)
    if len(args.topn) > 1:
        print("\n===== 비교 =====")
        print(f"{'top_n':>6} {'N':>5} {'P(갭상)':>10} {'평균갭':>10} {'중앙갭':>10} {'다음종가':>10}")
        for top_n, df in results.items():
            if df.empty:
                print(f"{top_n:>6} {0:>5} {'—':>10} {'—':>10} {'—':>10} {'—':>10}")
                continue
            n = len(df)
            p = (df["gap_pct"] > 0).sum() / n * 100.0
            print(f"{top_n:>6} {n:>5} {p:>9.1f}% {df['gap_pct'].mean():>+9.2f}% "
                  f"{df['gap_pct'].median():>+9.2f}% {df['hold_close_pct'].mean():>+9.2f}%")

        # 30 → 50 확장으로 새로 들어온 종목
        if 30 in results and 50 in results:
            codes_30 = set(zip(results[30]["date"].astype(str), results[30]["code"].astype(str)))
            codes_50 = set(zip(results[50]["date"].astype(str), results[50]["code"].astype(str)))
            new_in_50 = codes_50 - codes_30
            print(f"\n  30→50 확장으로 새로 들어온 (date, code) 쌍: {len(new_in_50)}개")
            new_rows = results[50][
                results[50].apply(
                    lambda r: (str(r["date"]), str(r["code"])) in new_in_50,
                    axis=1,
                )
            ]
            if not new_rows.empty:
                for _, r in new_rows.iterrows():
                    print(
                        f"    {r['date']} {r['code']} (rank={r['rank']}) "
                        f"ret={r['ret']:+.2f}% gap={r['gap_pct']:+.2f}% "
                        f"hold={r['hold_close_pct']:+.2f}%"
                    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
