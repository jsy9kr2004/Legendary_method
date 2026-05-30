"""ZigZag swing 왕복 매매 백테스트 (단저단고 v2 후보 — 2026-05-29 image/3.PNG).

사용자 의도: 추세·과매도 무관하게 분봉 로컬 swing 저점 매수 → 고점 매도 왕복.
현재 단저단고(oversold 게이트) 와 다른 패러다임. data/journal/2026-05-29.md 참조.

두 가지를 같이 측정한다:
  1. look-ahead 이상치 (완벽한 pivot 예지 — 실거래 불가, 상한선).
  2. 실시간 (look-ahead 제거 — 저점 대비 +floor% 반등 confirm 봉에 매수,
     고점 대비 -floor% 하락 confirm 봉에 매도). 실제 진입/청산가 = confirm 봉 종가.

비용 시나리오는 backtest_mean_reversion 과 동일 (왕복 한 매매당 차감):
  시장가 0.4% / 지정가 0.2% / 유동 리더 0.15%.

종목 선별 효과 — 일중 변동성(ATR%) / 일등락(추세 강도) 상·하위로 net 분포 비교.
(강추세+큰 swing 종목만 net 양수라는 가설 검증.)

CLI:
  python -m src.research.backtest_zigzag
  python -m src.research.backtest_zigzag --freq 5min --floors 0.5 1.0
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.scalping.bars import aggregate_bars

COST_SCENARIOS = {"market_0.4%": 0.4, "limit_0.2%": 0.2, "limit_liquid_0.15%": 0.15}
DATA_DIR = Path("data/tick_logs")
OUT_PATH = Path("data/backtest/zigzag.json")

MARKET_OPEN = pd.Timestamp("09:00").time()
MARKET_CLOSE = pd.Timestamp("15:30").time()


def zigzag_trades_realtime(close: np.ndarray, floor_pct: float) -> list[float]:
    """look-ahead 없는 실시간 ZigZag 왕복 매매 수익률 리스트 (%).

    저점 갱신 추적 중 저점 대비 +floor% 반등하면 그 봉 종가에 매수(저점보다 비쌈),
    고점 갱신 추적 중 고점 대비 -floor% 하락하면 그 봉 종가에 매도(고점보다 쌈).
    한 매수~매도 = 한 왕복. 미청산 포지션은 버림 (EOD 강제청산 안 함 — 보수적).
    """
    trades: list[float] = []
    trend = 0  # 0/-: 저점 탐색, +: 고점 탐색
    ext = close[0]
    entry: float | None = None
    for i in range(1, len(close)):
        if trend <= 0:
            if close[i] < ext:
                ext = close[i]
            elif (close[i] - ext) / ext * 100 >= floor_pct:
                if entry is None:
                    entry = close[i]
                trend = 1
                ext = close[i]
        if trend >= 0:
            if close[i] > ext:
                ext = close[i]
            elif (close[i] - ext) / ext * 100 <= -floor_pct:
                if entry is not None:
                    trades.append((close[i] / entry - 1) * 100)
                    entry = None
                trend = -1
                ext = close[i]
    return trades


def zigzag_trades_lookahead(close: np.ndarray, floor_pct: float) -> list[float]:
    """look-ahead ZigZag 이상치 — 확정된 pivot 으로 저점→고점 leg 수익 (실거래 불가)."""
    piv = [0]
    trend = 0
    ext_i = 0
    for i in range(1, len(close)):
        chg = (close[i] - close[ext_i]) / close[ext_i] * 100
        if trend >= 0 and close[i] > close[ext_i]:
            ext_i = i
        elif trend <= 0 and close[i] < close[ext_i]:
            ext_i = i
        if trend >= 0 and chg <= -floor_pct:
            piv.append(ext_i)
            trend = -1
            ext_i = i
        elif trend <= 0 and chg >= floor_pct:
            piv.append(ext_i)
            trend = 1
            ext_i = i
    piv.append(ext_i)
    piv = [p for k, p in enumerate(piv) if k == 0 or piv[k - 1] != p]
    legs = []
    for k in range(len(piv) - 1):
        a, b = piv[k], piv[k + 1]
        if close[b] > close[a]:
            legs.append((close[b] / close[a] - 1) * 100)
    return legs


def _atr_pct(bars: pd.DataFrame) -> float:
    """일중 변동성 — (high-low)/close 평균 % (봉 단위 변동폭)."""
    rng = (bars["high"] - bars["low"]) / bars["close"] * 100
    return float(rng.mean())


def run(freqs: list[str], floors: list[float]) -> tuple[pd.DataFrame, dict]:
    files = sorted(DATA_DIR.glob("2026-*.parquet"))
    rows: list[dict] = []
    for f in files:
        date = f.stem
        df = pd.read_parquet(f)
        df["ts"] = pd.to_datetime(df["ts"])
        mkt = df[(df["ts"].dt.time >= MARKET_OPEN) & (df["ts"].dt.time <= MARKET_CLOSE)]
        for code, sub in mkt.groupby("code"):
            sub = sub.sort_values("ts").set_index("ts")
            daily_ret = float(sub["daily_return"].dropna().iloc[-1]) if "daily_return" in sub and sub["daily_return"].notna().any() else np.nan
            for freq in freqs:
                bars = aggregate_bars(sub, freq=freq)
                if len(bars) < 20:
                    continue
                close = bars["close"].to_numpy()
                atrp = _atr_pct(bars)
                for floor in floors:
                    rt = zigzag_trades_realtime(close, floor)
                    la = zigzag_trades_lookahead(close, floor)
                    rows.append({
                        "date": date, "code": str(code), "freq": freq, "floor": floor,
                        "atr_pct": round(atrp, 3), "daily_ret": round(daily_ret, 2) if not np.isnan(daily_ret) else None,
                        "rt_n": len(rt), "rt_gross_sum": round(sum(rt), 3),
                        "rt_gross_mean": round(float(np.mean(rt)), 3) if rt else 0.0,
                        "rt_win": round(sum(1 for x in rt if x > 0.3) / len(rt) * 100, 1) if rt else 0.0,
                        "la_n": len(la), "la_gross_sum": round(sum(la), 3),
                    })
    tdf = pd.DataFrame(rows)
    return tdf, _summarize(tdf, freqs, floors)


def _summarize(tdf: pd.DataFrame, freqs: list[str], floors: list[float]) -> dict:
    summary: dict = {"grid": {}, "selection": {}}
    for freq in freqs:
        for floor in floors:
            g = tdf[(tdf["freq"] == freq) & (tdf["floor"] == floor)]
            if g.empty:
                continue
            # 종목·날짜 단위 net (왕복 평균 gross - 비용)
            gm = g["rt_gross_mean"]
            net = {lbl: round(float(gm.mean()) - c, 3) for lbl, c in COST_SCENARIOS.items()}
            summary["grid"][f"{freq}/floor{floor}"] = {
                "stock_days": len(g), "trades_total": int(g["rt_n"].sum()),
                "gross_mean_per_trade": round(float(gm.mean()), 3),
                "win_rate": round(float(g["rt_win"].mean()), 1),
                "net_per_trade": net,
                "la_gross_mean_per_trade": round(float(g["la_gross_sum"].sum() / max(g["la_n"].sum(), 1)), 3),
            }
    # 종목 선별 효과 — 대표 grid (가장 거래 많은 floor) 에서 ATR% / daily_ret 상·하위 비교
    best = max(summary["grid"], key=lambda k: summary["grid"][k]["trades_total"]) if summary["grid"] else None
    if best:
        freq, fl = best.split("/floor")
        g = tdf[(tdf["freq"] == freq) & (tdf["floor"] == float(fl))].copy()
        g = g[g["rt_n"] > 0]
        for axis in ["atr_pct", "daily_ret"]:
            gg = g[g[axis].notna()]
            if len(gg) < 8:
                continue
            hi = gg[gg[axis] >= gg[axis].median()]
            lo = gg[gg[axis] < gg[axis].median()]
            summary["selection"][f"{best}|{axis}"] = {
                "high_half_gross_mean": round(float(hi["rt_gross_mean"].mean()), 3),
                "low_half_gross_mean": round(float(lo["rt_gross_mean"].mean()), 3),
                "high_half_net_limit0.2": round(float(hi["rt_gross_mean"].mean()) - 0.2, 3),
                "low_half_net_limit0.2": round(float(lo["rt_gross_mean"].mean()) - 0.2, 3),
                "median_split": round(float(gg[axis].median()), 3),
            }
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--freq", nargs="+", default=["3min", "5min"])
    ap.add_argument("--floors", nargs="+", type=float, default=[0.5, 0.8, 1.0, 1.5])
    args = ap.parse_args()
    tdf, summary = run(args.freq, args.floors)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== ZigZag 실시간 백테스트 (5/18~5/29 전종목, 비용 차감 net/왕복) ===")
    print(f"{'grid':<16}{'종목일':>6}{'왕복':>6}{'gross/회':>9}{'승률':>6}{'net지정0.2':>11}{'net시장0.4':>11}{'lookahead':>10}")
    for k, v in summary["grid"].items():
        print(f"{k:<16}{v['stock_days']:>6}{v['trades_total']:>6}{v['gross_mean_per_trade']:>9.3f}"
              f"{v['win_rate']:>5.0f}%{v['net_per_trade']['limit_0.2%']:>11.3f}"
              f"{v['net_per_trade']['market_0.4%']:>11.3f}{v['la_gross_mean_per_trade']:>10.3f}")
    print("\n=== 종목 선별 효과 (대표 grid, 중앙값 상·하위 gross/회) ===")
    for k, v in summary["selection"].items():
        print(f"{k}: 상위 {v['high_half_gross_mean']:+.3f}% (net지정 {v['high_half_net_limit0.2']:+.3f}) "
              f"vs 하위 {v['low_half_gross_mean']:+.3f}% (net지정 {v['low_half_net_limit0.2']:+.3f}) "
              f"[분할 {v['median_split']}]")
    print(f"\n저장: {OUT_PATH}")
    print("주의: 실시간 net 은 confirm lag 반영. look-ahead 는 실거래 불가 상한선.")


if __name__ == "__main__":
    main()
