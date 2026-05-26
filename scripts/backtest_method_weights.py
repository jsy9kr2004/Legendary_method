"""매매법별 가중치 점수(돌파/눌림) — 정의신호 AND 게이트로 N 축소 검증.

사용자 의도(2026-05-25): N 축소를 컷 상향/리더한정/하루1진입 말고, **매매법별 지표
가산구조 자체를 조정**해서. → 각 매매법의 *정의 신호*를 필수(AND 게이트)로 만들고
부수 신호만 가산. 그러면 "부분 신호 짬뽕 합산 STRONG"이 사라져 N 이 준다(컷 불변).

통설 근거: 돌파 = 레벨돌파 + 거래량 *둘 다* 필수 / 눌림 = 1차급등 + 5MA지지 +
거래량 재유입(바운스 트리거) *셋 다* 필수.

⚠ 3일 in-sample, 가중치 통설 기반 첫 추정치. ritual상 운영 적용 X — 방향성.
사용: python -m scripts.backtest_method_weights
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DATES = ["2026-05-20", "2026-05-21", "2026-05-22"]
FWD_MIN = 30
GAP_MIN = 10
COST = 0.4
STOP = -2.0


def load_scored() -> pd.DataFrame:
    frames = []
    for d in DATES:
        df = pd.read_parquet(f"data/tick_logs/{d}.parquet")
        df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert("Asia/Seoul").dt.tz_localize(None)
        df = df[(df["ts"].dt.hour >= 9) & (df["ts"].dt.hour < 16)].copy()
        df["date"] = d
        frames.append(df)
    df = pd.concat(frames).sort_values(["date", "code", "ts"]).reset_index(drop=True)
    df["ih"] = df.groupby(["date", "code"])["price"].cummax()
    df["dist_high"] = np.where(df["ih"] > 0, (df["price"] - df["ih"]) / df["ih"] * 100, np.nan)

    def c(name):
        return pd.to_numeric(df[name], errors="coerce") if name in df.columns else pd.Series(np.nan, index=df.index)

    va5, va1, vp = c("vol_accel_5m"), c("vol_accel_1m"), c("vp")
    uw, lw, ma5 = c("candle_upper_wick_ratio"), c("candle_lower_wick_ratio"), c("price_vs_ma5_pct")
    dr = c("daily_return"); dr = dr.where(dr.abs() < 200)
    volr = c("volume_ratio_vs_prev_day")
    bull = df["candle_type"].eq("bullish") if "candle_type" in df.columns else pd.Series(False, index=df.index)
    divbull = df["divergence_bullish"].fillna(False) if "divergence_bullish" in df.columns else pd.Series(False, index=df.index)

    # additive (v2)
    df["score_bo"] = (
        4.0 * df["dist_high"].between(-2.0, -0.3).astype(float)
        + 3.0 * (va5 >= 1.2).astype(float)
        + 2.0 * (bull & (uw.fillna(1) < 0.3)).astype(float)
        + 1.5 * (vp >= 110).astype(float)
        + 1.0 * (volr >= 1.5).astype(float)
        - 3.0 * (dr >= 20).astype(float)
        - 2.0 * (df["dist_high"] >= -0.3).astype(float)
    )
    df["score_pb"] = (dr >= 5).astype(float) * (
        4.0 * ma5.between(-1.5, 1.0).astype(float)
        + 3.0 * (lw.fillna(0) >= 0.3).astype(float)
        + 2.0 * (va1 >= 1.0).astype(float)
        + 1.5 * divbull.astype(float)
        + 1.0 * (df["dist_high"] <= -1.0).astype(float)
        + 1.0 * (vp >= 100).astype(float)
    )
    # gated: 정의신호 AND 필수, 부수만 가산
    bo_core = df["dist_high"].between(-2.0, -0.3) & (va5 >= 1.5)
    df["score_bo_g"] = np.where(bo_core,
        4.0 + 2.0 * (bull & (uw.fillna(1) < 0.3)).astype(float) + 1.5 * (vp >= 110).astype(float)
        + 1.0 * (volr >= 1.5).astype(float) - 3.0 * (dr >= 20).astype(float), 0.0)
    pb_core = (dr >= 5) & ma5.between(-1.5, 0.5) & (va1 >= 1.5)
    df["score_pb_g"] = np.where(pb_core,
        4.0 + 3.0 * (lw.fillna(0) >= 0.5).astype(float) + 1.5 * divbull.astype(float)
        + 1.0 * (df["dist_high"] <= -1.0).astype(float) + 1.0 * (vp >= 100).astype(float), 0.0)

    df["va5"], df["dr"], df["ma5_"], df["vp_"] = va5, dr, ma5, vp
    df["vp5_"] = c("vp_5ma")
    df["div_bear"] = df["divergence_bearish"].fillna(False) if "divergence_bearish" in df.columns else False
    df["e3"] = df["trigger_e3_vol_drain"].fillna(False) if "trigger_e3_vol_drain" in df.columns else False
    df["e4"] = df["trigger_e4_bearish_candle"].fillna(False) if "trigger_e4_bearish_candle" in df.columns else False
    return df


def max_concurrent(times, win_min=10):
    if not times:
        return 0
    ev = []
    for t in times:
        ev.append((t, 1)); ev.append((t + pd.Timedelta(minutes=win_min), -1))
    ev.sort(key=lambda x: (x[0], x[1]))
    cur = mx = 0
    for _, d in ev:
        cur += d; mx = max(mx, cur)
    return mx


def ex_breakout(price, ep, *_):
    peak = 0.0
    for px in price:
        pnl = (px - ep) / ep * 100
        peak = max(peak, pnl)
        if pnl <= STOP: return STOP
        if px < ep * 0.99: return pnl
        if peak >= 2.0 and pnl <= peak - 2.0: return pnl
    return (price[-1] - ep) / ep * 100


def ex_pullback(price, ep, ih, ma5):
    for i, px in enumerate(price):
        pnl = (px - ep) / ep * 100
        if pnl <= STOP: return STOP
        if ih and px >= ih: return pnl
        if ma5[i] == ma5[i] and ma5[i] < -1.5: return pnl
    return (price[-1] - ep) / ep * 100


def ex_current(price, ep, vp, div, e3, e4):
    for i, px in enumerate(price):
        pnl = (px - ep) / ep * 100
        if pnl <= STOP: return STOP
        if vp[i] == vp[i] and vp[i] < 100: return pnl
        if div[i] or e3[i] or e4[i]: return pnl
    return (price[-1] - ep) / ep * 100


def run(groups, signal, cut, exit_kind):
    rows = []
    for (date, code), g in groups.items():
        ts = g["ts"].to_numpy()
        price = g["price"].to_numpy()
        if signal == "buy_grade":
            hot = (g["buy_grade"].to_numpy() == "STRONG")
        else:
            hot = (g[signal].to_numpy() >= cut)
        onset = hot & ~np.concatenate([[False], hot[:-1]])
        last = None
        idxs = np.flatnonzero(onset)
        for i in idxs:
            t = ts[i]
            if last is not None and (t - last) / np.timedelta64(1, "s") < GAP_MIN * 60:
                continue
            last = t
            m = (ts > t) & (ts <= t + np.timedelta64(FWD_MIN, "m"))
            if not m.any():
                continue
            ep = price[i]
            fp = price[m]
            if exit_kind == "breakout":
                pnl = ex_breakout(fp, ep)
            elif exit_kind == "pullback":
                pnl = ex_pullback(fp, ep, g["ih"].to_numpy()[i], g["ma5_"].to_numpy()[m])
            else:
                pnl = ex_current(fp, ep, g["vp_"].to_numpy()[m], g["div_bear"].to_numpy()[m],
                                 g["e3"].to_numpy()[m], g["e4"].to_numpy()[m])
            rows.append({"date": date, "ts": t, "pnl": pnl,
                         "dist_high": g["dist_high"].to_numpy()[i], "va5": g["va5"].to_numpy()[i]})
    return pd.DataFrame(rows)


def summ(df, label):
    n = len(df)
    if n == 0:
        return f"{label:<26} n=0"
    concur = max((max_concurrent(gg["ts"].tolist()) for _, gg in df.groupby("date")), default=0)
    return (f"{label:<26} n={n:>4} ({n/3:>5.1f}/일, 동시{concur:>2})  승률 {(df.pnl>0).mean()*100:>3.0f}%  "
            f"net {df.pnl.mean()-COST:+5.2f}%  | 고점{np.nanmedian(df.dist_high):+4.1f}%/가속{np.nanmedian(df.va5):4.2f}")


def main() -> int:
    df = load_scored()
    groups = {k: g.reset_index(drop=True) for k, g in df.groupby(["date", "code"], sort=False)}
    print("=" * 120)
    print("N 축소 = 컷 X / 매매법별 *정의신호 AND 게이트*. 전체universe·재진입. (비용0.4%)")
    print("=" * 120)
    print(" ", summ(run(groups, "buy_grade", 0, "current"), "[베이스]현재STRONG"))
    print("-" * 120)
    print(" 돌파 additive vs gated(레벨돌파 AND 거래량 필수):")
    print("  ", summ(run(groups, "score_bo", 7, "breakout"), "additive cut≥7"))
    print("  ", summ(run(groups, "score_bo_g", 4, "breakout"), "gated cut≥4"))
    print("  ", summ(run(groups, "score_bo_g", 6, "breakout"), "gated cut≥6"))
    print("-" * 120)
    print(" 눌림 additive vs gated(급등 AND 5MA지지 AND 거래량재유입 필수):")
    print("  ", summ(run(groups, "score_pb", 6, "pullback"), "additive cut≥6"))
    print("  ", summ(run(groups, "score_pb_g", 4, "pullback"), "gated cut≥4"))
    print("  ", summ(run(groups, "score_pb_g", 7, "pullback"), "gated cut≥7"))
    print("=" * 120)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
