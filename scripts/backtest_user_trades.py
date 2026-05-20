"""사용자 실제 매매 시점에서 variant 별 buy_grade 변화 측정 (v2).

5/20 사용자 매매 15건의 매수 ts 시점에서 정점 회피 페널티 적용 시 차단되는
매매 측정. tick_log 의 intraday_high 컬럼이 비어있어서 cumulative max 로
직접 계산.

신규 시그널:
- R14k: 일중 최고점 거리 페널티
   - dist >= -2% (정점 2% 이내) → -2
   - dist >= -5% (정점 5% 이내) → -1
- R14l: 횡보 정점 페널티 (수젠텍 케이스)
   - daily_return >= 15% AND dist >= -5% → -1.5
"""
from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path
from typing import Callable

import pandas as pd


def compute_at_tick_signals_with_intraday(
    bars: pd.DataFrame,
    intraday_high: float,
    tick_ts: pd.Timestamp,
    tick_price: float,
    daily_return: float,
) -> dict:
    """tick 시점 정점 회피 시그널 (intraday_high 직접 계산)."""
    past_bars = bars[bars.index <= tick_ts]
    sig: dict = {}

    # R14e: 5분 전 close 대비 변화율
    if len(past_bars) > 0:
        five_min_ago = tick_ts - pd.Timedelta(minutes=5)
        b5 = past_bars[past_bars.index <= five_min_ago]
        if len(b5) > 0 and b5["close"].iloc[-1] > 0:
            sig["recent_5m_change_pct"] = (tick_price - b5["close"].iloc[-1]) / b5["close"].iloc[-1] * 100
        else:
            sig["recent_5m_change_pct"] = float("nan")
    else:
        sig["recent_5m_change_pct"] = float("nan")

    # R14f: BB
    if len(past_bars) >= 20:
        last20 = past_bars["close"].iloc[-20:]
        bb_upper = last20.mean() + 2 * last20.std()
        sig["bb_position_pct"] = (tick_price - bb_upper) / bb_upper * 100 if bb_upper > 0 else float("nan")
    else:
        sig["bb_position_pct"] = float("nan")

    # R14g
    if len(past_bars) >= 5:
        last5 = past_bars.iloc[-5:]
        sig["recent_5_bars_bullish_count"] = int((last5["close"] > last5["open"]).sum())
    else:
        sig["recent_5_bars_bullish_count"] = 0

    # R14h: 거래량 정점
    if len(past_bars) >= 10:
        recent_5_v = past_bars["value"].iloc[-5:].sum()
        prev_5_v = past_bars["value"].iloc[-10:-5].sum()
        ratio = recent_5_v / prev_5_v if prev_5_v > 0 else 1.0
        flat = sig["recent_5m_change_pct"] < 0.3 if sig["recent_5m_change_pct"] == sig["recent_5m_change_pct"] else False
        sig["volume_peak_with_price_flat"] = (ratio > 2.0 and flat)
    else:
        sig["volume_peak_with_price_flat"] = False

    # R14k: dist from intraday high
    if intraday_high > 0:
        sig["dist_from_high_pct"] = (tick_price - intraday_high) / intraday_high * 100
    else:
        sig["dist_from_high_pct"] = float("nan")

    sig["daily_return"] = daily_return

    return sig


def build_1min_bars(group: pd.DataFrame) -> pd.DataFrame:
    g = group.sort_values("ts").set_index("ts")
    ohlc = g["price"].resample("1min").agg(["first", "max", "min", "last"])
    ohlc.columns = ["open", "high", "low", "close"]
    cum_last = g["trading_value"].resample("1min").last()
    minute_value = cum_last.diff().fillna(cum_last)
    bars = ohlc.copy()
    bars["value"] = minute_value
    bars = bars.dropna(subset=["close"])
    return bars


# === Variant 정의 ===

def _r14e(s: dict) -> float:
    v = s.get("recent_5m_change_pct")
    if v != v: return 0.0
    if v >= 10: return -2.0
    if v >= 5: return -1.0
    return 0.0


def _r14f(s: dict) -> float:
    v = s.get("bb_position_pct")
    if v != v: return 0.0
    if v >= 1: return -2.0
    if v >= 0: return -1.0
    return 0.0


def _r14g(s: dict) -> float:
    n = s.get("recent_5_bars_bullish_count", 0)
    return -1.0 if n >= 4 else 0.0


def _r14h(s: dict) -> float:
    return -1.5 if s.get("volume_peak_with_price_flat") else 0.0


