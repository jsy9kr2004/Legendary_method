"""STRONG 단저/단고 재정의 최종 비교 — 기존(oversold+score) vs 신규(강망치/강슈팅).

문서/코드 반영 전 검증 (2026-05-29 토론):
  - train/val 분리 (train=5/18~22, val=5/27~29) out-of-sample.
  - 같은 ZigZag swing 진입 후보(floor 0.5, 청산=다음 pivot)에 두 STRONG 판별 적용.
  - 단저(매수)=저점→다음고점 net / 단고(매도)=고점→다음저점 회피하락 net.
  - 종목별 net 분산 → 운전수(per-stock) 필요성.
  surface universe(is_auto/manual/holding) 한정. 비용 지정가 0.2% 차감.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from src.scalping.bars import build_bars
from src.scalping.signals.mean_reversion import is_oversold, is_overbought
from src.scalping.signals.weighted_score import (
    add_score_features, compute_score_buy, compute_score_sell,
    SCORE_BUY_STRONG, SCORE_SELL_STRONG,
)

TRAIN = {"2026-05-18", "2026-05-19", "2026-05-20", "2026-05-21", "2026-05-22"}
VAL = {"2026-05-27", "2026-05-28", "2026-05-29"}
COST = 0.2
HAM = 0.6  # 강망치/강슈팅 꼬리 임계
MKT_OPEN = pd.Timestamp("09:00").time()
MKT_CLOSE = pd.Timestamp("15:30").time()


def pivots(close: np.ndarray, floor: float) -> list[tuple]:
    trend = 0; ext = close[0]; ext_i = 0; seq = []
    for i in range(1, len(close)):
        if trend <= 0:
            if close[i] < ext: ext = close[i]; ext_i = i
            elif (close[i] - ext) / ext * 100 >= floor:
                seq.append(("L", ext_i, close[i])); trend = 1; ext = close[i]; ext_i = i
        if trend >= 0:
            if close[i] > ext: ext = close[i]; ext_i = i
            elif (close[i] - ext) / ext * 100 <= -floor:
                seq.append(("H", ext_i, close[i])); trend = -1; ext = close[i]; ext_i = i
    return seq


def collect() -> pd.DataFrame:
    rows = []
    for f in sorted(Path("data/tick_logs").glob("2026-*.parquet")):
        date = f.stem
        split = "train" if date in TRAIN else ("val" if date in VAL else None)
        if split is None:
            continue
        df = pd.read_parquet(f); df["ts"] = pd.to_datetime(df["ts"])
        mkt = df[(df["ts"].dt.time >= MKT_OPEN) & (df["ts"].dt.time <= MKT_CLOSE)]
        for code, sub in mkt.groupby("code"):
            sub = sub.sort_values("ts")
            if "is_auto" not in sub or not bool(sub[["is_auto", "is_manual", "is_holding"]].any().any()):
                continue
            b = build_bars(sub.set_index("ts"))
            if b is None or len(b) < 25 or "lower_wick_pct" not in b.columns:
                continue
            add_score_features(b)
            c = b["close"].to_numpy(); lw = b["lower_wick_pct"].values; uw = b["upper_wick_pct"].values
            zs = b["zscore"].values; vs = b["vol_spike"].values
            seq = pivots(c, 0.5)
            prev_px = None
            for k in range(len(seq) - 1):
                t, ei, cpx = seq[k]; t2, ei2, cpx2 = seq[k + 1]
                row = b.iloc[ei]
                if t == "L" and t2 == "H":  # 매수 swing
                    net = (cpx2 / cpx - 1) * 100 - COST
                    old = bool(is_oversold(row)) and compute_score_buy(row, str(code)) >= SCORE_BUY_STRONG
                    old_g = bool(is_oversold(row)) and compute_score_buy(row, None) >= SCORE_BUY_STRONG
                    rise_drop = (cpx / prev_px - 1) * 100 if prev_px else np.nan  # 직전 고점 대비(눌림 깊이)
                    rows.append(dict(split=split, code=str(code), side="buy", net=net,
                        old_strong=old, old_strong_glob=old_g,
                        new_core=(lw[ei] >= HAM),
                        new_full=(lw[ei] >= HAM and (zs[ei] < -0.98 if not np.isnan(zs[ei]) else False)),
                        ))
                elif t == "H" and t2 == "L":  # 매도 swing
                    net = (cpx / cpx2 - 1) * 100 - COST  # 회피 하락
                    old = bool(is_overbought(row)) and compute_score_sell(row, str(code)) >= SCORE_SELL_STRONG
                    old_g = bool(is_overbought(row)) and compute_score_sell(row, None) >= SCORE_SELL_STRONG
                    rise = (cpx / prev_px - 1) * 100 if prev_px else np.nan  # 직전 저점 대비 상승(급등?)
                    weak = (zs[ei] < 0.40 if not np.isnan(zs[ei]) else False)  # 추세약함=과매수아님
                    rows.append(dict(split=split, code=str(code), side="sell", net=net,
                        old_strong=old, old_strong_glob=old_g,
                        new_core=(uw[ei] >= HAM),
                        new_full=(uw[ei] >= HAM and weak),
                        ))
                prev_px = cpx
    return pd.DataFrame(rows)


def _stat(g: pd.DataFrame) -> str:
    if len(g) == 0:
        return f"{'0건':>16}"
    return f"{len(g):>4}건 net{g['net'].mean():+.3f}% 승{(g['net']>0).mean()*100:>3.0f}%"


def main() -> None:
    d = collect()
    for side, label in [("buy", "단저(매수: 저점→다음고점)"), ("sell", "단고(매도: 고점→회피하락)")]:
        ds = d[d["side"] == side]
        print(f"\n{'='*86}\n■ {label}  (surface universe, 비용 {COST}% 차감)\n{'='*86}")
        print(f"{'전략':<34}{'TRAIN':>26}{'VAL(out-of-sample)':>26}")
        defs = [
            ("전체 swing (baseline)", ds),
            ("기존 STRONG (per-stock)", ds[ds["old_strong"]]),
            ("기존 STRONG (global)", ds[ds["old_strong_glob"]]),
            (f"신규 코어 (꼬리≥{HAM})", ds[ds["new_core"]]),
            ("신규 코어+보완", ds[ds["new_full"]]),
        ]
        for name, sub in defs:
            tr = sub[sub["split"] == "train"]; va = sub[sub["split"] == "val"]
            print(f"{name:<34}{_stat(tr):>26}{_stat(va):>26}")

    # 종목별 운전수 — 신규 코어 net 의 종목 간 분산
    print(f"\n{'='*86}\n■ 종목별 운전수 가설 — 신규 코어(강망치) 종목별 net (val 포함 전체, n≥4)\n{'='*86}")
    buy = d[(d["side"] == "buy") & (d["new_core"])]
    g = buy.groupby("code")["net"].agg(["count", "mean"]).query("count>=4").sort_values("mean", ascending=False)
    print(f"종목수 {len(g)} / 종목간 net std = {g['mean'].std():.3f}%p (클수록 운전수 효과 큼)")
    print("상위 5:", ", ".join(f"{c}({r['mean']:+.2f}%,n{int(r['count'])})" for c, r in g.head(5).iterrows()))
    print("하위 5:", ", ".join(f"{c}({r['mean']:+.2f}%,n{int(r['count'])})" for c, r in g.tail(5).iterrows()))
    glob_net = buy["net"].mean()
    top_half = g[g["mean"] >= g["mean"].median()].index
    sel_net = buy[buy["code"].isin(top_half)]["net"].mean()
    print(f"글로벌 강망치 net {glob_net:+.3f}% → 종목 상위절반 선별 시 {sel_net:+.3f}% (per-stock 효과)")


if __name__ == "__main__":
    main()
