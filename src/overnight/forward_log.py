"""종배 forward 로깅 — 14:50 후보 벡터 + 다음날 실현 갭 join (2026-05-25).

목적 (memory project-eod-factor-edge):
    수급/체결강도/막판 신호는 분봉 히스토리 부재로 backtest 불가 → 매일 14:50 후보
    벡터(save_decision_candidates 로 이미 저장됨) + **다음날 실현 갭(open/high/low/
    close)** 을 누적해, N개월 후 factor_edge 분석으로 "어떤 신호가 갭을 가르나" 측정.
    = 종목별 강약(비중) + 청산 타이밍 정밀화의 **유일한 길**. 또한 청산 envelope
    (시초/최저/최고) 실측 누적.

저장: data/eod_forward/{decision_date}.json (idempotent 덮어쓰기).
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pandas as pd
from loguru import logger

from src.report.decision import _to_serializable, load_decision_candidates


def _as_date(val: Any) -> dt.date | None:
    if isinstance(val, dt.datetime):
        return val.date()
    if isinstance(val, dt.date):
        return val
    try:
        return pd.Timestamp(val).date()
    except (ValueError, TypeError):
        return None


def append_outcomes(
    decision_date: dt.date,
    daily_ohlcv: pd.DataFrame,
    data_dir,
) -> Path | None:
    """decision_date 의 저장 후보에 다음 거래일 실현 갭을 join → eod_forward 기록.

    Args:
        decision_date: 14:50 결정일 (= 종배 진입일, D). 종가에 매수했다고 가정.
        daily_ohlcv: 전종목 일봉. **D 다음 거래일(D+1) 바가 있어야** outcome 계산.
        data_dir: 데이터 루트.

    Returns:
        기록한 파일 경로. 후보 없음/다음날 바 부재 시 None.
    """
    cands = load_decision_candidates(data_dir, decision_date)
    if not cands:
        return None
    if daily_ohlcv is None or daily_ohlcv.empty:
        return None
    df = daily_ohlcv.copy()
    df["code"] = df["code"].astype(str)

    records: list[dict[str, Any]] = []
    for c in cands:
        if c.get("priority") == "excluded":
            continue
        code = str(c.get("code", "")).zfill(6)
        own = df[df["code"] == code].copy()
        if own.empty:
            continue
        own["_d"] = own["date"].apply(_as_date)
        d_row = own[own["_d"] == decision_date]
        nxt = own[own["_d"].apply(lambda x: x is not None and x > decision_date)]
        if d_row.empty or nxt.empty:
            continue  # 진입일/다음날 바 부재 (다음날 미적재면 다음 실행에서 완성)
        d_close = float(d_row.iloc[0]["close"])
        if d_close <= 0:
            continue
        nrow = nxt.sort_values("_d").iloc[0]

        def _gap(field: str) -> float:
            return (float(nrow[field]) - d_close) / d_close * 100.0

        records.append({
            "decision_date": decision_date.isoformat(),
            "outcome_date": nrow["_d"].isoformat(),
            "code": code,
            "name": c.get("name"),
            "rank": c.get("rank"),
            "sizing_bucket": c.get("sizing_bucket"),
            "kelly_bucket": (c.get("sizing") or {}).get("kelly_bucket"),
            "is_top3": c.get("is_top3"),
            "daily_return": c.get("daily_return"),
            "turnover": c.get("turnover"),
            "market_cap": c.get("market_cap"),
            # 미래 factor_edge 분석 대상 — backtest 불가 신호 (스냅샷 보존)
            "intraday_signals": c.get("intraday_signals"),
            "candle_aux": c.get("candle_aux"),
            "r4v2_check": c.get("r4v2_check"),
            # 실현 결과 (청산 envelope)
            "gap_open": _gap("open"),
            "gap_high": _gap("high"),
            "gap_low": _gap("low"),
            "gap_close": _gap("close"),
        })

    if not records:
        return None
    out = Path(data_dir) / "eod_forward" / f"{decision_date.isoformat()}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(_to_serializable(records), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(out)
    logger.info(f"[forward] {decision_date} 후보 {len(records)}개 outcome 기록 → {out}")
    return out


def backfill_pending_outcomes(
    daily_ohlcv: pd.DataFrame,
    data_dir,
    lookback_days: int = 15,
) -> list[dt.date]:
    """outcome 미기록 결정일을 일괄 처리 (self-heal).

    주식 일봉 incremental 은 데몬 cron 이 아니라 `./go update`/`./go start` 로만 갱신됨.
    16:40 cron 시점에 오늘 일봉이 아직 없으면 어제 결정의 outcome 을 못 구함 → 단일
    prev-day 처리는 영구 누락 위험. 따라서 **최근 결정일 중 eod_forward 파일이 없는
    것을 매 실행마다 모두 시도** — daily 가 나중에 갱신되면 다음 실행에서 자동 기록.

    Returns:
        이번에 새로 기록한 decision_date 리스트.
    """
    dec_dir = Path(data_dir) / "decisions"
    if not dec_dir.exists() or daily_ohlcv is None or daily_ohlcv.empty:
        return []
    fwd_dir = Path(data_dir) / "eod_forward"
    recorded: list[dt.date] = []
    files = sorted(dec_dir.glob("*.json"))[-(lookback_days * 2):]  # 주말 포함 여유
    for f in files:
        try:
            d = dt.date.fromisoformat(f.stem)
        except ValueError:
            continue
        if (fwd_dir / f"{d.isoformat()}.json").exists():
            continue  # 이미 기록됨
        if append_outcomes(d, daily_ohlcv, data_dir) is not None:
            recorded.append(d)
    if recorded:
        logger.info(f"[forward] backfill {len(recorded)}일 기록: "
                    f"{[d.isoformat() for d in recorded]}")
    return recorded


def load_outcomes(data_dir, decision_date: dt.date) -> list[dict[str, Any]]:
    """기록된 eod_forward outcome 로드 (없으면 빈 리스트)."""
    p = Path(data_dir) / "eod_forward" / f"{decision_date.isoformat()}.json"
    if not p.exists():
        return []
    try:
        return list(json.loads(p.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError):
        return []
