"""regret DATE — 그날 STRONG/WATCH 도달 종목 + 사용자 매수/매도 결정 매칭.

매직 넘버 튜닝 목적:
    "STRONG 떴는데 안 산 종목 vs 산 종목 — 어느 항목이 다른가?"
    "매수한 종목의 trigger 발화 빈도 vs 매수 안 한 종목" 비교로 R14 가중치 /
    R15 임계의 false positive/negative 패턴 발견.

사용:
    python -m src.analysis.regret 2026-05-15
"""
from __future__ import annotations

import argparse
import os
from datetime import date as _date
from datetime import datetime
from pathlib import Path

import pandas as pd


def _data_dir() -> Path:
    return Path(os.getenv("DATA_DIR", "data"))


def _load_tick_logs(day: _date) -> pd.DataFrame | None:
    base = _data_dir() / "tick_logs"
    pq = base / f"{day.isoformat()}.parquet"
    if pq.exists():
        return pd.read_parquet(pq)
    jsonl = base / "raw" / f"{day.isoformat()}.jsonl"
    if jsonl.exists():
        return pd.read_json(jsonl, lines=True, dtype={"code": str})
    return None


def _load_trades(day: _date) -> pd.DataFrame | None:
    base = _data_dir() / "trades"
    pq = base / f"{day.isoformat()}.parquet"
    if pq.exists():
        return pd.read_parquet(pq)
    jsonl = base / f"{day.isoformat()}.jsonl"
    if jsonl.exists():
        return pd.read_json(jsonl, lines=True, dtype={"code": str})
    return None


_GRADE_RANK = {"STRONG": 4, "WATCH": 3, "NEUTRAL": 2, "AVOID": 1}


def regret_summary(day: _date) -> None:
    """그날 도달 등급 분포 + 사용자 결정 매칭."""
    tlog = _load_tick_logs(day)
    if tlog is None:
        print(f"[regret] {day} tick_logs 없음")
        return

    trades = _load_trades(day)
    bought_codes: set[str] = set()
    if trades is not None and not trades.empty:
        bought_codes = set(trades[trades["action"] == "buy"]["code"].tolist())

    # 종목별 최고 등급 + 최고 점수
    tlog = tlog.copy()
    tlog["_grade_rank"] = tlog["buy_grade"].map(lambda g: _GRADE_RANK.get(g, 0))
    peak = (
        tlog.sort_values(["_grade_rank", "buy_score"], ascending=False)
        .drop_duplicates("code", keep="first")
        [["code", "name", "buy_grade", "buy_score", "ts"]]
    )
    peak = peak[peak["buy_grade"].isin(["STRONG", "WATCH"])]
    peak = peak.sort_values("buy_score", ascending=False)

    n_strong = int((peak["buy_grade"] == "STRONG").sum())
    n_watch = int((peak["buy_grade"] == "WATCH").sum())
    print(f"\n━━ {day} 도달 등급 요약 — STRONG {n_strong} / WATCH {n_watch} ━━")

    if not peak.empty:
        print("\n[그날 surface 된 STRONG/WATCH 종목 (점수 내림차순)]")
        print(f"{'코드':<8}{'이름':<14}{'등급':>7}  {'점수':>6}  {'도달 시각':>10}  매수 여부")
        print("─" * 70)
        for _, row in peak.iterrows():
            code = str(row["code"])
            name = str(row["name"])[:14]
            grade = row["buy_grade"]
            score = row["buy_score"]
            ts = row["ts"][11:19] if isinstance(row["ts"], str) else str(row["ts"])
            mark = "💰 매수" if code in bought_codes else "—"
            print(f"{code:<8}{name:<14}{grade:>7}  {score:>+6.1f}  {ts:>10}  {mark}")

    # 사용자 매수/매도 이벤트
    if trades is not None and not trades.empty:
        print(f"\n[사용자 매수/매도 이벤트 ({len(trades)}건)]")
        print(f"{'시각':<10}{'액션':<6}{'코드':<8}{'이름':<14}{'가격':>10}  사유")
        print("─" * 70)
        for _, t in trades.sort_values("ts").iterrows():
            ts = t["ts"][11:19] if isinstance(t["ts"], str) else str(t["ts"])
            action = str(t["action"]).upper()
            code = str(t["code"])
            name = str(t.get("name") or "")[:14]
            price = t.get("price")
            price_s = f"₩{int(price):,}" if pd.notna(price) and price else "—"
            trig = t.get("trigger_fired") or ""
            trig_s = str(trig) if pd.notna(trig) else ""
            print(f"{ts:<10}{action:<6}{code:<8}{name:<14}{price_s:>10}  {trig_s}")

    # surface 됐으나 안 산 STRONG 종목 (가장 큰 후회 후보)
    if not peak.empty:
        not_bought_strong = peak[
            (peak["buy_grade"] == "STRONG") & (~peak["code"].astype(str).isin(bought_codes))
        ]
        if not not_bought_strong.empty:
            print(f"\n[⚠ STRONG 떴는데 안 산 종목 — 매수 누락 검토 ({len(not_bought_strong)}건)]")
            for _, row in not_bought_strong.iterrows():
                ts = row["ts"][11:19] if isinstance(row["ts"], str) else str(row["ts"])
                print(
                    f"  {row['code']} {str(row['name'])[:14]:<14}  "
                    f"{row['buy_score']:>+5.1f}  도달 {ts}"
                )

    # 매수했으나 STRONG 안 떴던 종목 (false positive 후보)
    if bought_codes and not peak.empty:
        strong_codes = set(peak[peak["buy_grade"] == "STRONG"]["code"].astype(str))
        bought_not_strong = bought_codes - strong_codes
        if bought_not_strong:
            print(
                f"\n[⚠ 매수했으나 STRONG 미도달 종목 — 가중치/임계 재검토 "
                f"({len(bought_not_strong)}건)]"
            )
            for code in sorted(bought_not_strong):
                # 그 종목의 그날 최고 점수 확인
                peak_row = peak[peak["code"].astype(str) == code]
                if not peak_row.empty:
                    r = peak_row.iloc[0]
                    print(
                        f"  {code} {str(r['name'])[:14]:<14}  "
                        f"최고 {r['buy_grade']} {r['buy_score']:>+5.1f}"
                    )
                else:
                    print(f"  {code}  최고 등급 NEUTRAL/AVOID 또는 surface X")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("date", help="YYYY-MM-DD")
    args = parser.parse_args()

    day = datetime.strptime(args.date, "%Y-%m-%d").date()
    regret_summary(day)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
