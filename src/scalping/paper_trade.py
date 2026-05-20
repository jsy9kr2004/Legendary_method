"""단타 paper-trade 기록기 (round 32, ritual 2 자동화).

`docs/plan.md` "Buy.Score/Exit.Triggers 가중치 검증 ritual" 의 ritual 2 자동화. 백테스트가
분봉 히스토리 부재로 불가능하므로 매일 결정 레포트 결과를 누적해 점수 ↔ 갭상
확률 상관을 산출.

자동 매매 X — 본 모듈은 **기록만** 한다. 실주문 코드 없음. CLAUDE.md "자동
매매 절대 금지" 정책 유지.

흐름:
    14:50 결정 레포트 → `record_decision()` 호출 (STRONG/WATCH 종목 저장)
    다음날 09:30 모닝 → `record_open_result()` 호출 (시초가/오전 결과 추가)
    1개월 누적 후    → `compute_summary()` 로 Spearman ρ / 평균 갭 등 산출

저장 위치:
    {DATA_DIR}/paper_trade/YYYY-MM-DD.json — 결정 일자 기준 (다음날 결과까지 동일 파일)
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass
class PaperTradeRecord:
    """단일 종목 paper-trade 기록 (14:50 결정 + 다음날 결과)."""
    code: str
    name: str = ""
    grade: str = ""              # STRONG / WATCH / NEUTRAL / AVOID
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    decision_price: float = 0.0   # 14:50 시점 가격
    decision_time: str = ""       # ISO 8601
    # 다음날 결과 (record_open_result 로 채움 — 초기 None/0)
    open_price: float | None = None
    open_gap_pct: float | None = None
    jongbae_exit_action: str | None = None  # "sell_all" / "sell_partial"
    intraday_high: float | None = None
    intraday_close: float | None = None
    morning_return_pct: float | None = None  # (intraday_high - decision_price) / decision_price * 100

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PaperTradeRecord:
        return cls(
            code=str(d.get("code", "")),
            name=str(d.get("name", "")),
            grade=str(d.get("grade", "")),
            score=float(d.get("score", 0.0)),
            reasons=list(d.get("reasons") or []),
            decision_price=float(d.get("decision_price", 0.0)),
            decision_time=str(d.get("decision_time", "")),
            open_price=_to_float_or_none(d.get("open_price")),
            open_gap_pct=_to_float_or_none(d.get("open_gap_pct")),
            jongbae_exit_action=d.get("jongbae_exit_action"),
            intraday_high=_to_float_or_none(d.get("intraday_high")),
            intraday_close=_to_float_or_none(d.get("intraday_close")),
            morning_return_pct=_to_float_or_none(d.get("morning_return_pct")),
        )


def _to_float_or_none(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None


def _path_for(data_dir: Path, decision_date: str) -> Path:
    return data_dir / "paper_trade" / f"{decision_date}.json"


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    """tmp file + os.replace 패턴 (parquet atomic write 와 동일 원칙)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f) or {}
    except json.JSONDecodeError as e:
        logger.error(f"[paper_trade] JSON 손상 — {path}: {e}")
        return {}


def record_decision(
    decision_date: str,
    records: list[PaperTradeRecord],
    data_dir: Path,
) -> None:
    """14:50 결정 레포트 직후 호출 — STRONG/WATCH 후보들 저장.

    같은 날짜 파일이 이미 있으면 기존 records 가 덮어쓰여짐 (재실행 안전).

    Args:
        decision_date: "YYYY-MM-DD".
        records: 후보 리스트. grade 가 STRONG/WATCH 인 것만 추천 — 호출자 책임.
        data_dir: settings.data_dir.
    """
    path = _path_for(data_dir, decision_date)
    payload = {
        "decision_date": decision_date,
        "records": [asdict(r) for r in records],
    }
    _atomic_write(path, payload)
    logger.info(
        f"[paper_trade] {decision_date}: {len(records)}건 기록 → {path}"
    )


