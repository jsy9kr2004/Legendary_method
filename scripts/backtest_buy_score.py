"""Buy.Score backtest — variant 별 진입/청산 시뮬레이션.

5/18 ~ 5/20 tick_log parquet 으로 다중 variant 비교. 사용자 룰 (STRONG 매수 +
청산 시그널/-2% 매도) baseline + 변형.

사용:
    python -m scripts.backtest_buy_score
    python -m scripts.backtest_buy_score --variants current,q5_inv_6,r14i_dist_high
    python -m scripts.backtest_buy_score --dates 2026-05-20

caveat:
- 5/18, 5/19 데이터는 KIS volume-rank FID_BLNG_CLS_CODE="0" 버그 시기 →
  universe 가 거래량 기준 (ETF/저가주 편향). variant 간 상대 비교만 신뢰,
  절대 수익률은 5/20 부터 의미.
- 청산은 (a) Buy.Score 등급 STRONG → 이하 강등 OR (b) -stop% 도달 OR (c) EOD.
  Exit.E1~E5 시그널 청산은 미반영 (별도 시뮬 — future work).
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from typing import Callable

import pandas as pd


# ── Variant 정의 ────────────────────────────────────────────────────────────

PenaltyFunc = Callable[[pd.Series], float]


def _r14i_dist_high_penalty(row: pd.Series) -> float:
    """일중 최고점 거리 페널티 (5/20 일지 H7)."""
    intraday_high = row.get("intraday_high")
    price = row.get("price")
    if intraday_high is None or price is None or intraday_high == 0:
        return 0.0
    if intraday_high != intraday_high or price != price:  # NaN
        return 0.0
    dist_pct = (price - intraday_high) / intraday_high * 100  # 음수 또는 0
    # dist_pct가 -1% 이내 (즉 |dist| < 1%) = 일중 최고점 근접
    if dist_pct >= -0.5:  # 0.5% 이내
        return -2.0
    if dist_pct >= -1.0:  # 1% 이내
        return -1.0
    return 0.0


def _r14j_consolidation_penalty(row: pd.Series) -> float:
    """일중 +15% 도달 후 횡보 페널티 (5/20 일지 H8 — 수젠텍 micro fluctuation).

    daily_return ≥ 15% AND 일중 최고점 거리 < 1% → 횡보 구간 micro fluctuation.
    """
    daily_return = row.get("daily_return")
    intraday_high = row.get("intraday_high")
    price = row.get("price")
    if daily_return is None or daily_return != daily_return:
        return 0.0
    # daily_return 단위가 비정상 — 시초 잡음 회피 위해 percent 가정 (5/20 일지 §1.4)
    # 5/20 데이터 일부 ret 350% 같이 잡음 — 200% 초과는 무시
    if daily_return > 200 or daily_return < -200:
        return 0.0
    if daily_return < 15:
        return 0.0
    if intraday_high is None or price is None or intraday_high == 0:
        return 0.0
    if intraday_high != intraday_high or price != price:
        return 0.0
    dist_pct = (price - intraday_high) / intraday_high * 100
    if dist_pct >= -1.0:  # 1% 이내 (횡보)
        return -1.5
    return 0.0


@dataclass
class Variant:
    """Backtest variant — cutoff + 추가 페널티 + 진입 시각 필터."""

    name: str
    cutoff: float = 5.0
    extra_penalty_func: PenaltyFunc | None = None
    earliest_entry_time: time | None = None  # 이 시각 이전 진입 차단


# Variant 정의 — 사용자 결정 + 5/20 일지 §4 가설
VARIANTS: list[Variant] = [
    # === Baseline ===
    Variant("current", cutoff=5.0),
    # === proposal P0 Q5 (cutoff 하향) ===
    Variant("q5_lower_4", cutoff=4.0),
    # === 5/20 일지 H6 (cutoff 상향) ===
    Variant("q5_inv_6", cutoff=6.0),
    Variant("q5_inv_7", cutoff=7.0),
    # === 5/20 일지 H7 (dist_from_intraday_high 페널티) ===
    Variant("r14i_dist_high", cutoff=5.0, extra_penalty_func=_r14i_dist_high_penalty),
    # === 5/20 일지 H8 (일중 +15% 후 횡보 페널티) ===
    Variant("r14j_consolidation", cutoff=5.0, extra_penalty_func=_r14j_consolidation_penalty),
    # === orthogonal 시초 5분 제외 ===
    Variant("exclude_first_5min", cutoff=5.0, earliest_entry_time=time(9, 5, 0)),
    # === 조합 안 ===
    Variant(
        "combo_h6+h7",
        cutoff=6.0,
        extra_penalty_func=_r14i_dist_high_penalty,
    ),
    Variant(
        "combo_h6+h7+nosrcfirst5",
        cutoff=6.0,
        extra_penalty_func=_r14i_dist_high_penalty,
        earliest_entry_time=time(9, 5, 0),
    ),
]


# ── 진입 / 청산 시뮬 ────────────────────────────────────────────────────────


def adjusted_score(row: pd.Series, variant: Variant) -> float:
    """variant 의 페널티 함수 적용한 점수."""
    base = row.get("buy_score")
    if base is None or base != base:
        return float("-inf")
    if variant.extra_penalty_func is not None:
        base = base + variant.extra_penalty_func(row)
    return float(base)


def find_first_entry(
    group: pd.DataFrame, variant: Variant
) -> int | None:
    """variant 의 첫 STRONG 진입 시점 (row index)."""
    for i, row in group.iterrows():
        ts = row["ts"]
        if variant.earliest_entry_time is not None and ts.time() < variant.earliest_entry_time:
            continue
        score = adjusted_score(row, variant)
        if score >= variant.cutoff:
            return i
    return None


def find_exit(
    group: pd.DataFrame,
    entry_idx: int,
    entry_price: float,
    stop_loss_pct: float,
    variant: Variant,
) -> tuple[int, str]:
    """진입 후 청산 시점 + 사유."""
    # 진입 후 tick 들만 순회
    after = group.iloc[entry_idx + 1 :]
    stop_price = entry_price * (1 + stop_loss_pct / 100)
    for i, row in after.iterrows():
        price = row.get("price")
        if price is None or price != price:
            continue
        # (a) Stop-loss
        if price <= stop_price:
            return i, "stop_loss"
        # (b) 등급 강등 (STRONG → WATCH 이하)
        # variant 의 adjusted_score 가 cutoff 미달이면 청산
        score = adjusted_score(row, variant)
        if score < variant.cutoff:
            return i, "grade_demote"
    # (c) EOD — 마지막 tick
    last = group.iloc[-1]
    return last.name, "eod"


# ── Backtest 메인 ──────────────────────────────────────────────────────────


def backtest_one_day(
    df: pd.DataFrame, variant: Variant, stop_loss_pct: float = -2.0
) -> pd.DataFrame:
    """하루치 tick_log + variant + stop → trade 리스트."""
    results: list[dict] = []
    for code, group in df.groupby("code"):
        group = group.sort_values("ts").reset_index(drop=True)
        entry_idx = find_first_entry(group, variant)
        if entry_idx is None:
            continue
        entry_price = group.iloc[entry_idx].get("price")
        if entry_price is None or entry_price != entry_price:
            continue
        exit_idx, exit_reason = find_exit(
            group, entry_idx, entry_price, stop_loss_pct, variant
        )
        exit_price = group.iloc[exit_idx]["price"]
        pnl = (exit_price - entry_price) / entry_price * 100
        results.append({
            "variant": variant.name,
            "code": str(code),
            "name": group.iloc[entry_idx].get("name", ""),
            "entry_ts": group.iloc[entry_idx]["ts"],
            "entry_price": entry_price,
            "entry_score": group.iloc[entry_idx].get("buy_score"),
            "exit_ts": group.iloc[exit_idx]["ts"],
            "exit_price": exit_price,
            "exit_reason": exit_reason,
            "pnl_pct": pnl,
            "holding_sec": (group.iloc[exit_idx]["ts"] - group.iloc[entry_idx]["ts"]).total_seconds(),
        })
    return pd.DataFrame(results)


def summarize(df_all: pd.DataFrame) -> pd.DataFrame:
    """variant × date 별 집계."""
    grouped = df_all.groupby(["variant", "date"]).agg(
        n=("code", "count"),
        avg_pnl=("pnl_pct", "mean"),
        median_pnl=("pnl_pct", "median"),
        win_rate=("pnl_pct", lambda x: (x > 0).mean() * 100 if len(x) > 0 else 0.0),
        avg_hold_min=("holding_sec", lambda x: x.mean() / 60 if len(x) > 0 else 0.0),
        stop_loss_pct=("exit_reason", lambda x: (x == "stop_loss").mean() * 100 if len(x) > 0 else 0.0),
    ).round(2)
    return grouped


def overall_summary(df_all: pd.DataFrame) -> pd.DataFrame:
    """variant 별 3일 합산."""
    return df_all.groupby("variant").agg(
        n=("code", "count"),
        avg_pnl=("pnl_pct", "mean"),
        median_pnl=("pnl_pct", "median"),
        win_rate=("pnl_pct", lambda x: (x > 0).mean() * 100 if len(x) > 0 else 0.0),
        avg_hold_min=("holding_sec", lambda x: x.mean() / 60 if len(x) > 0 else 0.0),
        stop_loss_pct=("exit_reason", lambda x: (x == "stop_loss").mean() * 100 if len(x) > 0 else 0.0),
    ).round(2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--variants",
        type=str,
        default=None,
        help="comma-separated variant names (default: all)",
    )
    ap.add_argument(
        "--dates",
        type=str,
        default=None,
        help="comma-separated dates (default: 2026-05-18,2026-05-19,2026-05-20)",
    )
    ap.add_argument(
        "--stops",
        type=str,
        default="-2.0,-1.5",
        help="comma-separated stop_loss_pct values",
    )
    ap.add_argument("--out", type=Path, default=Path("data/backtest/buy_score_v1.csv"))
    args = ap.parse_args()

    # variants 필터
    if args.variants:
        wanted = set(args.variants.split(","))
        variants = [v for v in VARIANTS if v.name in wanted]
    else:
        variants = VARIANTS

    # dates 필터
    if args.dates:
        dates = args.dates.split(",")
    else:
        dates = ["2026-05-18", "2026-05-19", "2026-05-20"]

    stops = [float(s) for s in args.stops.split(",")]

    # 데이터 로드
    all_results: list[pd.DataFrame] = []
    for date_str in dates:
        path = Path(f"data/tick_logs/{date_str}.parquet")
        if not path.exists():
            print(f"[skip] {path} (not found)")
            continue
        print(f"[load] {path}")
        df = pd.read_parquet(path)
        df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert("Asia/Seoul")
        for variant in variants:
            for stop in stops:
                trades = backtest_one_day(df, variant, stop_loss_pct=stop)
                if trades.empty:
                    continue
                trades["date"] = date_str
                trades["stop"] = stop
                all_results.append(trades)
                print(f"  variant={variant.name} stop={stop} N={len(trades)} avg={trades['pnl_pct'].mean():.2f}%")

    if not all_results:
        print("no trades — exiting")
        return 1

    df_all = pd.concat(all_results, ignore_index=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    df_all.to_csv(args.out, index=False)
    print(f"\n[saved] {args.out} ({len(df_all)} trades)")

    # 출력 — variant × stop × date 별
    print("\n=== variant × stop × date ===")
    for stop in stops:
        sub = df_all[df_all["stop"] == stop]
        if sub.empty:
            continue
        print(f"\n--- stop = {stop}% ---")
        s = sub.groupby(["variant", "date"]).agg(
            n=("code", "count"),
            avg_pnl=("pnl_pct", "mean"),
            win_rate=("pnl_pct", lambda x: (x > 0).mean() * 100),
            stop_hit=("exit_reason", lambda x: (x == "stop_loss").mean() * 100),
        ).round(2)
        print(s.to_string())

    print("\n=== variant × stop (3일 합산) ===")
    for stop in stops:
        sub = df_all[df_all["stop"] == stop]
        if sub.empty:
            continue
        print(f"\n--- stop = {stop}% ---")
        s = sub.groupby("variant").agg(
            n=("code", "count"),
            avg_pnl=("pnl_pct", "mean"),
            median=("pnl_pct", "median"),
            win_rate=("pnl_pct", lambda x: (x > 0).mean() * 100),
            avg_hold_min=("holding_sec", lambda x: x.mean() / 60),
            stop_hit_pct=("exit_reason", lambda x: (x == "stop_loss").mean() * 100),
        ).round(2)
        print(s.to_string())

    return 0


if __name__ == "__main__":
    sys.exit(main())
