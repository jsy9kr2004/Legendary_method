"""저장된 레포트 산출물 읽기 레이어 (read-only).

파일 레이아웃:
    마크다운     : {DATA_DIR}/reports/YYYY-MM-DD/{HH_MM}_{label}.md
    결정 구조화  : {DATA_DIR}/decisions/YYYY-MM-DD.json
"""
from __future__ import annotations

import json
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import pytz

KST = pytz.timezone("Asia/Seoul")

# 웹에 노출하는 레포트 종류 (사용자 선택: 우선 결정+사후. 탭 확장 시 여기 추가).
REPORT_LABELS: dict[str, str] = {
    "decision": "결정 레포트",
    "afterhours": "사후 레포트",
}


def _reports_root(data_dir: Path) -> Path:
    return Path(data_dir) / "reports"


def _decisions_root(data_dir: Path) -> Path:
    return Path(data_dir) / "decisions"


def _day_dir(data_dir: Path, d: date) -> Path:
    return _reports_root(data_dir) / d.strftime("%Y-%m-%d")


def _md_path(data_dir: Path, d: date, label: str) -> Path | None:
    """해당 일자/라벨의 마크다운 파일. 같은 라벨이 여러 개면 가장 늦은 시각본."""
    day = _day_dir(data_dir, d)
    if not day.is_dir():
        return None
    matches = sorted(day.glob(f"*_{label}.md"))
    return matches[-1] if matches else None


def has_report(data_dir: Path, d: date, label: str) -> bool:
    return _md_path(data_dir, d, label) is not None


def load_markdown(data_dir: Path, d: date, label: str) -> str | None:
    p = _md_path(data_dir, d, label)
    if p is None:
        return None
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return None


def report_mtime(data_dir: Path, d: date, label: str) -> float | None:
    """파일 수정 시각(epoch). 라이브 폴링용 — 값이 바뀌면 클라이언트가 새로고침."""
    p = _md_path(data_dir, d, label)
    if p is None:
        return None
    try:
        return p.stat().st_mtime
    except OSError:
        return None


def available_labels(data_dir: Path, d: date) -> list[str]:
    """해당 일자에 존재하는, 웹 노출 대상 레포트 라벨 (REPORT_LABELS 순서 유지)."""
    return [lbl for lbl in REPORT_LABELS if has_report(data_dir, d, lbl)]


def parse_date(s: str) -> date | None:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def list_dates(data_dir: Path) -> list[date]:
    """결정 또는 사후 레포트가 하나라도 있는 일자 (최신순)."""
    root = _reports_root(data_dir)
    out: list[date] = []
    if not root.is_dir():
        return out
    for child in root.iterdir():
        if not child.is_dir():
            continue
        d = parse_date(child.name)
        if d is None:
            continue
        if any(child.glob(f"*_{lbl}.md") for lbl in REPORT_LABELS):
            out.append(d)
    return sorted(out, reverse=True)


def latest_date(data_dir: Path) -> date | None:
    dates = list_dates(data_dir)
    return dates[0] if dates else None


def load_decision_payload(data_dir: Path, d: date) -> dict[str, Any] | None:
    """결정 구조화 JSON 전체 ({report_date, report_time, market, leading_themes, candidates})."""
    p = _decisions_root(data_dir) / f"{d.strftime('%Y-%m-%d')}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def is_market_window(now: datetime | None = None) -> bool:
    """장중~사후 발송 시간대 (09:00~16:40 KST 평일) — 라이브 폴링 활성 구간.

    이 구간에만 클라이언트가 mtime 폴링 → 그 외엔 정적이라 폴링 불필요.
    """
    n = now or datetime.now(KST)
    if n.weekday() >= 5:  # 주말
        return False
    return time(9, 0) <= n.timetz().replace(tzinfo=None) <= time(16, 40)
