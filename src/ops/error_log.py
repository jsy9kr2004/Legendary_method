"""파이프라인 에러 집계 채널.

스케줄러의 각 잡/단계에서 발생한 에러를 일자별 JSONL 파일에 append.
사후 레포트(16:00)가 같은 날 파일을 읽어 `[알려진 이슈]` 섹션에 노출.

경로:
    {DATA_DIR}/errors/YYYY-MM-DD.jsonl

각 줄 (JSON):
    {"ts": "HH:MM:SS", "source": "사후", "msg": "market_stats 조회 실패: ..."}

동시성: APScheduler 가 잡을 순차 실행하므로 단순 append-only 로 충분.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from src.config import KST, now_kst


def _errors_path(data_dir, d: date) -> Path:
    return Path(data_dir) / "errors" / f"{d.strftime('%Y-%m-%d')}.jsonl"


def record_error(
    data_dir,
    source: str,
    msg: str,
    when: datetime | None = None,
) -> None:
    """에러 1건을 일자별 JSONL 파일에 append.

    Args:
        data_dir: 설정의 data_dir.
        source: 발생 위치 라벨 (e.g. "사후", "스냅샷", "상한가 알림").
        msg: 에러 메시지 (한 줄 권장).
        when: 발생 시각 (KST). 생략 시 현재.
    """
    when = when or now_kst()
    path = _errors_path(data_dir, when.date())
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": when.strftime("%H:%M:%S"),
        "source": source,
        "msg": msg.replace("\n", " ").strip(),
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_errors(data_dir, d: date) -> list[dict[str, Any]]:
    """그날 기록된 에러 전체 반환 (시각순). 파일 없으면 빈 리스트."""
    path = _errors_path(data_dir, d)
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return entries


def format_error_lines(entries: list[dict[str, Any]]) -> list[str]:
    """사후 레포트에 들어갈 사람-읽는 줄로 변환 ("[HH:MM:SS] source: msg")."""
    return [
        f"[{e.get('ts','??:??:??')}] {e.get('source','?')}: {e.get('msg','')}"
        for e in entries
    ]
