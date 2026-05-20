"""Buy.Score backtest v2 — 정점 회피 시그널 (R14e/f/g/h) 포함.

5/18 ~ 5/20 tick_log parquet 으로 다중 variant 비교. 사용자 룰 (STRONG 매수 +
청산 시그널/-2% 매도) baseline + proposal P1 정점 회피 페널티.

사용:
    python -m scripts.backtest_buy_score
    python -m scripts.backtest_buy_score --variants current,p1_full,p0_p1_combo
    python -m scripts.backtest_buy_score --image-5020   # 차트 4건 매수 시점 비교

caveat:
- 5/18, 5/19 universe 편향 (KIS FID_BLNG_CLS_CODE="0" 버그 시기).
- 청산: -stop% / 등급 강등 / EOD. Exit.E1~E5 트리거 미반영 (별도 시뮬).
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd


# ── 1분봉 helper ────────────────────────────────────────────────────────────


def build_1min_bars(group: pd.DataFrame) -> pd.DataFrame:
    """tick 시계열 → 1분 OHLCV + 분당 거래대금.

    group: 한 종목의 tick rows (ts, price, trading_value).
    return: index=1분봉 시각, columns=[open, high, low, close, value].
    """
    g = group.sort_values("ts").set_index("ts")
    # OHLC
    ohlc = g["price"].resample("1min").agg(["first", "max", "min", "last"])
    ohlc.columns = ["open", "high", "low", "close"]
    # 분당 거래대금 = 누적의 1분봉 last - prev_minute last
    cum_last = g["trading_value"].resample("1min").last()
    minute_value = cum_last.diff().fillna(cum_last)
    bars = ohlc.copy()
    bars["value"] = minute_value
    bars = bars.dropna(subset=["close"])
    return bars


def compute_at_tick_signals(
    bars: pd.DataFrame, tick_ts: pd.Timestamp, tick_price: float,
    intraday_high: float = 0.0, daily_return: float = float("nan"),
) -> dict:
    """주어진 tick 시점에서 정점 회피 시그널 계산.

    R14e: 최근 5분 가격 변화율
    R14f: BB 상한 위치 (20봉 SMA + 2σ)
    R14g: 직전 5분봉 5개 중 양봉 카운트
    R14h: 거래량 정점 + 가격 정체
    R14k: 일중 최고점 거리 (intraday_high 인자 필요)
    R14l: 횡보 정점 (daily_return 인자 필요)
    """
    # bars 의 timezone 통일 — tick_ts 가 timezone-aware 면 bars index 도
    bars_index = bars.index
    # 현재 시각 이전 봉만
    past_bars = bars[bars_index <= tick_ts]
    if len(past_bars) == 0:
        return {
            "recent_5m_change_pct": float("nan"),
            "bb_position_pct": float("nan"),
            "recent_5_bars_bullish_count": 0,
            "volume_peak_with_price_flat": False,
        }

    # R14e: 5분 전 close vs 현재 price
    five_min_ago = tick_ts - pd.Timedelta(minutes=5)
    bars_5m_ago = past_bars[past_bars.index <= five_min_ago]
    if len(bars_5m_ago) > 0:
        price_5m_ago = bars_5m_ago["close"].iloc[-1]
        if price_5m_ago > 0:
            r14e = (tick_price - price_5m_ago) / price_5m_ago * 100
        else:
            r14e = float("nan")
    else:
        r14e = float("nan")

    # R14f: BB position
    if len(past_bars) >= 20:
        last20_close = past_bars["close"].iloc[-20:]
        sma20 = last20_close.mean()
        std20 = last20_close.std()
        bb_upper = sma20 + 2 * std20
        if bb_upper > 0:
            r14f = (tick_price - bb_upper) / bb_upper * 100
        else:
            r14f = float("nan")
    else:
        r14f = float("nan")

    # R14g: 직전 5분봉 5개 중 양봉 카운트
    if len(past_bars) >= 5:
        last5 = past_bars.iloc[-5:]
        r14g = int((last5["close"] > last5["open"]).sum())
    else:
        r14g = 0

    # R14h: 거래량 정점
    if len(past_bars) >= 6:
        recent_5_value = past_bars["value"].iloc[-5:].sum()
        prev_5_value = past_bars["value"].iloc[-10:-5].sum()
        if prev_5_value > 0:
            volume_ratio = recent_5_value / prev_5_value
        else:
            volume_ratio = 1.0
        # 가격 정체: 최근 5분 변화율 < 0.3%
        price_5_change_pct = r14e if r14e == r14e else 0.0
        r14h = (volume_ratio > 2.0 and price_5_change_pct < 0.3)
    else:
        r14h = False

    # R14k: dist from intraday high
    if intraday_high > 0:
        dist_from_high = (tick_price - intraday_high) / intraday_high * 100
    else:
        dist_from_high = float("nan")

    return {
        "recent_5m_change_pct": r14e,
        "bb_position_pct": r14f,
        "recent_5_bars_bullish_count": r14g,
        "volume_peak_with_price_flat": r14h,
        "dist_from_high_pct": dist_from_high,
        "daily_return": daily_return,
    }


# ── 페널티 함수 ──────────────────────────────────────────────────────────────


def _r14e_penalty(signals: dict) -> float:
    """최근 5분 +10% → -2, +5% → -1."""
    v = signals.get("recent_5m_change_pct")
    if v is None or v != v:
        return 0.0
    if v >= 10.0:
        return -2.0
    if v >= 5.0:
        return -1.0
    return 0.0


def _r14f_penalty(signals: dict) -> float:
    """BB 상한 도달 (≥0) → -1, 돌파 (≥+1%) → -2."""
    v = signals.get("bb_position_pct")
    if v is None or v != v:
        return 0.0
    if v >= 1.0:
        return -2.0
    if v >= 0.0:
        return -1.0
    return 0.0


def _r14g_penalty(signals: dict) -> float:
    """직전 5봉 중 양봉 4+ → -1."""
    n = signals.get("recent_5_bars_bullish_count", 0)
    if n >= 4:
        return -1.0
    return 0.0


def _r14h_penalty(signals: dict) -> float:
    """거래량 정점 + 가격 정체 → -1.5."""
    if signals.get("volume_peak_with_price_flat"):
        return -1.5
    return 0.0


def _p1_full_penalty(signals: dict) -> float:
    return _r14e_penalty(signals) + _r14f_penalty(signals) + _r14g_penalty(signals) + _r14h_penalty(signals)


def _r14k_penalty(signals: dict) -> float:
    """일중 최고점 거리 (정점 5% 이내 페널티)."""
    v = signals.get("dist_from_high_pct")
    if v is None or v != v:
        return 0.0
    if v >= -2.0:
        return -2.0
    if v >= -5.0:
        return -1.0
    return 0.0


def _r14l_penalty(signals: dict) -> float:
    """횡보 정점 — daily_return >= 15% AND dist >= -5% → -1.5."""
    dr = signals.get("daily_return")
    if dr is None or dr != dr or abs(dr) > 200:
        return 0.0
    dist = signals.get("dist_from_high_pct")
    if dist is None or dist != dist:
        return 0.0
    if dr >= 15 and dist >= -5:
        return -1.5
    return 0.0


def _kl_only_penalty(signals: dict) -> float:
    return _r14k_penalty(signals) + _r14l_penalty(signals)


def _p1_plus_kl_penalty(signals: dict) -> float:
    return _p1_full_penalty(signals) + _kl_only_penalty(signals)


# ── Variant 정의 ────────────────────────────────────────────────────────────


@dataclass
class Variant:
    """Backtest variant."""

    name: str
    cutoff: float = 5.0
    penalty_func: Callable[[dict], float] | None = None  # signals dict → penalty score


VARIANTS: list[Variant] = [
    # === Baseline ===
    Variant("current", cutoff=5.0),
    # === proposal P0 Q5 (cutoff 하향) ===
    Variant("q5_lower_4", cutoff=4.0),
    # === H6 (cutoff 상향) ===
    Variant("q5_inv_6", cutoff=6.0),
    # === proposal P1 페널티 개별 ===
    Variant("p1_r14e", cutoff=5.0, penalty_func=_r14e_penalty),       # 최근 5분 폭등
    Variant("p1_r14f", cutoff=5.0, penalty_func=_r14f_penalty),       # BB 상한
    Variant("p1_r14g", cutoff=5.0, penalty_func=_r14g_penalty),       # 연속 양봉
    Variant("p1_r14h", cutoff=5.0, penalty_func=_r14h_penalty),       # 거래량 정점
    # === proposal P1 결합 ===
    Variant("p1_full", cutoff=5.0, penalty_func=_p1_full_penalty),
    # === P0 + P1 결합 (사용자 의도 정합 — cutoff 낮춰 폭등 초기 + 정점 페널티) ===
    Variant("p0_p1_combo", cutoff=4.0, penalty_func=_p1_full_penalty),
    # === ★ 추가 — R14k/R14l (일중 최고점 거리, 횡보 정점) ===
    # 사용자 의도 정합 — backtest_user_trades.py 에서 7건 차단 확인
    Variant("kl_only", cutoff=5.0, penalty_func=_kl_only_penalty),
    Variant("p1_plus_kl", cutoff=5.0, penalty_func=_p1_plus_kl_penalty),
    Variant("p0_p1_plus_kl_combo", cutoff=4.0, penalty_func=_p1_plus_kl_penalty),
]


# ── 진입 / 청산 시뮬 ────────────────────────────────────────────────────────


def adjusted_score(
    row: pd.Series, variant: Variant, bars: pd.DataFrame | None = None,
    intraday_high: float = 0.0,
) -> tuple[float, dict]:
    """variant 페널티 적용 점수 + 시그널 dict."""
    base = row.get("buy_score")
    if base is None or base != base:
        return float("-inf"), {}
    score = float(base)
    signals: dict = {}
    if variant.penalty_func is not None and bars is not None:
        dr = row.get("daily_return", float("nan"))
        if abs(dr) > 200:
            dr = float("nan")
        signals = compute_at_tick_signals(
            bars, row["ts"], row["price"],
            intraday_high=intraday_high, daily_return=dr,
        )
        score += variant.penalty_func(signals)
    return score, signals


def find_first_entry(
    group: pd.DataFrame, variant: Variant, bars: pd.DataFrame | None,
    price_cummax: pd.Series | None = None,
) -> tuple[int | None, dict]:
    """variant cutoff 이상 첫 진입 시점 + 진입 시점 시그널."""
    for i, row in group.iterrows():
        intraday_high = float(price_cummax.iloc[i]) if price_cummax is not None else 0.0
        score, signals = adjusted_score(row, variant, bars, intraday_high)
        if score >= variant.cutoff:
            return i, signals
    return None, {}


def find_exit(
    group: pd.DataFrame,
    entry_idx: int,
    entry_price: float,
    stop_loss_pct: float,
    variant: Variant,
    bars: pd.DataFrame | None,
    price_cummax: pd.Series | None = None,
) -> tuple[int, str]:
    """청산 시점 + 사유."""
    after = group.iloc[entry_idx + 1 :]
    stop_price = entry_price * (1 + stop_loss_pct / 100)
    for i, row in after.iterrows():
        price = row.get("price")
        if price is None or price != price:
            continue
        if price <= stop_price:
            return i, "stop_loss"
        intraday_high = float(price_cummax.iloc[i]) if price_cummax is not None else 0.0
        score, _ = adjusted_score(row, variant, bars, intraday_high)
        if score < variant.cutoff:
            return i, "grade_demote"
    last = group.iloc[-1]
    return last.name, "eod"


# ── Backtest 메인 ──────────────────────────────────────────────────────────


def backtest_one_day(
    df: pd.DataFrame, variant: Variant, stop_loss_pct: float = -2.0
) -> pd.DataFrame:
    """하루치 tick_log + variant + stop → trade 리스트."""
    results: list[dict] = []
    needs_bars = variant.penalty_func is not None
    for code, group in df.groupby("code"):
        group = group.sort_values("ts").reset_index(drop=True)
        # 1분봉 — penalty_func 가 있을 때만 만듦 (비용 ↓)
        bars = build_1min_bars(group) if needs_bars else None
        # cumulative max price = intraday_high
        price_cummax = group["price"].cummax() if needs_bars else None
        entry_idx, entry_signals = find_first_entry(group, variant, bars, price_cummax)
        if entry_idx is None:
            continue
        entry_row = group.iloc[entry_idx]
        entry_price = entry_row.get("price")
        if entry_price is None or entry_price != entry_price:
            continue

        # 매수 시점 메트릭
        intraday_high = entry_row.get("intraday_high", 0) or 0
        if intraday_high > 0:
            dist_from_high = (entry_price - intraday_high) / intraday_high * 100
        else:
            dist_from_high = float("nan")
        daily_return = entry_row.get("daily_return", float("nan"))
        # daily_return 비정상 값 (시초 잡음 - 200% 이상) 필터
        if daily_return != daily_return or abs(daily_return) > 200:
            daily_return = float("nan")

        exit_idx, exit_reason = find_exit(
            group, entry_idx, entry_price, stop_loss_pct, variant, bars, price_cummax
        )
        exit_price = group.iloc[exit_idx]["price"]
        pnl = (exit_price - entry_price) / entry_price * 100
        results.append({
            "variant": variant.name,
            "code": str(code),
            "name": entry_row.get("name", ""),
            "entry_ts": entry_row["ts"],
            "entry_price": float(entry_price),
            "entry_score": entry_row.get("buy_score"),
            "entry_daily_return": daily_return,
            "entry_dist_from_high": dist_from_high,
            "entry_recent_5m_change": entry_signals.get("recent_5m_change_pct", float("nan")),
            "entry_bb_position": entry_signals.get("bb_position_pct", float("nan")),
            "entry_bullish_count_5bars": entry_signals.get("recent_5_bars_bullish_count", 0),
            "exit_ts": group.iloc[exit_idx]["ts"],
            "exit_price": float(exit_price),
            "exit_reason": exit_reason,
            "pnl_pct": pnl,
            "holding_sec": (group.iloc[exit_idx]["ts"] - entry_row["ts"]).total_seconds(),
        })
    return pd.DataFrame(results)


# ── image/0520 4건 매수 시점 비교 ────────────────────────────────────────────


IMAGE_5020_CODES = {
    "253840": "수젠텍",
    "036930": "주성엔지니어링",
    "012330": "현대모비스",
    "067170": "오텍",
}


def simulate_image_5020(stop_loss_pct: float = -2.0) -> pd.DataFrame:
    """5/20 데이터로 image 차트 4건의 매수 시점 비교."""
    path = Path("data/tick_logs/2026-05-20.parquet")
    df = pd.read_parquet(path)
    df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert("Asia/Seoul")
    df = df[df["code"].isin(IMAGE_5020_CODES.keys())].copy()

    rows = []
    for variant in VARIANTS:
        for code, name in IMAGE_5020_CODES.items():
            group = df[df["code"] == code].sort_values("ts").reset_index(drop=True)
            if group.empty:
                continue
            bars = build_1min_bars(group) if variant.penalty_func else None
            price_cummax = group["price"].cummax() if variant.penalty_func else None
            entry_idx, signals = find_first_entry(group, variant, bars, price_cummax)
            if entry_idx is None:
                rows.append({
                    "variant": variant.name,
                    "code": code,
                    "name": name,
                    "entry_ts": "N/A",
                    "entry_price": None,
                    "entry_daily_return": None,
                    "entry_dist_from_high": None,
                    "entry_recent_5m_change": None,
                    "entry_bb_position": None,
                    "pnl_pct": None,
                    "exit_reason": "no_entry",
                })
                continue
            entry_row = group.iloc[entry_idx]
            entry_price = float(entry_row["price"])
            intraday_high = entry_row.get("intraday_high", 0) or 0
            dist_from_high = (entry_price - intraday_high) / intraday_high * 100 if intraday_high > 0 else float("nan")
            exit_idx, exit_reason = find_exit(
                group, entry_idx, entry_price, stop_loss_pct, variant, bars, price_cummax
            )
            exit_price = float(group.iloc[exit_idx]["price"])
            pnl = (exit_price - entry_price) / exit_price * 0 + (exit_price - entry_price) / entry_price * 100
            rows.append({
                "variant": variant.name,
                "code": code,
                "name": name,
                "entry_ts": entry_row["ts"].strftime("%H:%M:%S"),
                "entry_price": entry_price,
                "entry_score": entry_row.get("buy_score"),
                "entry_daily_return": entry_row.get("daily_return"),
                "entry_dist_from_high": dist_from_high,
                "entry_recent_5m_change": signals.get("recent_5m_change_pct"),
                "entry_bb_position": signals.get("bb_position_pct"),
                "entry_bullish_5": signals.get("recent_5_bars_bullish_count"),
                "exit_ts": group.iloc[exit_idx]["ts"].strftime("%H:%M:%S"),
                "exit_price": exit_price,
                "exit_reason": exit_reason,
                "pnl_pct": pnl,
            })
    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", type=str, default=None)
    ap.add_argument("--dates", type=str, default=None)
    ap.add_argument("--stops", type=str, default="-2.0")
    ap.add_argument("--out", type=Path, default=Path("data/backtest/buy_score_v2.csv"))
    ap.add_argument("--image-5020", action="store_true", help="image/0520 차트 4건 매수 시점 비교")
    args = ap.parse_args()

    if args.image_5020:
        df = simulate_image_5020()
        out = Path("data/backtest/buy_score_v2_image_5020.csv")
        out.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(out, index=False)
        print(f"\n[saved] {out}")
        # 종목별 + variant 별 표시
        for code, name in IMAGE_5020_CODES.items():
            print(f"\n=== {name} ({code}) ===")
            sub = df[df["code"] == code]
            print(sub[["variant", "entry_ts", "entry_price", "entry_score",
                       "entry_recent_5m_change", "entry_bb_position", "entry_bullish_5",
                       "entry_dist_from_high", "pnl_pct", "exit_reason"]].to_string(index=False))
        return 0

    variants = VARIANTS
    if args.variants:
        wanted = set(args.variants.split(","))
        variants = [v for v in VARIANTS if v.name in wanted]
    dates = args.dates.split(",") if args.dates else ["2026-05-18", "2026-05-19", "2026-05-20"]
    stops = [float(s) for s in args.stops.split(",")]

    all_results: list[pd.DataFrame] = []
    for date_str in dates:
        path = Path(f"data/tick_logs/{date_str}.parquet")
        if not path.exists():
            print(f"[skip] {path}")
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
                avg_entry_recent_5m = trades["entry_recent_5m_change"].mean()
                avg_entry_bb = trades["entry_bb_position"].mean()
                print(
                    f"  {variant.name:<20} stop={stop} N={len(trades):>3} "
                    f"avg={trades['pnl_pct'].mean():+.2f}% win={int((trades['pnl_pct']>0).mean()*100):>2}% "
                    f"entry_5m={avg_entry_recent_5m:+.1f}% entry_bb={avg_entry_bb:+.2f}%"
                )

    if not all_results:
        return 1
    df_all = pd.concat(all_results, ignore_index=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    df_all.to_csv(args.out, index=False)
    print(f"\n[saved] {args.out} ({len(df_all)} trades)")

    # 3일 합산
    print("\n=== variant × stop (3일 합산) — 사용자 의도 정합 메트릭 포함 ===")
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
            avg_entry_5m=("entry_recent_5m_change", "mean"),
            avg_entry_bb=("entry_bb_position", "mean"),
            avg_dist_high=("entry_dist_from_high", "mean"),
            avg_hold_min=("holding_sec", lambda x: x.mean() / 60),
        ).round(2)
        print(s.to_string())

    return 0


if __name__ == "__main__":
    sys.exit(main())
