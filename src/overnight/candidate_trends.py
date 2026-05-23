"""결정 레포트 후보 3거래일 추이 (거래대금 / 회전율 / 수급 + 순위 변동).

2026-05-24 도입 — 사용자 요청: 후보 카드에 거래대금·회전율·수급의 최근 3거래일
변화 추이 + 거래대금/회전율 순위 변동을 함께 표시.

데이터 소스:
    거래대금 값 추이 — daily/ohlcv.parquet (전종목·전일자 `trading_value`, 원).
    회전율 값 추이   — daily `trading_value` / 시총(억). 시총은 후보 dict 의 현재값
                       (intraday.infer_market_cap_eok 로 역산) 사용. 3거래일 동안의
                       시총 변동은 무시 (근사 — 3일이라 오차 미미).
    순위 변동        — 과거 14:50 스냅샷 (거래대금 top 50). 종목이 top 50 밖이면
                       결측("권외"), 스냅샷 파일 자체가 없으면 "—".
    수급 추이        — investor_daily/{code}.parquet (자체 누적, 2026-05-22~).

오늘(today) 셀:
    거래대금/회전율 — daily 에 오늘 행이 아직 없으므로(16:00 적재) 후보 dict 의
                     현재(스냅샷) 값을 사용. 순위도 후보 dict 의 rank/turnover_rank.
    수급          — investor_daily 에 오늘 행이 이미 append 된 상태(scheduler 가
                     fetch 직후 저장)라 그대로 포함.

모든 함수는 데이터 결측에 강건 — 파일/행 없으면 해당 셀만 비우고 진행.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from loguru import logger

from src.data.intraday import compute_turnover

# 순위 셀 상태
RANK_IN = "in"      # 스냅샷에서 발견 — rank 값 있음
RANK_OUT = "out"    # 스냅샷은 있으나 종목이 top 50 밖 — "권외"
RANK_NA = "na"      # 스냅샷 파일 자체가 없음 — "—"

_DECISION_SNAPSHOT_HHMM = "14_50"


def _coerce_date(v: Any) -> date | None:
    """date / datetime / 'YYYY-MM-DD' 문자열 / pandas Timestamp → date."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return pd.Timestamp(v).date()
    except (ValueError, TypeError):
        return None


def _recent_daily_rows(
    daily_ohlcv: pd.DataFrame,
    code: str,
    today: date,
    n: int,
) -> pd.DataFrame:
    """해당 종목의 today **이전** 거래일 일봉 중 최근 n개 (date 오름차순).

    today 당일 행은 제외 (오늘 값은 후보 dict 의 실시간 값으로 별도 처리).
    """
    if daily_ohlcv is None or daily_ohlcv.empty:
        return pd.DataFrame()
    own = daily_ohlcv[daily_ohlcv["code"].astype(str).str.zfill(6) == code].copy()
    if own.empty:
        return own
    own["_d"] = own["date"].apply(_coerce_date)
    own = own[own["_d"].notna() & (own["_d"] < today)]
    own = own.sort_values("_d").tail(n)
    return own


def _rank_in_snapshot(
    data_dir: Path,
    d: date,
    code: str,
    rank_col: str,
) -> tuple[int | None, str]:
    """특정 거래일 14:50 스냅샷에서 종목의 순위 조회.

    Returns:
        (rank, state) — state 는 RANK_IN / RANK_OUT / RANK_NA.
    """
    from src.data.snapshot import load_snapshot

    try:
        snap = load_snapshot(data_dir, d, _DECISION_SNAPSHOT_HHMM)
    except Exception:  # noqa: BLE001
        return None, RANK_NA
    if snap is None or snap.empty:
        return None, RANK_NA
    snap = snap.copy()
    snap["_code"] = snap["code"].astype(str).str.zfill(6)
    row = snap[snap["_code"] == code]
    if row.empty:
        return None, RANK_OUT  # 스냅샷은 있으나 top 50 밖
    val = row.iloc[0].get(rank_col)
    if val is None or (isinstance(val, float) and val != val):
        return None, RANK_OUT
    try:
        return int(val), RANK_IN
    except (TypeError, ValueError):
        return None, RANK_OUT


