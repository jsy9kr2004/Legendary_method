"""replay CODE DATE — 종목 1개의 그날 시그널 시계열 + R14 breakdown.

매직 넘버 튜닝 목적: 사용자가 매수/매도 결정한 시점의 시그널 값과 R14 breakdown 을
한눈에 보고 "이 항목 가중치가 너무 높았다 / 낮았다", "이 트리거가 너무 자주 발화했다"
같은 판단을 직접 내림.

사용:
    python -m src.analysis.replay 091340 2026-05-15
    python -m src.analysis.replay 091340 2026-05-15 --since 09:30 --until 10:30
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
    """parquet 우선, fallback 으로 jsonl. code 는 str 보존."""
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


def _fmt(v, fmt: str, na: str = "—") -> str:
    if v is None:
        return na
    try:
        if pd.isna(v):
            return na
    except (TypeError, ValueError):
        pass
    try:
        return fmt.format(v)
    except (TypeError, ValueError):
        return na


def replay_stock(
    code: str,
    day: _date,
    since: str | None = None,
    until: str | None = None,
) -> None:
    """그 종목의 그날 시그널 시계열을 터미널에 출력."""
    tlog = _load_tick_logs(day)
    if tlog is None:
        print(f"[replay] {day} tick_logs 없음 — 운영 안 했거나 아직 변환 X")
        return

    s = tlog[tlog["code"] == code].copy()
    if s.empty:
        print(f"[replay] {code} 의 {day} 데이터 없음 (Stage 0 통과 안 했을 수도)")
        return
    s = s.sort_values("ts")

    # 시간 필터 (HH:MM)
    def _hhmm(ts: str) -> str:
        return ts[11:16] if isinstance(ts, str) and len(ts) >= 16 else ""

    if since:
        s = s[s["ts"].apply(lambda x: _hhmm(x) >= since)]
    if until:
        s = s[s["ts"].apply(lambda x: _hhmm(x) <= until)]

    if s.empty:
        print(f"[replay] {code} {day} 시간 범위 내 데이터 없음")
        return

    name = s["name"].iloc[0]
    print(f"\n━━ {code} {name} — {day} (n={len(s):,} tick) ━━")

    # 매수/매도 마커 — 해당 종목의 trade events
    trades = _load_trades(day)
    if trades is not None:
        st = trades[trades["code"] == code]
        if not st.empty:
            print("\n[매수/매도 이벤트]")
            for _, t in st.iterrows():
                action = str(t["action"]).upper()
                ts_short = t["ts"][11:19] if isinstance(t["ts"], str) else str(t["ts"])
                price_str = _fmt(t.get("price"), "₩{:,.0f}")
                trig = t.get("trigger_fired")
                trig_str = f"  trigger={trig}" if trig and pd.notna(trig) else ""
                print(f"  {ts_short}  {action:<4} {price_str}{trig_str}")

    # 시계열 표 헤더
    print(
        "\n시각      가격         등급/점수    a5    a1    VP    R15(c1/c2/c3/c4)  사유"
    )
    print("─" * 100)
    for _, row in s.iterrows():
        ts_short = row["ts"][11:19] if isinstance(row["ts"], str) else str(row["ts"])
        price = row.get("price")
        ret = row.get("daily_return")
        grade = row.get("buy_grade") or "—"
        score = row.get("buy_score")
        a5 = row.get("vol_accel_5m")
        a1 = row.get("vol_accel_1m")
        vp = row.get("vp")
        c1 = "✓" if row.get("trigger_c1_vp_below_100") else "·"
        c2 = "✓" if row.get("trigger_c2_bearish_divergence") else "·"
        c3 = "✓" if row.get("trigger_c3_vol_drain") else "·"
        c4 = "✓" if row.get("trigger_c4_bearish_candle") else "·"
        reasons = row.get("buy_reasons") or []
        # parquet 의 list 컬럼 — numpy array 일 수도
        if not isinstance(reasons, (list, tuple)):
            try:
                reasons = list(reasons)
            except TypeError:
                reasons = []
        reasons_str = " / ".join(str(r) for r in reasons[:3]) if reasons else ""

        price_s = _fmt(price, "{:>7,.0f}원")
        ret_s = _fmt(ret, "({:+5.1f}%)")
        score_s = _fmt(score, "{:+5.1f}")
        a5_s = _fmt(a5, "{:>5.1f}")
        a1_s = _fmt(a1, "{:>5.1f}")
        vp_s = _fmt(vp, "{:>3.0f}")

        print(
            f"{ts_short}  {price_s} {ret_s}  {grade:>7} {score_s}  "
            f"{a5_s} {a1_s}  {vp_s}    {c1} {c2} {c3} {c4}     {reasons_str}"
        )

    # 요약
    print("\n[요약]")
    n_strong = int((s["buy_grade"] == "STRONG").sum())
    n_watch = int((s["buy_grade"] == "WATCH").sum())
    max_score = s["buy_score"].max() if "buy_score" in s.columns else None
    max_score_s = _fmt(max_score, "{:+.1f}")
    funnel_pass = int(s["funnel_passed_rising"].sum()) if "funnel_passed_rising" in s.columns else 0
    print(
        f"  STRONG tick: {n_strong:,} / WATCH tick: {n_watch:,} / "
        f"최고 점수: {max_score_s} / RISING 통과 tick: {funnel_pass:,}"
    )
    # 트리거 발화 빈도 — 청산 임계 튜닝용
    for c in (
        ("trigger_c1_vp_below_100", "C1 VP<100"),
        ("trigger_c2_bearish_divergence", "C2 Bearish"),
        ("trigger_c3_vol_drain", "C3 자금고갈"),
        ("trigger_c4_bearish_candle", "C4 윗꼬리음봉"),
    ):
        col, label = c
        if col in s.columns:
            n = int(s[col].fillna(False).astype(bool).sum())
            print(f"  {label} 발화: {n:,} tick")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("code", help="6자리 종목코드")
    parser.add_argument("date", help="YYYY-MM-DD")
    parser.add_argument("--since", help="시작 시각 (HH:MM)")
    parser.add_argument("--until", help="끝 시각 (HH:MM)")
    args = parser.parse_args()

    day = datetime.strptime(args.date, "%Y-%m-%d").date()
    replay_stock(args.code, day, since=args.since, until=args.until)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
