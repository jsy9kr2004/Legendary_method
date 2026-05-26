"""청산 개선 + 눌림 목표 재설계 — *큰 표본*(additive 수백 건)으로 튜닝.

사용자 지적(2026-05-25): 청산/목표 튜닝은 줄인 N(게이트 23~26건) 말고 *기존 수백 N*으로
해야 통계적으로 믿을 만함. N축소(게이트)는 운영 레버, 청산튜닝은 별개로 큰 표본에서.

돌파 청산 변형: 추세 winner 를 더 태우는 방향 (현재 35% 승률 = winner 못 태움).
눌림 목표 재설계: 직전고점(너무 멈, MFE+2.2%) → 고정/절반/트레일 비교.

⚠ 3일 in-sample. ritual상 운영 적용 X.
사용: python -m scripts.tune_exits
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.backtest_method_weights import load_scored, max_concurrent

FWD_MIN = 30
GAP_MIN = 10
COST = 0.4
STOP = -2.0


# ── 돌파 청산 변형 (price, ep, ih, ma5[], vp5[]) ──
def bo_exit(level_lost, arm, give, ride_vp):
    def fn(price, ep, ih, ma5, vp5):
        peak = 0.0
        for i, px in enumerate(price):
            pnl = (px - ep) / ep * 100
            peak = max(peak, pnl)
            if pnl <= STOP:
                return STOP
            if level_lost and px < ep * 0.99:
                return pnl
            if ride_vp:
                if pnl > 0 and vp5[i] == vp5[i] and vp5[i] < 100:
                    return pnl
            elif peak >= arm and pnl <= peak - give:
                return pnl
        return (price[-1] - ep) / ep * 100
    return fn


# ── 눌림 청산 변형 ──
def pb_exit(mode, tgt, arm, give, ma5break=-1.5):
    def fn(price, ep, ih, ma5, vp5):
        peak = 0.0
        for i, px in enumerate(price):
            pnl = (px - ep) / ep * 100
            peak = max(peak, pnl)
            if pnl <= STOP:
                return STOP
            if mode == "prevhigh" and ih and px >= ih:
                return pnl
            if mode == "fixed" and pnl >= tgt:
                return pnl
            if mode == "halfway" and ih:
                if px >= ep + (ih - ep) * 0.5:
                    return pnl
            if mode == "trail" and peak >= arm and pnl <= peak - give:
                return pnl
            if ma5[i] == ma5[i] and ma5[i] < ma5break:
                return pnl
        return (price[-1] - ep) / ep * 100
    return fn


def run(groups, signal, cut, exit_fn):
    rows = []
    for (date, code), g in groups.items():
        ts = g["ts"].to_numpy()
        price = g["price"].to_numpy()
        hot = (g["buy_grade"].to_numpy() == "STRONG") if signal == "buy_grade" else (g[signal].to_numpy() >= cut)
        onset = hot & ~np.concatenate([[False], hot[:-1]])
        last = None
        for i in np.flatnonzero(onset):
            t = ts[i]
            if last is not None and (t - last) / np.timedelta64(1, "s") < GAP_MIN * 60:
                continue
            last = t
            m = (ts > t) & (ts <= t + np.timedelta64(FWD_MIN, "m"))
            if not m.any():
                continue
            pnl = exit_fn(price[m], price[i], g["ih"].to_numpy()[i],
                          g["ma5_"].to_numpy()[m], g["vp5_"].to_numpy()[m])
            rows.append({"date": date, "ts": t, "pnl": pnl})
    return pd.DataFrame(rows)


def line(df, label):
    n = len(df)
    if n == 0:
        return f"  {label:<30} n=0"
    win = (df.pnl > 0).mean() * 100
    avg = df.pnl.mean()
    return (f"  {label:<30} n={n:>4}  승률 {win:>3.0f}%  평균 {avg:+5.2f}%  "
            f"net {avg-COST:+5.2f}%  일net {(avg-COST)*n/3:+6.1f}%")


def main() -> int:
    df = load_scored()
    groups = {k: g.reset_index(drop=True) for k, g in df.groupby(["date", "code"], sort=False)}

    print("=" * 96)
    print("돌파 청산 튜닝 (큰 표본 = additive score_bo cut≥6)  — winner 더 태우기")
    print("=" * 96)
    bo_variants = [
        ("현재(레벨컷+트레일2/2)", bo_exit(True, 2, 2, False)),
        ("레벨컷제거+트레일2/2", bo_exit(False, 2, 2, False)),
        ("레벨컷제거+트레일3/3(느슨)", bo_exit(False, 3, 3, False)),
        ("레벨컷제거+VP死까지 태움", bo_exit(False, 0, 0, True)),
    ]
    for lbl, fn in bo_variants:
        print(line(run(groups, "score_bo", 6, fn), lbl))

    print("=" * 96)
    print("눌림 목표 재설계 (큰 표본 = additive score_pb cut≥5)")
    print("=" * 96)
    pb_variants = [
        ("현재(직전고점 목표)", pb_exit("prevhigh", 0, 0, 0)),
        ("고정 +2%", pb_exit("fixed", 2.0, 0, 0)),
        ("고정 +3%", pb_exit("fixed", 3.0, 0, 0)),
        ("직전고점까지 절반", pb_exit("halfway", 0, 0, 0)),
        ("트레일 1.5/1.5", pb_exit("trail", 0, 1.5, 1.5)),
    ]
    for lbl, fn in pb_variants:
        print(line(run(groups, "score_pb", 5, fn), lbl))
    print("=" * 96)
    print("참고: 베이스(현재STRONG+현재청산) net -0.29% / 비용 0.4%. net>0 이라야 매매 의미.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
