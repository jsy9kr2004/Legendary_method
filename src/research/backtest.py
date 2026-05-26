"""config 기반 채점 + 진입/청산 시뮬 + 메트릭 (clean day 단위).

docs §10.5 의 검증 로직을 StrategyConfig 로 파라미터화. walk-forward 가 이걸 호출.
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from src.research.strategy_config import StrategyConfig

CLEAN_SINCE = date(2026, 5, 20)  # 거래량/거래대금 fix 이후 (5/19 이전 universe 버그)


def data_dir() -> Path:
    return Path(os.getenv("DATA_DIR", "data"))


def clean_days() -> list[str]:
    """검증 가능한 거래일 (post-fix tick_log 존재). 새 날 들어오면 자동 포함."""
    tl = data_dir() / "tick_logs"
    days = []
    for p in sorted(tl.glob("*.parquet")):
        try:
            d = date.fromisoformat(p.stem)
        except ValueError:
            continue
        if d >= CLEAN_SINCE:
            days.append(p.stem)
    return days


def _num(df: pd.DataFrame, name: str) -> pd.Series:
    return pd.to_numeric(df[name], errors="coerce") if name in df.columns else pd.Series(np.nan, index=df.index)


def load_day(day: str) -> pd.DataFrame:
    """하루 tick_log → 파생 신호 컬럼 포함 (09:00~16:00, 종목별 정렬)."""
    df = pd.read_parquet(data_dir() / "tick_logs" / f"{day}.parquet")
    df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert("Asia/Seoul").dt.tz_localize(None)
    df = df[(df["ts"].dt.hour >= 9) & (df["ts"].dt.hour < 16)].copy()
    df = df.sort_values(["code", "ts"]).reset_index(drop=True)
    df["ih"] = df.groupby("code")["price"].cummax()
    df["dist_high"] = np.where(df["ih"] > 0, (df["price"] - df["ih"]) / df["ih"] * 100, np.nan)
    df["va5"] = _num(df, "vol_accel_5m")
    df["va1"] = _num(df, "vol_accel_1m")
    df["vp_"] = _num(df, "vp")
    df["vp5_"] = _num(df, "vp_5ma")
    df["uw"] = _num(df, "candle_upper_wick_ratio")
    df["lw"] = _num(df, "candle_lower_wick_ratio")
    df["ma5_"] = _num(df, "price_vs_ma5_pct")
    dr = _num(df, "daily_return")
    df["dr"] = dr.where(dr.abs() < 200)
    df["volr"] = _num(df, "volume_ratio_vs_prev_day")
    df["bull"] = df["candle_type"].eq("bullish") if "candle_type" in df.columns else False
    df["divbull"] = df["divergence_bullish"].fillna(False).astype(bool) if "divergence_bullish" in df.columns else False
    df["div_bear"] = df["divergence_bearish"].fillna(False).astype(bool) if "divergence_bearish" in df.columns else False
    df["e3"] = df["trigger_e3_vol_drain"].fillna(False).astype(bool) if "trigger_e3_vol_drain" in df.columns else False
    df["e4"] = df["trigger_e4_bearish_candle"].fillna(False).astype(bool) if "trigger_e4_bearish_candle" in df.columns else False
    # 국면(breadth) 피처 병합 (P2-7) — 진입 시점 시장 폭
    breadth = compute_breadth(df)
    if not breadth.empty:
        df = pd.merge_asof(df.sort_values("ts"), breadth.sort_values("bts"),
                           left_on="ts", right_on="bts", direction="backward")
        df = df.sort_values(["code", "ts"]).reset_index(drop=True)
    else:
        df["breadth_up_frac"] = float("nan")
    return df


def compute_breadth(df: pd.DataFrame) -> pd.DataFrame:
    """분 단위 시장 폭: 그 시각 모니터 종목 중 상승(daily_return>0) 비율 + +5%↑ 수.

    국면 proxy — 강세장에서만 돌파가 통한다(docs §10.2-7) 를 정량화. 단일 종목이 아니라
    *그 순간 전체* 의 상태라 cross-sectional 집계.
    """
    g = df[["ts", "code", "dr"]].dropna(subset=["dr"])
    if g.empty:
        return pd.DataFrame(columns=["bts", "breadth_up_frac", "breadth_n_up5"])
    g = g.assign(minute=g["ts"].dt.floor("1min"))
    last = g.sort_values("ts").groupby(["minute", "code"], as_index=False)["dr"].last()
    agg = last.groupby("minute").agg(
        n=("code", "size"),
        n_up=("dr", lambda x: int((x > 0).sum())),
        n_up5=("dr", lambda x: int((x >= 5).sum())),
    )
    agg["breadth_up_frac"] = agg["n_up"] / agg["n"]
    return (agg.reset_index().rename(columns={"minute": "bts", "n_up5": "breadth_n_up5"})
            [["bts", "breadth_up_frac", "breadth_n_up5"]])


def score(df: pd.DataFrame, cfg: StrategyConfig) -> np.ndarray:
    """config 기반 진입 점수 (게이트 정의신호 AND 필수, 부수 가산, 국면 게이트)."""
    if cfg.method == "current":
        raw = np.where(df["buy_grade"].to_numpy() == "STRONG", cfg.cut, 0.0)
    elif cfg.method == "breakout":
        core = df["dist_high"].between(cfg.bo_level_lo, cfg.bo_level_hi) & (df["va5"] >= cfg.bo_vol_accel_min)
        raw = np.where(core,
            4.0
            + cfg.bo_w_candle * (df["bull"] & (df["uw"].fillna(1) < 0.3)).to_numpy()
            + cfg.bo_w_vp * (df["vp_"] >= 110).to_numpy()
            + cfg.bo_w_volratio * (df["volr"] >= 1.5).to_numpy()
            - cfg.bo_blowoff_pen * (df["dr"] >= cfg.bo_blowoff_dr).to_numpy(), 0.0)
    elif cfg.method == "pullback":
        core = (df["dr"] >= cfg.pb_surge_min) & df["ma5_"].between(cfg.pb_ma5_lo, cfg.pb_ma5_hi) & (df["va1"] >= cfg.pb_reentry_min)
        raw = np.where(core,
            4.0
            + cfg.pb_w_hammer * (df["lw"].fillna(0) >= cfg.pb_hammer_min).to_numpy()
            + cfg.pb_w_divbull * df["divbull"].to_numpy()
            + cfg.pb_w_pulled * (df["dist_high"] <= -1.0).to_numpy()
            + cfg.pb_w_vp * (df["vp_"] >= 100).to_numpy(), 0.0)
    else:
        raise ValueError(f"unknown method {cfg.method}")

    # 국면(breadth) 게이트 (P2-7): 시장 폭 부족하면 진입 차단.
    if cfg.regime_breadth_min > 0 and "breadth_up_frac" in df.columns:
        ok = (df["breadth_up_frac"].fillna(0).to_numpy() >= cfg.regime_breadth_min)
        raw = np.where(ok, raw, 0.0)
    return raw


def _exit(cfg: StrategyConfig, price, ep, ih, ma5, vp5, vp, div, e3, e4) -> float:
    stop = cfg.stop_pct
    if cfg.exit_kind == "breakout":
        peak = 0.0
        for i, px in enumerate(price):
            pnl = (px - ep) / ep * 100
            peak = max(peak, pnl)
            if pnl <= stop: return stop
            if cfg.bo_level_lost_cut and px < ep * 0.99: return pnl
            if cfg.bo_ride_vp_death:
                if pnl > 0 and vp5[i] == vp5[i] and vp5[i] < 100: return pnl
            elif peak >= cfg.bo_trail_arm and pnl <= peak - cfg.bo_trail_give: return pnl
        return (price[-1] - ep) / ep * 100
    if cfg.exit_kind == "pullback":
        peak = 0.0
        for i, px in enumerate(price):
            pnl = (px - ep) / ep * 100
            peak = max(peak, pnl)
            if pnl <= stop: return stop
            if cfg.pb_target_mode == "prevhigh" and ih and px >= ih: return pnl
            if cfg.pb_target_mode == "fixed" and pnl >= cfg.pb_target_pct: return pnl
            if cfg.pb_target_mode == "halfway" and ih and px >= ep + (ih - ep) * 0.5: return pnl
            if cfg.pb_target_mode == "trail" and peak >= 1.5 and pnl <= peak - 1.5: return pnl
            if ma5[i] == ma5[i] and ma5[i] < cfg.pb_ma5_break: return pnl
        return (price[-1] - ep) / ep * 100
    # current = 단발 시그널 하나라도 OR -2%
    for i, px in enumerate(price):
        pnl = (px - ep) / ep * 100
        if pnl <= stop: return stop
        if vp[i] == vp[i] and vp[i] < 100: return pnl
        if div[i] or e3[i] or e4[i]: return pnl
    return (price[-1] - ep) / ep * 100


def simulate(df: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
    """하루 진입(onset, gap dedup) → 매매법별 청산 → 실현손익(비용 차감 전 pnl)."""
    sc = score(df, cfg)
    rows = []
    for code, idx in df.groupby("code").groups.items():
        g = df.loc[idx]
        ts = g["ts"].to_numpy()
        price = g["price"].to_numpy()
        s = sc[g.index.to_numpy()]
        hot = s >= cfg.cut
        onset = hot & ~np.concatenate([[False], hot[:-1]])
        last = None
        for j in np.flatnonzero(onset):
            t = ts[j]
            if last is not None and (t - last) / np.timedelta64(1, "s") < cfg.gap_min * 60:
                continue
            last = t
            m = (ts > t) & (ts <= t + np.timedelta64(cfg.fwd_min, "m"))
            if not m.any():
                continue
            pnl = _exit(cfg, price[m], price[j], g["ih"].to_numpy()[j],
                        g["ma5_"].to_numpy()[m], g["vp5_"].to_numpy()[m], g["vp_"].to_numpy()[m],
                        g["div_bear"].to_numpy()[m], g["e3"].to_numpy()[m], g["e4"].to_numpy()[m])
            rows.append({"code": code, "ts": t, "pnl": pnl})
    return pd.DataFrame(rows)


def metrics(trades: pd.DataFrame, cfg: StrategyConfig, n_days: int = 1) -> dict:
    n = len(trades)
    if n == 0:
        return {"n": 0, "per_day": 0.0, "win": float("nan"), "avg": float("nan"), "net": float("nan")}
    avg = float(trades["pnl"].mean())
    return {
        "n": n, "per_day": round(n / max(n_days, 1), 1),
        "win": round((trades["pnl"] > 0).mean() * 100, 1),
        "avg": round(avg, 3),
        "net": round(avg - cfg.cost_pct, 3),  # 비용 차감 net
    }


def evaluate(days: list[str], cfg: StrategyConfig) -> dict:
    """여러 날에 cfg 적용 → 합산 메트릭."""
    all_trades = []
    for d in days:
        all_trades.append(simulate(load_day(d), cfg))
    trades = pd.concat(all_trades) if all_trades else pd.DataFrame(columns=["pnl"])
    return metrics(trades, cfg, n_days=len(days))