def _r14k(s: dict) -> float:
    """일중 최고점 거리. dist >= -2% → -2, >= -5% → -1."""
    v = s.get("dist_from_high_pct")
    if v != v: return 0.0
    if v >= -2: return -2.0
    if v >= -5: return -1.0
    return 0.0


def _r14l(s: dict) -> float:
    """횡보 정점 — daily_return >= 15% AND dist >= -5% → -1.5."""
    dr = s.get("daily_return")
    if dr is None or dr != dr or abs(dr) > 200: return 0.0
    dist = s.get("dist_from_high_pct")
    if dist != dist: return 0.0
    if dr >= 15 and dist >= -5:
        return -1.5
    return 0.0


def _p1_full(s: dict) -> float:
    return _r14e(s) + _r14f(s) + _r14g(s) + _r14h(s)


def _p1_plus_kl(s: dict) -> float:
    return _r14e(s) + _r14f(s) + _r14g(s) + _r14h(s) + _r14k(s) + _r14l(s)


def _kl_only(s: dict) -> float:
    return _r14k(s) + _r14l(s)


VARIANTS: list[tuple[str, float, Callable | None]] = [
    ("current", 5.0, None),
    ("q5_lower_4", 4.0, None),
    ("q5_inv_6", 6.0, None),
    ("p1_full", 5.0, _p1_full),                # R14e+f+g+h
    ("p1_plus_kl", 5.0, _p1_plus_kl),          # ★ 추가 R14k + R14l
    ("kl_only", 5.0, _kl_only),                # R14k+R14l 단독
    ("p0_p1_kl_combo", 4.0, _p1_plus_kl),      # ★ P0 + 모든 페널티
]


