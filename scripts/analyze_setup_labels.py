"""셋업 라벨링 (돌파 / 눌림 / 추격 / 기타) + 매매법별 청산 what-if.

docs/trading-method-separation-discussion.md §9-5 / scalping-method-taxonomy.md §5.

목적:
  1. 사용자 실제 매매(5/20~5/22)를 tick_log 로 셋업 분류 → "어중간한 추격" 가설 검증
  2. 매매법별 청산(눌림=느림/직전고점, 돌파=빠름/모멘텀사망)을 적용했을 때
     실손익이 어떻게 달라지는지 what-if 비교

⚠ 분류 임계값은 통설 기반 첫 추정치(§0.5). 본 스크립트는 측정 도구이지 확정 룰 아님.
   N(거래) 작음 → 방향성만. CLAUDE.md ritual: 1건 표본 즉시 변경 X.

사용:
    python -m scripts.analyze_setup_labels
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

# ─────────────────────────────────────────────────────────────
# 분류 임계 (통설 §0.5 기반 첫 추정치 — 튜닝 대상)
# ─────────────────────────────────────────────────────────────
GIJUNBONG_VALUE = 2.0e9          # 기준봉 = 1분봉 거래대금 20억 (i-whale, 확정)
NEAR_HIGH_PCT = -1.0             # 일중 고점 이 % 이내면 "고점 근처"
EXTENDED_5M_PCT = 3.0            # 최근 5분 이 %↑ 상승이면 "연장됨"(추격 위험)
EXTENDED_CONSEC_BULL = 4         # 직전 5봉 중 양봉 이 개수↑면 "연장됨"
PULLBACK_DIST_LO = -6.0          # 눌림 진입 고점거리 하한
PULLBACK_DIST_HI = -1.0          # 눌림 진입 고점거리 상한
STOP_PCT = -2.0                  # 사용자 baseline 손절
DATES = ["2026-05-20", "2026-05-21", "2026-05-22"]
FWD_MIN = 30                     # 진입 후 forward 관찰 분


def to_kst_naive(s: pd.Series) -> pd.Series:
    dt = pd.to_datetime(s, format="ISO8601")
    if getattr(dt.dt, "tz", None) is not None:
        dt = dt.dt.tz_convert("Asia/Seoul").dt.tz_localize(None)
    return dt


def load_trades(d: str) -> pd.DataFrame:
    t = pd.read_parquet(f"data/trades/{d}.parquet")
    t["ts"] = to_kst_naive(t["ts"])
    t = t[(t["ts"].dt.hour >= 9) & (t["ts"].dt.hour < 16)].dropna(subset=["price"])
    t = t.sort_values("ts").reset_index(drop=True)
    keep, last = [], {}
    for i, r in t.iterrows():
        k = (r["code"], r["action"], r["price"])
        if k in last and (r["ts"] - last[k]).total_seconds() < 60:
            continue
        last[k] = r["ts"]
        keep.append(i)
    return t.loc[keep].reset_index(drop=True)


def pair_fifo(trades: pd.DataFrame) -> list[dict]:
    """code 별 FIFO buy→sell 페어링. 미청산 buy 는 sell=None."""
    open_buys: dict[str, list] = {}
    pairs: list[dict] = []
    for _, r in trades.iterrows():
        c = r["code"]
        if r["action"] == "buy":
            open_buys.setdefault(c, []).append(r)
        elif r["action"] == "sell" and open_buys.get(c):
            b = open_buys[c].pop(0)
            pairs.append({"buy": b, "sell": r})
    for c, lst in open_buys.items():
        for b in lst:
            pairs.append({"buy": b, "sell": None})
    return pairs


def build_1min_bars(group: pd.DataFrame) -> pd.DataFrame:
    g = group.sort_values("ts").set_index("ts")
    ohlc = g["price"].resample("1min").agg(["first", "max", "min", "last"])
    ohlc.columns = ["open", "high", "low", "close"]
    cum_last = g["trading_value"].resample("1min").last()
    minute_value = cum_last.diff().fillna(cum_last)
    bars = ohlc.copy()
    bars["value"] = minute_value
    return bars.dropna(subset=["close"])


def entry_features(group: pd.DataFrame, buy_ts, buy_price: float) -> dict:
    before = group[group["ts"] <= buy_ts]
    if before.empty:
        return {}
    closest = before.iloc[-1]
    intraday_high = before["price"].max()
    dist_high = (buy_price - intraday_high) / intraday_high * 100 if intraday_high else np.nan
    dr = closest.get("daily_return", np.nan)
    if pd.notna(dr) and abs(dr) > 200:
        dr = np.nan

    bars = build_1min_bars(before)
    # 최근 5분 변화율
    five_ago = buy_ts - pd.Timedelta(minutes=5)
    b5 = bars[bars.index <= five_ago]
    r5 = (buy_price - b5["close"].iloc[-1]) / b5["close"].iloc[-1] * 100 if len(b5) else np.nan
    # 직전 5봉 양봉 수
    consec = int((bars.iloc[-5:]["close"] > bars.iloc[-5:]["open"]).sum()) if len(bars) >= 5 else 0
    # 기준봉: 20억+ 봉 존재 + 마지막 기준봉 이후 경과(분) + 그 봉 고점
    gij = bars[bars["value"] >= GIJUNBONG_VALUE]
    if len(gij):
        last_gij_ts = gij.index[-1]
        gij_ago_min = (buy_ts - last_gij_ts).total_seconds() / 60
        gij_high = before[before["ts"] <= last_gij_ts]["price"].max()
    else:
        gij_ago_min, gij_high = np.nan, np.nan

    return {
        "intraday_high": intraday_high,
        "dist_high": dist_high,
        "daily_return": dr,
        "recent_5m": r5,
        "consec_bull": consec,
        "gij_ago_min": gij_ago_min,
        "has_gijunbong": len(gij) > 0,
        "vp": closest.get("vp", np.nan),
        "vp_5ma": closest.get("vp_5ma", np.nan),
        "ma5_pct": closest.get("price_vs_ma5_pct", np.nan),
        "vwap_pct": closest.get("price_vs_vwap_pct", np.nan),
        "candle": closest.get("candle_type", ""),
    }


def classify(f: dict) -> str:
    """셋업 분류 (보수적). 통설 §2 구조 기반."""
    if not f:
        return "no_data"
    dist = f.get("dist_high", np.nan)
    r5 = f.get("recent_5m", np.nan)
    consec = f.get("consec_bull", 0)
    near_high = pd.notna(dist) and dist >= NEAR_HIGH_PCT
    extended = (pd.notna(r5) and r5 >= EXTENDED_5M_PCT) or consec >= EXTENDED_CONSEC_BULL

    # 추격: 고점 근처 + 이미 연장됨 (양봉양봉양봉매수 패턴)
    if near_high and extended:
        return "chase"
    # 돌파: 고점/신고가 근처인데 아직 연장 안 됨 (이른 돌파)
    if near_high and not extended:
        return "breakout"
    # 눌림: 1차 급등(기준봉) 후 고점에서 적당히 눌린 자리 + 반등 기미
    if (f.get("has_gijunbong")
            and pd.notna(dist) and PULLBACK_DIST_LO <= dist <= PULLBACK_DIST_HI):
        return "pullback"
    return "none"


def forward_path(group: pd.DataFrame, buy_ts, buy_price: float) -> dict:
    fwd = group[(group["ts"] > buy_ts)
                & (group["ts"] <= buy_ts + pd.Timedelta(minutes=FWD_MIN))]
    if fwd.empty:
        return {"mfe": np.nan, "mae": np.nan, "t_mfe_s": np.nan}
    hi = fwd["price"].max()
    lo = fwd["price"].min()
    t_mfe = (fwd.loc[fwd["price"].idxmax(), "ts"] - buy_ts).total_seconds()
    return {
        "mfe": (hi - buy_price) / buy_price * 100,
        "mae": (lo - buy_price) / buy_price * 100,
        "t_mfe_s": t_mfe,
    }


TRAIL_ARM_PCT = 1.0       # 트레일링 발동 (이만큼 오른 뒤부터)
TRAIL_GIVEBACK_PCT = 1.5  # 정점 대비 이만큼 되돌리면 청산
MA5_BREAK_PCT = -1.0      # 눌림 지지(ma5) 진짜 붕괴 임계


def sim_exit(group, buy_ts, buy_price, label, intraday_high) -> dict:
    """매매법별 청산 시뮬 (통설 §2 기반).

    공통: -2% 하드 손절.
    돌파: 추세 태움 → 모멘텀 사망(vp_5ma<100) / 윗꼬리음봉 / 트레일링.  (VP 단발 X)
    눌림: 직전 고점(목표) 익절 / ma5 진짜 붕괴 / 트레일링.  (VP 단발 X)
    """
    fwd = group[(group["ts"] > buy_ts)
                & (group["ts"] <= buy_ts + pd.Timedelta(minutes=FWD_MIN))]
    if fwd.empty:
        return {"sim_pnl": np.nan, "sim_reason": "no_fwd"}

    target = intraday_high  # 눌림 목표 = 직전 고점
    peak_pnl = 0.0
    for _, r in fwd.iterrows():
        px = r["price"]
        pnl = (px - buy_price) / buy_price * 100
        peak_pnl = max(peak_pnl, pnl)
        # 공통 하드 손절
        if pnl <= STOP_PCT:
            return {"sim_pnl": STOP_PCT, "sim_reason": "stop_-2%"}
        # 트레일링 (양 매매법 공통 — 정점 일부 환수, MFE 추종)
        if peak_pnl >= TRAIL_ARM_PCT and pnl <= peak_pnl - TRAIL_GIVEBACK_PCT:
            return {"sim_pnl": pnl, "sim_reason": "trailing"}
        if label == "pullback":
            if px >= target:
                return {"sim_pnl": pnl, "sim_reason": "target_prevhigh"}
            if pd.notna(r.get("price_vs_ma5_pct")) and r["price_vs_ma5_pct"] < MA5_BREAK_PCT:
                return {"sim_pnl": pnl, "sim_reason": "ma5_break"}
        elif label == "breakout":
            if pd.notna(r.get("vp_5ma")) and r["vp_5ma"] < 100:
                return {"sim_pnl": pnl, "sim_reason": "vp5ma_dead"}
            if r.get("trigger_e4_bearish_candle"):
                return {"sim_pnl": pnl, "sim_reason": "bearish_candle"}
    last_pnl = (fwd["price"].iloc[-1] - buy_price) / buy_price * 100
    return {"sim_pnl": last_pnl, "sim_reason": f"eow_{FWD_MIN}m"}


def main() -> int:
    all_rows = []
    for d in DATES:
        tl = pd.read_parquet(f"data/tick_logs/{d}.parquet")
        tl["ts"] = pd.to_datetime(tl["ts"], utc=True).dt.tz_convert("Asia/Seoul").dt.tz_localize(None)
        tl = tl[(tl["ts"].dt.hour >= 9) & (tl["ts"].dt.hour < 16)]
        trades = load_trades(d)
        for pr in pair_fifo(trades):
            b = pr["buy"]
            s = pr["sell"]
            code, name = str(b["code"]), b["name"]
            buy_ts, buy_px = b["ts"], float(b["price"])
            grp = tl[tl["code"] == code].sort_values("ts")
            if grp.empty:
                continue
            f = entry_features(grp, buy_ts, buy_px)
            label = classify(f)
            fp = forward_path(grp, buy_ts, buy_px)
            ih = f.get("intraday_high", np.nan)
            sim = sim_exit(grp, buy_ts, buy_px, label, ih)
            realized = ((float(s["price"]) - buy_px) / buy_px * 100) if s is not None else np.nan
            hold_s = (s["ts"] - buy_ts).total_seconds() if s is not None else np.nan
            all_rows.append({
                "date": d[5:], "time": buy_ts.strftime("%H:%M:%S"), "name": name,
                "setup": label,
                "dist_high": f.get("dist_high"), "recent_5m": f.get("recent_5m"),
                "consec_bull": f.get("consec_bull"), "gij_ago": f.get("gij_ago_min"),
                "vp": f.get("vp"), "ma5%": f.get("ma5_pct"),
                "hold_s": hold_s, "realized": realized,
                "mfe": fp["mfe"], "mae": fp["mae"], "t_mfe_s": fp["t_mfe_s"],
                "sim_pnl": sim["sim_pnl"], "sim_reason": sim["sim_reason"],
            })

    df = pd.DataFrame(all_rows)
    out = Path("data/backtest/setup_labels.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)

    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", None)
    r2 = df.round(2)

    print(f"\n[saved] {out}  (n={len(df)} 매매)\n")
    print("=" * 110)
    print("1) 매매별 셋업 라벨 + 진입 특성 + forward")
    print("=" * 110)
    print(r2[["date", "time", "name", "setup", "dist_high", "recent_5m",
              "consec_bull", "gij_ago", "vp", "hold_s", "realized", "mfe", "mae", "t_mfe_s"]].to_string(index=False))

    print("\n" + "=" * 110)
    print("2) 셋업 분포 (어중간한 추격 가설 검증)")
    print("=" * 110)
    dist = df.groupby("setup").agg(
        n=("setup", "count"),
        avg_dist_high=("dist_high", "mean"),
        avg_recent5m=("recent_5m", "mean"),
        avg_realized=("realized", "mean"),
        avg_mfe=("mfe", "mean"),
        avg_hold_s=("hold_s", "mean"),
    ).round(2)
    print(dist.to_string())

    print("\n" + "=" * 110)
    print("3) 청산 what-if: 실제 vs 매매법별 청산 (라벨 있는 매매만)")
    print("=" * 110)
    lab = df[df["setup"].isin(["breakout", "pullback"])].copy()
    if len(lab):
        cmp = lab[["date", "time", "name", "setup", "realized", "sim_pnl", "sim_reason", "mfe"]].round(2)
        print(cmp.to_string(index=False))
        print(f"\n  라벨 매매 실제 누적:   {lab['realized'].sum():+.2f}%  (평균 {lab['realized'].mean():+.2f}%)")
        print(f"  라벨 매매 시뮬 누적:   {lab['sim_pnl'].sum():+.2f}%  (평균 {lab['sim_pnl'].mean():+.2f}%)")
    else:
        print("  라벨(돌파/눌림) 매매 없음")

    print("\n" + "=" * 110)
    print("4) 전체 실제 손익 (참고)")
    print("=" * 110)
    closed = df.dropna(subset=["realized"])
    print(f"  청산 완료 {len(closed)}건  누적 {closed['realized'].sum():+.2f}%  "
          f"평균 {closed['realized'].mean():+.2f}%  승률 {(closed['realized']>0).mean()*100:.0f}%  "
          f"평균보유 {closed['hold_s'].mean():.0f}초")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
