"""사용자 매매 vs 단저단고 시그널 정합도 자동 통계.

`docs/scalping-redesign-2026-05-27.md` §9 F. 매매일지 자동 통계.

기준 (CLAUDE.md trading-journal §0.2 시간차 윈도우):
    사용자 매매 ts 의 [-30s, +5s] 윈도우에 그 종목의 mr_sigB / mr_sigS 발화
    여부 확인. 매수 = sigB 매칭 / 매도 = sigS 매칭.

데이터 우선순위:
    1. tick_log parquet 의 mr_sigB / mr_sigS 컬럼 (Task #14 이후 데이터).
    2. 없으면 raw price 기반 build_bars + classify 로 즉석 산출 (fallback).

출력: data/journal/auto/YYYY-MM-DD.md.

CLI:
    python -m src.analysis.mr_alignment 2026-05-27
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from src.scalping.bars import build_bars
from src.scalping.signals.mean_reversion import classify


WINDOW_BEFORE_SEC = 30
WINDOW_AFTER_SEC = 5


def _data_dir() -> Path:
    return Path(os.getenv("DATA_DIR", "data"))


def _load_trades(date_str: str) -> pd.DataFrame:
    d = _data_dir() / "trades"
    parq = d / f"{date_str}.parquet"
    jsonl = d / f"{date_str}.jsonl"
    if parq.exists():
        df = pd.read_parquet(parq)
    elif jsonl.exists():
        df = pd.read_json(jsonl, lines=True)
    else:
        return pd.DataFrame()
    df["ts"] = pd.to_datetime(df["ts"])
    return df


def _load_tick_log(date_str: str) -> pd.DataFrame:
    d = _data_dir() / "tick_logs"
    parq = d / f"{date_str}.parquet"
    jsonl = d / "raw" / f"{date_str}.jsonl"
    if parq.exists():
        df = pd.read_parquet(parq)
    elif jsonl.exists():
        df = pd.read_json(jsonl, lines=True)
    else:
        return pd.DataFrame()
    df["ts"] = pd.to_datetime(df["ts"])
    return df


def _signals_for_code(code: str, tick_log: pd.DataFrame) -> pd.DataFrame:
    """종목별 (ts, mr_sigB, mr_sigS, mr_reason) DataFrame.

    tick_log 에 mr_sigB 컬럼이 있으면 그대로 사용 (Task #14 이후).
    없으면 raw price → build_bars → classify 로 즉석 산출.
    """
    sub = tick_log[tick_log["code"] == code].sort_values("ts").copy()
    if "mr_sigB" in sub.columns and sub["mr_sigB"].notna().any():
        return sub[["ts", "mr_sigB", "mr_sigS", "mr_reason"]].reset_index(drop=True)
    if len(sub) < 200:
        return pd.DataFrame(columns=["ts", "mr_sigB", "mr_sigS", "mr_reason"])
    sub_idx = sub.set_index("ts").sort_index()
    bars = build_bars(sub_idx)
    if len(bars) < 25:
        return pd.DataFrame(columns=["ts", "mr_sigB", "mr_sigS", "mr_reason"])
    classify(bars)
    bars_reset = bars.reset_index().rename(columns={bars.index.name or "ts": "ts"})
    bars_reset["mr_reason"] = None
    return bars_reset[["ts", "sigB", "sigS", "mr_reason"]].rename(
        columns={"sigB": "mr_sigB", "sigS": "mr_sigS"}
    )


def evaluate_alignment(trades: pd.DataFrame, tick_log: pd.DataFrame) -> list[dict]:
    """각 매매 이벤트에 대해 윈도우 내 sigB/sigS 발화 여부 평가.

    윈도우 정책:
      - tick log 에 mr_sigB 컬럼 있음 (tick 단위 시그널, 3초 간격) → [-30s, +5s]
        매매일지 시간차 정합.
      - fallback (build_bars 즉석 산출, 3분봉 ts 간격 3분) → 매매 ts 가 속한 봉
        + 직전 봉 (총 [-6min, +0min]). 봉 단위 시그널이라 윈도우 늘림.
    """
    results = []
    for _, trade in trades.iterrows():
        code = str(trade["code"])
        ts = trade["ts"]
        action = trade["action"]
        sigs = _signals_for_code(code, tick_log)
        if sigs.empty:
            results.append({
                "ts": ts, "code": code, "name": trade.get("name", code),
                "action": action, "price": trade.get("price"),
                "match": None, "window_n": 0, "fallback": True,
            })
            continue
        # 데이터 입도 자동 감지: ts 간격 ≥ 60s 면 봉 단위 윈도우.
        sample_diff = sigs["ts"].diff().dropna().median()
        is_bar_grain = sample_diff is not None and sample_diff >= pd.Timedelta(seconds=60)
        if is_bar_grain:
            lo = ts - timedelta(minutes=6)
            hi = ts
        else:
            lo = ts - timedelta(seconds=WINDOW_BEFORE_SEC)
            hi = ts + timedelta(seconds=WINDOW_AFTER_SEC)
        window = sigs[(sigs["ts"] >= lo) & (sigs["ts"] <= hi)]
        if action == "buy":
            matched = bool(window["mr_sigB"].any()) if len(window) > 0 else False
            reason_list = [r for r in window.loc[window["mr_sigB"] == True, "mr_reason"].dropna().tolist() if r]
        elif action == "sell":
            matched = bool(window["mr_sigS"].any()) if len(window) > 0 else False
            reason_list = [r for r in window.loc[window["mr_sigS"] == True, "mr_reason"].dropna().tolist() if r]
        else:
            matched = None
            reason_list = []
        results.append({
            "ts": ts, "code": code, "name": trade.get("name", code),
            "action": action, "price": trade.get("price"),
            "match": matched, "window_n": len(window),
            "matched_reason": ", ".join(reason_list[:2]) if reason_list else None,
        })
    return results


def render_report(date_str: str, results: list[dict]) -> str:
    """Markdown 리포트 생성."""
    lines = [
        f"# 단저단고 시그널 정합도 자동 통계 — {date_str}",
        "",
        f"기준: 사용자 매매 ts 의 [-{WINDOW_BEFORE_SEC}s, +{WINDOW_AFTER_SEC}s] 윈도우에 mr_sigB / mr_sigS 발화 여부.",
        "",
    ]
    if not results:
        lines.append("매매 이벤트 없음.")
        return "\n".join(lines)

    df = pd.DataFrame(results)
    buy = df[df["action"] == "buy"]
    sell = df[df["action"] == "sell"]
    buy_match = buy["match"].sum() if len(buy) > 0 else 0
    sell_match = sell["match"].sum() if len(sell) > 0 else 0

    lines.append("## 종합")
    lines.append("")
    lines.append(f"- 매수 {len(buy)} 건 / sigB 매칭 **{buy_match} 건 ({buy_match/max(len(buy),1)*100:.0f}%)**")
    lines.append(f"- 매도 {len(sell)} 건 / sigS 매칭 **{sell_match} 건 ({sell_match/max(len(sell),1)*100:.0f}%)**")
    lines.append("")

    lines.append("## 매매별 상세")
    lines.append("")
    lines.append("| ts | 종목 | 동작 | 가격 | 매칭 | window n | 사유 |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in results:
        ts_str = r["ts"].strftime("%H:%M:%S") if hasattr(r["ts"], "strftime") else str(r["ts"])
        match_icon = "✓" if r.get("match") else ("✗" if r.get("match") is False else "—")
        reason = r.get("matched_reason") or ""
        lines.append(
            f"| {ts_str} | {r.get('code')} {r.get('name','')} | {r['action']} | "
            f"{r.get('price') or '—'} | {match_icon} | {r.get('window_n', 0)} | {reason} |"
        )
    lines.append("")
    lines.append("## 메모")
    lines.append("")
    lines.append("- 매칭 = sigB/sigS 발화 시점이 사용자 매매 ts 의 30s 전 ~ 5s 후 윈도우 안.")
    lines.append("- `fallback=True` 시 tick_log 에 mr_sigB 컬럼 없어 build_bars 즉석 산출.")
    lines.append("- 누적 통계: data/journal/auto/ 디렉토리 전체 합치면 패턴 추출 가능.")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("date", help="YYYY-MM-DD")
    args = parser.parse_args()
    date_str = args.date
    trades = _load_trades(date_str)
    if trades.empty:
        print(f"매매 이벤트 없음: {date_str}")
        return
    tick_log = _load_tick_log(date_str)
    if tick_log.empty:
        print(f"tick_log 없음: {date_str}")
        return
    results = evaluate_alignment(trades, tick_log)
    md = render_report(date_str, results)
    output = _data_dir() / "journal" / "auto" / f"{date_str}.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(md, encoding="utf-8")
    print(md)
    print(f"\n저장: {output}")


if __name__ == "__main__":
    main()
