"""기존 시스템 STRONG 진입 vs 매매법별 진입 — 진입 지점 품질 비교.

벤치마크 = 사용자 손매매가 아니라 **현재 시스템이 STRONG 띄운 시점**(tick_log buy_grade).
사용자 매매는 시그널 누락/손 느림 노이즈라 비교 부적합 (사용자 정정 2026-05-25).

진입 품질 척도 (청산 무관):
  - race: 진입 후 30분 내 +2% 먼저 닿나(up) / -2% 먼저(down) / 둘 다 X(neither)
  - MFE/MAE: 최대 상승/하락 폭
핵심 질문: chase(고점·연장) 진입을 걸러내면 진입 품질이 좋아지나?

사용:
    python -m scripts.analyze_system_entries
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from scripts.analyze_setup_labels import (
    DATES, classify, entry_features,
)

EPISODE_GAP_MIN = 10     # 같은 종목 STRONG 재진입 최소 간격(분)
RACE_UP = 2.0            # +2% 먼저 닿으면 좋은 진입
RACE_DOWN = -2.0         # -2% 먼저 닿으면 나쁜 진입
FWD_MIN = 30


def strong_episodes(tl: pd.DataFrame) -> pd.DataFrame:
    """STRONG onset 을 종목별 10분 간격으로 dedup → 구별되는 진입 기회."""
    s = tl.sort_values(["code", "ts"]).copy()
    s["is_strong"] = s["buy_grade"] == "STRONG"
    s["prev"] = s.groupby("code")["is_strong"].shift(1)
    s["prev"] = s["prev"].astype("boolean").fillna(False)
    onsets = s[s["is_strong"] & ~s["prev"]]
    rows = []
    last_ts: dict = {}
    for _, r in onsets.iterrows():
        c = r["code"]
        if c in last_ts and (r["ts"] - last_ts[c]).total_seconds() < EPISODE_GAP_MIN * 60:
            continue
        last_ts[c] = r["ts"]
        rows.append(r)
    return pd.DataFrame(rows)


def race(group: pd.DataFrame, buy_ts, buy_price: float) -> dict:
    fwd = group[(group["ts"] > buy_ts)
                & (group["ts"] <= buy_ts + pd.Timedelta(minutes=FWD_MIN))]
    if fwd.empty:
        return {"race": "no_fwd", "mfe": np.nan, "mae": np.nan}
    up_t = down_t = None
    for _, r in fwd.iterrows():
        pnl = (r["price"] - buy_price) / buy_price * 100
        if up_t is None and pnl >= RACE_UP:
            up_t = r["ts"]
        if down_t is None and pnl <= RACE_DOWN:
            down_t = r["ts"]
        if up_t or down_t:
            break
    if up_t and not down_t:
        res = "up"
    elif down_t and not up_t:
        res = "down"
    elif up_t and down_t:
        res = "up" if up_t <= down_t else "down"
    else:
        res = "neither"
    hi = (fwd["price"].max() - buy_price) / buy_price * 100
    lo = (fwd["price"].min() - buy_price) / buy_price * 100
    return {"race": res, "mfe": hi, "mae": lo}


STOP_PCT = -2.0
TRAIL_ARM_PCT = 1.0
TRAIL_GIVEBACK_PCT = 1.5
MA5_BREAK_PCT = -1.0


def current_exit(group, buy_ts, buy_price):
    """현재 시스템 = 사용자 baseline 룰: E 시그널 하나라도 OR -2%. (주로 VP<100 즉발)"""
    fwd = group[(group["ts"] > buy_ts) & (group["ts"] <= buy_ts + pd.Timedelta(minutes=FWD_MIN))]
    if fwd.empty:
        return np.nan, np.nan, "no_fwd"
    for _, r in fwd.iterrows():
        h = (r["ts"] - buy_ts).total_seconds()
        pnl = (r["price"] - buy_price) / buy_price * 100
        if pnl <= STOP_PCT:
            return h, STOP_PCT, "stop_-2%"
        if pd.notna(r.get("vp")) and r["vp"] < 100:
            return h, pnl, "E1_vp<100"
        if r.get("divergence_bearish"):
            return h, pnl, "E2_bearish_div"
        if r.get("trigger_e3_vol_drain"):
            return h, pnl, "E3_vol_drain"
        if r.get("trigger_e4_bearish_candle"):
            return h, pnl, "E4_bearish_candle"
    last = fwd.iloc[-1]
    return (last["ts"] - buy_ts).total_seconds(), (last["price"] - buy_price) / buy_price * 100, "eow"


def matched_exit(group, buy_ts, buy_price, label, intraday_high):
    """매매법별 청산 (VP 단발 무시). 돌파=추세/트레일링, 눌림=목표/지지."""
    fwd = group[(group["ts"] > buy_ts) & (group["ts"] <= buy_ts + pd.Timedelta(minutes=FWD_MIN))]
    if fwd.empty:
        return np.nan, np.nan, "no_fwd"
    peak = 0.0
    for _, r in fwd.iterrows():
        h = (r["ts"] - buy_ts).total_seconds()
        px = r["price"]
        pnl = (px - buy_price) / buy_price * 100
        peak = max(peak, pnl)
        if pnl <= STOP_PCT:
            return h, STOP_PCT, "stop_-2%"
        if peak >= TRAIL_ARM_PCT and pnl <= peak - TRAIL_GIVEBACK_PCT:
            return h, pnl, "trailing"
        if label == "pullback":
            if intraday_high and px >= intraday_high:
                return h, pnl, "target_prevhigh"
            if pd.notna(r.get("price_vs_ma5_pct")) and r["price_vs_ma5_pct"] < MA5_BREAK_PCT:
                return h, pnl, "ma5_break"
        elif label == "breakout":
            if pd.notna(r.get("vp_5ma")) and r["vp_5ma"] < 100:
                return h, pnl, "vp5ma_dead"
            if r.get("trigger_e4_bearish_candle"):
                return h, pnl, "bearish_candle"
    last = fwd.iloc[-1]
    return (last["ts"] - buy_ts).total_seconds(), (last["price"] - buy_price) / buy_price * 100, "eow"


def main() -> int:
    import sys
    dates = sys.argv[1:] if len(sys.argv) > 1 else DATES
    tag = "" if dates == DATES else "_" + "_".join(x[5:] for x in dates)
    rows = []
    for d in dates:
        tl = pd.read_parquet(f"data/tick_logs/{d}.parquet")
        tl["ts"] = pd.to_datetime(tl["ts"], utc=True).dt.tz_convert("Asia/Seoul").dt.tz_localize(None)
        tl = tl[(tl["ts"].dt.hour >= 9) & (tl["ts"].dt.hour < 16)]
        eps = strong_episodes(tl)
        for _, e in eps.iterrows():
            code = e["code"]
            buy_ts, buy_px = e["ts"], float(e["price"])
            grp = tl[tl["code"] == code].sort_values("ts")
            f = entry_features(grp, buy_ts, buy_px)
            label = classify(f)
            rc = race(grp, buy_ts, buy_px)
            ih = f.get("intraday_high", np.nan)
            cur_h, cur_pnl, cur_r = current_exit(grp, buy_ts, buy_px)
            mat_h, mat_pnl, mat_r = matched_exit(grp, buy_ts, buy_px, label, ih)
            rows.append({
                "date": d[5:], "time": buy_ts.strftime("%H:%M"), "name": e["name"],
                "setup": label, "buy_score": e.get("buy_score"),
                "dist_high": f.get("dist_high"), "recent_5m": f.get("recent_5m"),
                "consec_bull": f.get("consec_bull"),
                "race": rc["race"], "mfe": rc["mfe"], "mae": rc["mae"],
                "cur_hold_s": cur_h, "cur_pnl": cur_pnl, "cur_reason": cur_r,
                "mat_hold_s": mat_h, "mat_pnl": mat_pnl, "mat_reason": mat_r,
            })

    df = pd.DataFrame(rows)
    out = Path(f"data/backtest/system_entries{tag}.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    valid = df[df["race"] != "no_fwd"].copy()

    def race_stats(sub: pd.DataFrame) -> str:
        n = len(sub)
        if n == 0:
            return "n=0"
        up = (sub["race"] == "up").mean() * 100
        dn = (sub["race"] == "down").mean() * 100
        ne = (sub["race"] == "neither").mean() * 100
        return (f"n={n:>4}  +2%먼저 {up:5.1f}%  -2%먼저 {dn:5.1f}%  무승부 {ne:5.1f}%  "
                f"| MFE {sub['mfe'].mean():+5.2f}  MAE {sub['mae'].mean():+5.2f}  "
                f"기대 {(sub['mfe'].mean()+sub['mae'].mean())/2:+5.2f}")

    print(f"\n[saved] {out}  (STRONG 진입 {len(df)}건, forward 유효 {len(valid)}건)\n")

    print("=" * 100)
    print("1) 기존 시스템 STRONG 진입의 셋업 분포 — 시스템이 어디서 사라고 하나")
    print("=" * 100)
    for setup in ["breakout", "chase", "pullback", "none", "no_data"]:
        sub = valid[valid["setup"] == setup]
        print(f"  {setup:<10} {race_stats(sub)}")

    print("\n" + "=" * 100)
    print("2) 진입 품질 비교: 현재 시스템(전체 STRONG) vs 매매법 필터")
    print("=" * 100)
    print(f"  [A] 현재: 전체 STRONG          {race_stats(valid)}")
    no_chase = valid[~valid["setup"].isin(["chase", "none", "no_data"])]
    print(f"  [B] chase/none 제외           {race_stats(no_chase)}")
    pb = valid[valid["setup"] == "pullback"]
    print(f"  [C] 눌림만                    {race_stats(pb)}")
    bo = valid[valid["setup"] == "breakout"]
    print(f"  [D] 돌파(미연장)만            {race_stats(bo)}")

    print("\n" + "=" * 100)
    print("3) buy_score 구간별 진입 품질 (점수 높을수록 좋은가? = 정점 함정 검증)")
    print("=" * 100)
    valid["score_bin"] = pd.cut(valid["buy_score"].astype(float),
                                [-99, 5, 7, 9, 99],
                                labels=["WATCH~5", "5~7", "7~9", "9+"])
    for b in ["WATCH~5", "5~7", "7~9", "9+"]:
        sub = valid[valid["score_bin"] == b]
        print(f"  score {b:<8} {race_stats(sub)}")

    print("\n" + "=" * 100)
    print("4) 보유 기간 변화: 현재 청산(시그널 하나라도) vs 매매법별 청산")
    print("=" * 100)
    v = valid.dropna(subset=["cur_hold_s", "mat_hold_s"]).copy()

    def hold_stats(sub, col_h, col_p):
        med = sub[col_h].median()
        mean = sub[col_h].mean()
        pnl = sub[col_p].mean()
        return (f"보유 중앙값 {med:6.0f}초 ({med/60:4.1f}분)  평균 {mean:6.0f}초  "
                f"| PnL 평균 {pnl:+5.2f}%")

    print(f"  [현재 청산]   {hold_stats(v, 'cur_hold_s', 'cur_pnl')}")
    print(f"  [매매법 청산] {hold_stats(v, 'mat_hold_s', 'mat_pnl')}")
    print(f"  → 보유 중앙값 {v['cur_hold_s'].median():.0f}초 → {v['mat_hold_s'].median():.0f}초 "
          f"({v['mat_hold_s'].median()/max(v['cur_hold_s'].median(),1):.1f}배)")

    print("\n  -- 셋업별 --")
    for setup in ["breakout", "pullback", "chase"]:
        sub = v[v["setup"] == setup]
        if len(sub) < 5:
            continue
        print(f"  {setup:<9} 현재: {hold_stats(sub, 'cur_hold_s', 'cur_pnl')}")
        print(f"  {'':<9} 분리: {hold_stats(sub, 'mat_hold_s', 'mat_pnl')}")

    print("\n  -- 현재 청산 사유 분포 (왜 빨리 나가나) --")
    print("   ", v["cur_reason"].value_counts().to_dict())
    print("  -- 10초 이내 청산 비율 --")
    print(f"    현재: {(v['cur_hold_s']<=10).mean()*100:.1f}%   매매법: {(v['mat_hold_s']<=10).mean()*100:.1f}%")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
