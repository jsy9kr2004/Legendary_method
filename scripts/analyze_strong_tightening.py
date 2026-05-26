"""STRONG 조이기 — 필터별 빈도/동시성/승률/비용반영 순기대값.

질문(사용자 2026-05-25): STRONG N 너무 큼(폰 매매, 동시 2~3개 한계, 거래비용).
승률·수익 기반으로 조이면 유의미하게 좋아지나?

척도:
  - entries/day: 빈도 (폰으로 따라갈 수 있나)
  - max_concurrent: 10분 attention 윈도우 기준 동시 진입 최대 (2~3개 한계)
  - up%/down%: +2% 먼저 / -2% 먼저 (진입 품질)
  - gross_E: +2/-2 bracket 기대값 = 2*(up%-down%)  (neither=0 가정, 보수적)
  - net_E: gross_E - 왕복거래비용

전제: data/backtest/system_entries.csv (analyze_system_entries.py 산출).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

ROUND_TRIP_COST = 0.4   # 왕복 비용% (거래세 0.18 + 수수료 + 슬리피지 ~0.2). 민감도 주의.
ATTN_WIN_MIN = 10       # 동시성 측정용 attention 윈도우(분)
N_DAYS = 3


def max_concurrent(times: pd.Series, win_min: int) -> int:
    """각 진입이 [t, t+win] 점유한다고 보고 최대 동시 점유 수 (sweep line)."""
    if len(times) == 0:
        return 0
    starts = sorted(times)
    events = []
    for t in starts:
        events.append((t, +1))
        events.append((t + pd.Timedelta(minutes=win_min), -1))
    events.sort(key=lambda x: (x[0], x[1]))
    cur = mx = 0
    for _, delta in events:
        cur += delta
        mx = max(mx, cur)
    return mx


def evaluate(df: pd.DataFrame, name: str) -> dict:
    v = df[df["race"] != "no_fwd"]
    n = len(v)
    if n == 0:
        return {"filter": name, "n": 0}
    up = (v["race"] == "up").mean() * 100
    dn = (v["race"] == "down").mean() * 100
    ne = (v["race"] == "neither").mean() * 100
    gross = 2 * (up - dn) / 100
    # 동시성: 일자별 max_concurrent 의 최대
    concur = 0
    for d, g in v.groupby("date"):
        dt = pd.to_datetime("2026-" + d + " " + g["time"])
        concur = max(concur, max_concurrent(dt, ATTN_WIN_MIN))
    return {
        "filter": name, "n": n, "per_day": round(n / N_DAYS, 1),
        "max_concur": concur,
        "up%": round(up, 1), "down%": round(dn, 1), "neither%": round(ne, 1),
        "win_share%": round(up / (up + dn) * 100, 1) if (up + dn) else np.nan,
        "gross_E%": round(gross, 2), "net_E%": round(gross - ROUND_TRIP_COST, 2),
    }


def main() -> int:
    import sys
    global N_DAYS
    csv = sys.argv[1] if len(sys.argv) > 1 else "data/backtest/system_entries.csv"
    if len(sys.argv) > 2:
        N_DAYS = int(sys.argv[2])
    df = pd.read_csv(csv)
    df = df[df["race"] != "no_fwd"].copy()
    df["score"] = pd.to_numeric(df["buy_score"], errors="coerce")

    good_zone = df["dist_high"].between(-2.0, -0.5)
    breakout_thrust = (df["setup"] == "breakout") & df["recent_5m"].between(1.0, 3.0)
    pullback_shallow = (df["setup"] == "pullback") & df["dist_high"].between(-2.0, -1.0)
    not_chase = ~df["setup"].isin(["chase", "none", "no_data"])

    filters = [
        ("현재 전체 STRONG", df),
        ("score >= 5.5", df[df["score"] >= 5.5]),
        ("score >= 6", df[df["score"] >= 6]),
        ("score >= 7", df[df["score"] >= 7]),
        ("chase/none 제외", df[not_chase]),
        ("좋은 자리(-0.5~-2%)", df[good_zone]),
        ("좋은자리 AND score>=6", df[good_zone & (df["score"] >= 6)]),
        ("돌파추진력 OR 얕은눌림", df[breakout_thrust | pullback_shallow]),
        ("돌파추진력OR얕은눌림 AND score>=6",
         df[(breakout_thrust | pullback_shallow) & (df["score"] >= 6)]),
    ]

    rows = [evaluate(sub, name) for name, sub in filters]
    res = pd.DataFrame(rows)

    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", None)
    print(f"\n왕복 거래비용 가정 = {ROUND_TRIP_COST}%  /  동시성 윈도우 = {ATTN_WIN_MIN}분  /  {N_DAYS}일 표본\n")
    print(res.to_string(index=False))

    print("\n읽는 법:")
    print("  - per_day: 하루 진입 수 (폰으로 2~3개 동시면 하루 ~5~15건이 현실적)")
    print("  - max_concur: 10분 내 동시 최대 (2~3 이하라야 폰 매매 가능)")
    print("  - net_E%: 비용 뺀 한 건당 순기대값. >0 이라야 의미. 현재 전체는 보통 음수.")
    print("  - ⚠ 3일 표본 → tight 필터는 n 작아 노이즈. 방향성만. 5/23~ 누적 재검증.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