def _value_trend(
    daily_ohlcv: pd.DataFrame,
    data_dir: Path,
    code: str,
    today: date,
    today_value: float,
    today_rank: int | None,
    rank_col: str,
    value_kind: str,
    market_cap_eok: int,
    n_days: int,
) -> list[dict[str, Any]]:
    """거래대금/회전율 추이 셀 리스트 (오래된→오늘).

    value_kind: "trading_value" | "turnover".
        trading_value → daily `trading_value` 그대로(원).
        turnover      → daily `trading_value` / 시총(억) (compute_turnover).
    """
    prior = _recent_daily_rows(daily_ohlcv, code, today, n_days - 1)
    cells: list[dict[str, Any]] = []
    for _, r in prior.iterrows():
        d = _coerce_date(r["date"])
        tv = int(r.get("trading_value") or 0)
        if value_kind == "turnover":
            value = compute_turnover(tv, market_cap_eok) if market_cap_eok > 0 else float("nan")
        else:
            value = tv
        rank, state = _rank_in_snapshot(data_dir, d, code, rank_col)
        cells.append({"date": d.isoformat() if d else None, "value": value,
                      "rank": rank, "rank_state": state})
    # 오늘 셀 — 후보 dict 현재값 + 후보 rank
    cells.append({
        "date": today.isoformat(),
        "value": float(today_value) if today_value == today_value else float("nan"),
        "rank": int(today_rank) if today_rank is not None else None,
        "rank_state": RANK_IN if today_rank is not None else RANK_OUT,
    })
    return cells


def _supply_trend(
    data_dir: Path,
    code: str,
    today: date,
    n_days: int,
) -> list[dict[str, Any]]:
    """수급(외인/기관/프로그램) 최근 n거래일 추이 (오래된→오늘).

    investor_daily/{code}.parquet 자체 누적에서 읽음. 누적 시작(2026-05-22) 직후라
    행이 1~n개. 행 없으면 빈 리스트.
    """
    path = Path(data_dir) / "investor_daily" / f"{code}.parquet"
    if not path.exists():
        return []
    try:
        df = pd.read_parquet(path)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[trends] {code} investor_daily read 실패: {e}")
        return []
    if df.empty:
        return []
    df = df.copy()
    df["_d"] = df["date"].apply(_coerce_date)
    df = df[df["_d"].notna() & (df["_d"] <= today)].sort_values("_d").tail(n_days)
    out: list[dict[str, Any]] = []
    for _, r in df.iterrows():
        d = r["_d"]
        out.append({
            "date": d.isoformat() if d else None,
            "foreign": int(r.get("foreign_net_buy") or 0),
            "institution": int(r.get("institution_net_buy") or 0),
            "program": int(r.get("program_net_buy") or 0),
        })
    return out


def attach_candidate_trends(
    candidates: list[dict[str, Any]],
    daily_ohlcv: pd.DataFrame,
    data_dir,
    today: date,
    n_days: int = 3,
) -> None:
    """각 후보 dict 에 `trends` 키를 in-place 추가.

    trends = {
        "n_days": int,
        "trading_value": [{date, value(원), rank, rank_state}, ...],   # 오래된→오늘
        "turnover":      [{date, value(%),  rank, rank_state}, ...],
        "supply":        [{date, foreign, institution, program}, ...], # 누적분만
    }

    데이터 결측에 강건 — 한 후보에서 실패해도 나머지 후보는 계속 처리.
    """
    data_dir = Path(data_dir)
    for c in candidates:
        code = str(c.get("code", "")).zfill(6)
        if not code or code == "000000":
            continue
        try:
            market_cap_eok = int(c.get("market_cap") or 0)
            tv_today = float(c.get("trading_value") or 0)
            to_today = c.get("turnover", float("nan"))
            to_today = float(to_today) if to_today is not None else float("nan")
            rank_today = c.get("rank")
            rank_today = int(rank_today) if rank_today not in (None, 0) else None
            trk = c.get("turnover_rank")
            trk = int(trk) if trk not in (None, 0) and trk == trk else None

            c["trends"] = {
                "n_days": n_days,
                "trading_value": _value_trend(
                    daily_ohlcv, data_dir, code, today, tv_today, rank_today,
                    rank_col="rank", value_kind="trading_value",
                    market_cap_eok=market_cap_eok, n_days=n_days,
                ),
                "turnover": _value_trend(
                    daily_ohlcv, data_dir, code, today, to_today, trk,
                    rank_col="turnover_rank", value_kind="turnover",
                    market_cap_eok=market_cap_eok, n_days=n_days,
                ),
                "supply": _supply_trend(data_dir, code, today, n_days),
            }
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[trends] {code} 추이 계산 실패 — 생략: {e}")
            c["trends"] = None