def record_open_result(
    decision_date: str,
    code: str,
    *,
    data_dir: Path,
    open_price: float | None = None,
    intraday_high: float | None = None,
    intraday_close: float | None = None,
    jongbae_exit_action: str | None = None,
) -> bool:
    """다음날 09:30 또는 16:00 호출 — 시초가/오전 결과 추가.

    record_decision 으로 만들어진 record 를 in-place 갱신. 해당 code 가 파일에
    없으면 False 반환 (no-op).

    morning_return_pct 는 자동 계산 (intraday_high 와 decision_price 사용).

    Returns:
        True 면 갱신 성공, False 면 record 없음 (decision_date 파일 없거나 code 미스).
    """
    path = _path_for(data_dir, decision_date)
    data = _load(path)
    if not data:
        return False
    rows = data.get("records") or []
    target = next((r for r in rows if r.get("code") == code), None)
    if target is None:
        return False

    if open_price is not None:
        target["open_price"] = float(open_price)
        decision_price = float(target.get("decision_price") or 0)
        if decision_price > 0:
            target["open_gap_pct"] = (
                (float(open_price) - decision_price) / decision_price * 100.0
            )
    if intraday_high is not None:
        target["intraday_high"] = float(intraday_high)
        decision_price = float(target.get("decision_price") or 0)
        if decision_price > 0:
            target["morning_return_pct"] = (
                (float(intraday_high) - decision_price) / decision_price * 100.0
            )
    if intraday_close is not None:
        target["intraday_close"] = float(intraday_close)
    if jongbae_exit_action is not None:
        target["jongbae_exit_action"] = jongbae_exit_action

    _atomic_write(path, data)
    return True


def load_records(
    data_dir: Path,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[PaperTradeRecord]:
    """누적 records 로드. date 필터 ISO 문자열 비교 (lexicographic).

    YYYY-MM-DD 포맷이면 lexicographic 비교가 시간순.
    """
    pt_dir = data_dir / "paper_trade"
    if not pt_dir.exists():
        return []
    out: list[PaperTradeRecord] = []
    for path in sorted(pt_dir.glob("*.json")):
        date_str = path.stem
        if date_from and date_str < date_from:
            continue
        if date_to and date_str > date_to:
            continue
        data = _load(path)
        for row in data.get("records") or []:
            out.append(PaperTradeRecord.from_dict(row))
    return out


def compute_summary(records: list[PaperTradeRecord]) -> dict[str, Any]:
    """누적 records → mini-stat (ritual 2 gate criteria 입력값).

    Returns:
        {
            "n_total": int,
            "n_strong": int,
            "n_watch": int,
            "n_with_result": int,                # open_price not None
            "avg_morning_return_strong_pct": float,  # STRONG 평균 시초~오전고 수익
            "avg_morning_return_watch_pct": float,
            "spearman_score_vs_morning_return": float,  # ρ (gate ≥ 0.3)
        }

        표본 부족(n_with_result < 2) 시 통계 항목 NaN.
    """
    out: dict[str, Any] = {
        "n_total": len(records),
        "n_strong": sum(1 for r in records if r.grade == "STRONG"),
        "n_watch": sum(1 for r in records if r.grade == "WATCH"),
        "n_with_result": sum(1 for r in records if r.morning_return_pct is not None),
        "avg_morning_return_strong_pct": float("nan"),
        "avg_morning_return_watch_pct": float("nan"),
        "spearman_score_vs_morning_return": float("nan"),
    }

    strong_returns = [
        r.morning_return_pct for r in records
        if r.grade == "STRONG" and r.morning_return_pct is not None
    ]
    if strong_returns:
        out["avg_morning_return_strong_pct"] = sum(strong_returns) / len(strong_returns)
    watch_returns = [
        r.morning_return_pct for r in records
        if r.grade == "WATCH" and r.morning_return_pct is not None
    ]
    if watch_returns:
        out["avg_morning_return_watch_pct"] = sum(watch_returns) / len(watch_returns)

    paired = [
        (r.score, r.morning_return_pct) for r in records
        if r.morning_return_pct is not None
    ]
    if len(paired) >= 2:
        out["spearman_score_vs_morning_return"] = _spearman(paired)

    return out


def _spearman(pairs: list[tuple[float, float]]) -> float:
    """순위 기반 Spearman ρ. scipy 의존 없이 단순 구현.

    동순위 ties 는 평균 순위. n ≥ 2 가정.
    """
    n = len(pairs)
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    rx = _rank(xs)
    ry = _rank(ys)
    mean_x = sum(rx) / n
    mean_y = sum(ry) / n
    num = sum((a - mean_x) * (b - mean_y) for a, b in zip(rx, ry))
    var_x = sum((a - mean_x) ** 2 for a in rx)
    var_y = sum((b - mean_y) ** 2 for b in ry)
    denom = (var_x * var_y) ** 0.5
    if denom == 0:
        return float("nan")
    return num / denom


def _rank(values: list[float]) -> list[float]:
    """동순위 평균 순위 부여."""
    sorted_idx = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(values):
        j = i
        while j + 1 < len(values) and values[sorted_idx[j + 1]] == values[sorted_idx[i]]:
            j += 1
        avg = (i + j) / 2 + 1  # 1-based 평균
        for k in range(i, j + 1):
            ranks[sorted_idx[k]] = avg
        i = j + 1
    return ranks
