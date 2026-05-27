"""단저단고 (mean reversion) 백테스트.

`docs/scalping-redesign-2026-05-27.md` §3 v3 시그널 5/18~5/27 baseline.

핵심 가정:
  - 매수: 봉 close 후 다음 봉 open (look-ahead 없음, 단 지연 ~0.37% 슬리피지)
  - 매도 우선: stop -2% > trailing peak-1% > sigS > max_hold 30분 > EOD
  - universe 게이트: 매수 시점 rank ≤ 30 OR turnover ≥ 30 (사용자 비전)
  - 종목 국면 게이트: daily_return ≥ 0 (catch falling knife 회피)
  - 종일 반복 매매 허용 (단저단고 본질)

지정가/시장가 비용 시나리오:
  - 시장가 0.4% (현재 사용자 시장가 매매 가정)
  - 지정가 0.2% (사용자 지정가 매매 시)
  - 유동 리더 + 지정가 0.15% (최선 시나리오)

CLI 사용:
  python -m src.research.backtest_mean_reversion --days 6 --output data/backtest/mr_v3.json
"""
from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.scalping.bars import build_bars
from src.scalping.signals.mean_reversion import classify


# 청산 임계
STOP_LOSS_PCT = -2.0
TRAILING_PCT = -1.0
TRAILING_MIN_PEAK_PCT = 0.5
MAX_HOLD_BARS = 10  # 3분봉 × 10 = 30분
EOD_TIME = "15:15"

# universe 게이트
RANK_MAX = 30
TURNOVER_MIN = 30.0
DAILY_RETURN_MIN = 0.0

# 비용 시나리오
COST_SCENARIOS = {"market_0.4%": 0.4, "limit_0.2%": 0.2, "limit_liquid_0.15%": 0.15}


def simulate_day_code(
    bars: pd.DataFrame,
    dr_series: pd.Series,
    rank_series: pd.Series,
    turnover_series: pd.Series,
) -> list[dict]:
    """1 종목 1 일자 — 반복 매매 시뮬레이션."""
    bars = bars.copy()
    bars["next_open"] = bars["open"].shift(-1)
    trades: list[dict] = []
    pos: dict | None = None
    eod_time = pd.Timestamp(EOD_TIME).time()

    for i, (ts, row) in enumerate(bars.iterrows()):
        if pos is None:
            if not row["sigB"] or pd.isna(row["next_open"]):
                continue
            dr = dr_series.asof(ts)
            rk = rank_series.asof(ts)
            tr = turnover_series.asof(ts)
            if pd.isna(dr) or dr < DAILY_RETURN_MIN:
                continue
            if pd.isna(rk):
                continue
            in_universe = (rk <= RANK_MAX) or (pd.notna(tr) and tr >= TURNOVER_MIN)
            if not in_universe:
                continue
            pos = {"entry_ts": ts, "entry_price": float(row["next_open"]), "entry_idx": i, "peak": float(row["next_open"])}
            continue

        pos["peak"] = max(pos["peak"], float(row["high"]))
        ep = pos["entry_price"]
        peak = pos["peak"]
        sl_price = ep * (1 + STOP_LOSS_PCT / 100)
        tr_price = peak * (1 + TRAILING_PCT / 100)
        hold_bars = i - pos["entry_idx"]
        is_eod = ts.time() >= eod_time

        exit_price = None
        reason = None
        if row["low"] <= sl_price:
            exit_price = sl_price
            reason = "stop_loss"
        elif row["low"] <= tr_price and peak > ep * (1 + TRAILING_MIN_PEAK_PCT / 100):
            exit_price = tr_price
            reason = "trailing"
        elif row["sigS"] and pd.notna(row["next_open"]):
            exit_price = float(row["next_open"])
            reason = "sigS"
        elif hold_bars >= MAX_HOLD_BARS:
            exit_price = float(row["close"]) if pd.isna(row["next_open"]) else float(row["next_open"])
            reason = "max_hold"
        elif is_eod:
            exit_price = float(row["close"]) if pd.isna(row["next_open"]) else float(row["next_open"])
            reason = "eod"

        if exit_price is None:
            continue
        ret_pct = (exit_price / ep - 1) * 100
        trades.append(
            {
                "entry_ts": pos["entry_ts"],
                "entry_price": ep,
                "exit_ts": ts,
                "exit_price": exit_price,
                "ret_pct": ret_pct,
                "hold_min": (ts - pos["entry_ts"]).total_seconds() / 60,
                "reason": reason,
                "peak_pct": (peak / ep - 1) * 100,
            }
        )
        pos = None
    return trades


def run_backtest(tick_log_files: list[str]) -> pd.DataFrame:
    """tick log 파일 list → 매매 DataFrame."""
    all_trades: list[dict] = []
    for f in tick_log_files:
        df = pd.read_parquet(f)
        df["ts"] = pd.to_datetime(df["ts"])
        date = df["ts"].dt.date.iloc[0]
        code_counts = df.groupby("code").size()
        valid_codes = code_counts[code_counts >= 100].index.tolist()
        for code in valid_codes:
            sub = df[df["code"] == code].set_index("ts").sort_index()
            name = sub["name"].iloc[0]
            try:
                bars = build_bars(sub)
                if len(bars) < 25:
                    continue
                classify(bars)
                trades = simulate_day_code(bars, sub["daily_return"], sub["rank"], sub["turnover"])
                for t in trades:
                    t["date"] = date
                    t["code"] = code
                    t["name"] = name
                    all_trades.append(t)
            except Exception as e:
                print(f"  [warn] {code} {date}: {e}")
    return pd.DataFrame(all_trades)


def summarize(tdf: pd.DataFrame) -> dict:
    """매매 DataFrame → 요약 통계 + 비용 시나리오 net."""
    if len(tdf) == 0:
        return {"n": 0}
    summary = {
        "n": int(len(tdf)),
        "gross_per_trade_pct": float(tdf["ret_pct"].mean()),
        "median_per_trade_pct": float(tdf["ret_pct"].median()),
        "winrate_pct": float((tdf["ret_pct"] > 0).mean() * 100),
        "hold_min_mean": float(tdf["hold_min"].mean()),
        "hold_min_median": float(tdf["hold_min"].median()),
        "gross_total_pct": float(tdf["ret_pct"].sum()),
        "by_reason": tdf.groupby("reason").agg(n=("ret_pct", "count"), avg_pct=("ret_pct", "mean"),
                                                 winrate=("ret_pct", lambda x: (x > 0).mean() * 100)).round(3).to_dict(),
    }
    for label, cost in COST_SCENARIOS.items():
        summary[f"net_{label}_per_trade"] = round(summary["gross_per_trade_pct"] - cost, 3)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tick-logs-glob", default="data/tick_logs/2026-05-*.parquet")
    parser.add_argument("--output", default="data/backtest/mr_v3_baseline.json")
    args = parser.parse_args()
    files = sorted(glob.glob(args.tick_logs_glob))
    print(f"백테스트 입력: {len(files)} 파일")
    tdf = run_backtest(files)
    summary = summarize(tdf)
    print(json.dumps({k: v for k, v in summary.items() if k != "by_reason"}, indent=2, ensure_ascii=False))
    print("청산 사유별:")
    print(json.dumps(summary.get("by_reason", {}), indent=2, ensure_ascii=False))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"결과 저장: {output}")


if __name__ == "__main__":
    main()