def main() -> int:
    tl = pd.read_parquet("data/tick_logs/2026-05-20.parquet")
    tl["ts"] = pd.to_datetime(tl["ts"], utc=True).dt.tz_convert("Asia/Seoul")

    tr = pd.read_parquet("data/trades/2026-05-20.parquet")
    def parse_ts(x):
        dt = pd.to_datetime(str(x))
        return dt.tz_localize("Asia/Seoul") if dt.tzinfo is None else dt.tz_convert("Asia/Seoul")
    tr["ts"] = tr["ts"].apply(parse_ts)
    buys = tr[tr["action"] == "buy"].sort_values("ts").reset_index(drop=True)

    # 5/20 사용자 매매 분류 + 실제 손익
    classifications = {
        ("440110", "09:01:43"): ("파두", "A_시초", 0.27),
        ("036930", "09:02:31"): ("주성 1차", "A_시초", 1.34),
        ("080220", "09:04:55"): ("제주반도체", "A_시초", 0.10),
        ("011000", "09:06:52"): ("진원", "A_시초", -1.06),
        ("001740", "09:09:01"): ("SK네트웍스", "A_시초", -0.80),
        ("067170", "09:14:23"): ("오텍 1차", "A_시초", -0.59),
        ("036930", "09:20:36"): ("주성 2차", "A_시초", 3.10),
        ("036930", "09:27:50"): ("주성 3차", "A_시초", 7.59),
        ("036930", "09:35:15"): ("주성 4차", "B_정점직후", -1.20),
        ("253840", "09:42:52"): ("수젠텍 1차", "C_횡보고점", 0.38),
        ("036930", "09:44:45"): ("주성 5차", "B_정점직후", -2.40),
        ("253840", "09:48:56"): ("수젠텍 2차", "C_횡보고점", 0.25),
        ("067170", "09:53:50"): ("오텍 2차", "B_정점직후", -2.22),
        ("253840", "10:04:47"): ("수젠텍 3차", "C_횡보고점", 0.12),
        ("012330", "10:10:05"): ("현대모비스", "C_횡보고점", 0.36),
    }

    rows: list[dict] = []
    for _, b in buys.iterrows():
        code = str(b["code"]); buy_ts = b["ts"]
        key = (code, buy_ts.strftime("%H:%M:%S"))
        if key not in classifications:
            continue
        label, cat, pnl = classifications[key]

        group = tl[tl["code"] == code].sort_values("ts").reset_index(drop=True)
        if group.empty:
            continue
        # buy_ts 까지 cumulative max price = intraday_high
        before_buy = group[group["ts"] <= buy_ts]
        intraday_high = before_buy["price"].max() if not before_buy.empty else 0
        # daily return at buy_ts (from closest tick)
        closest_tick = before_buy.iloc[-1] if not before_buy.empty else None
        if closest_tick is None:
            continue
        daily_return = closest_tick.get("daily_return", float("nan"))
        if abs(daily_return) > 200:
            daily_return = float("nan")

        # 윈도우 [-30s, +5s] max(buy_score) tick — 사용자가 본 가능성 가장 높은 시점
        lo = buy_ts - timedelta(seconds=30)
        hi = buy_ts + timedelta(seconds=5)
        win = group[(group["ts"] >= lo) & (group["ts"] <= hi)]
        if win.empty:
            continue
        max_idx = win["buy_score"].idxmax()
        max_tick = win.loc[max_idx]
        max_tick_price = max_tick["price"]
        max_tick_ts = max_tick["ts"]
        max_intraday_high = group[group["ts"] <= max_tick_ts]["price"].max()

        # 1분봉
        bars = build_1min_bars(group[group["ts"] <= buy_ts])

        # variant 별 페널티
        for name, cutoff, penalty_func in VARIANTS:
            base = float(max_tick["buy_score"])
            if penalty_func is None:
                signals = {}
                penalty = 0.0
            else:
                signals = compute_at_tick_signals_with_intraday(
                    bars, max_intraday_high, max_tick_ts, max_tick_price, daily_return
                )
                penalty = penalty_func(signals)
            adjusted = base + penalty
            allowed = adjusted >= cutoff
            rows.append({
                "buy_ts": buy_ts.strftime("%H:%M:%S"),
                "code": code, "name": label, "category": cat, "actual_pnl": pnl,
                "variant": name,
                "base_score": base, "penalty": penalty, "adjusted": adjusted, "cutoff": cutoff,
                "allowed": allowed,
                "intraday_high": max_intraday_high,
                "dist_from_high": signals.get("dist_from_high_pct"),
                "daily_return": signals.get("daily_return"),
                "recent_5m": signals.get("recent_5m_change_pct"),
                "bb_pos": signals.get("bb_position_pct"),
            })

    df = pd.DataFrame(rows)
    out = Path("data/backtest/user_trades_filter_v2.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"[saved] {out} ({len(df)} rows)\n")

    # variant 별 차단 효과
    print("=== variant 별 사용자 매매 (15건) 차단 효과 ===")
    summary = df.groupby("variant").agg(
        n=("allowed", "count"),
        n_allowed=("allowed", "sum"),
        n_blocked=("allowed", lambda x: (~x).sum()),
        avg_penalty=("penalty", "mean"),
    ).round(2)
    summary["block_pct"] = (summary["n_blocked"] / summary["n"] * 100).round(1)
    print(summary.to_string())

    # 카테고리별 차단
    print("\n=== variant × category 별 차단 ===")
    cat_summary = df.groupby(["variant", "category"]).agg(
        n=("allowed", "count"),
        n_blocked=("allowed", lambda x: (~x).sum()),
    )
    cat_summary["block_pct"] = (cat_summary["n_blocked"] / cat_summary["n"] * 100).round(1)
    print(cat_summary.to_string())

    # 사용자 매매 결과 검증
    print("\n=== variant 별 차단 매매의 실제 손익 ===")
    for name, cutoff, _ in VARIANTS:
        sub = df[df["variant"] == name]
        blocked = sub[~sub["allowed"]]
        allowed = sub[sub["allowed"]]
        n_b, n_a = len(blocked), len(allowed)
        avg_b = blocked["actual_pnl"].mean() if n_b else 0.0
        avg_a = allowed["actual_pnl"].mean() if n_a else 0.0
        total_all = sub["actual_pnl"].sum()
        total_allowed = allowed["actual_pnl"].sum()
        print(
            f"  {name:<22} 차단={n_b:>2} (평균 {avg_b:+.2f}%) "
            f"허용={n_a:>2} (평균 {avg_a:+.2f}%) | "
            f"전체 누적 {total_all:+.2f}% → 차단 후 {total_allowed:+.2f}%"
        )

    # 차단된 매매 상세
    print("\n=== 어떤 매매가 차단됐나 (variant 별) ===")
    for name, cutoff, _ in VARIANTS:
        sub = df[(df["variant"] == name) & (~df["allowed"])]
        if sub.empty:
            print(f"\n--- {name}: 차단 0건 ---")
            continue
        print(f"\n--- {name}: 차단 {len(sub)}건 ---")
        print(sub[["buy_ts", "name", "category", "actual_pnl",
                   "base_score", "penalty", "adjusted",
                   "dist_from_high", "daily_return"]].to_string(index=False))

    return 0


if __name__ == "__main__":
    sys.exit(main())
