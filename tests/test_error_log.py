"""src.ops.error_log — 파이프라인 에러 집계 채널 단위 테스트."""
from __future__ import annotations

import json
from datetime import date, datetime

from src.config import KST
from src.ops.error_log import format_error_lines, read_errors, record_error


def test_record_and_read_round_trip(tmp_path):
    when = datetime(2026, 5, 6, 11, 23, 45, tzinfo=KST)
    record_error(tmp_path, "스냅샷 11:00", "API 타임아웃", when=when)

    entries = read_errors(tmp_path, when.date())
    assert len(entries) == 1
    assert entries[0]["ts"] == "11:23:45"
    assert entries[0]["source"] == "스냅샷 11:00"
    assert entries[0]["msg"] == "API 타임아웃"


def test_record_appends_multiple(tmp_path):
    d = datetime(2026, 5, 6, 9, 30, tzinfo=KST)
    record_error(tmp_path, "모닝", "첫 번째", when=d)
    record_error(tmp_path, "사후", "두 번째", when=d.replace(hour=16))

    entries = read_errors(tmp_path, d.date())
    assert [e["source"] for e in entries] == ["모닝", "사후"]


def test_read_missing_file_returns_empty(tmp_path):
    assert read_errors(tmp_path, date(2026, 5, 6)) == []


def test_record_strips_newlines_in_message(tmp_path):
    """다행 메시지는 한 줄로 정규화 (JSONL 깨짐 방지)."""
    when = datetime(2026, 5, 6, 14, 50, tzinfo=KST)
    record_error(tmp_path, "결정", "에러:\n트레이스백\n  line ...", when=when)

    entries = read_errors(tmp_path, when.date())
    assert "\n" not in entries[0]["msg"]


def test_read_skips_corrupt_lines(tmp_path):
    """깨진 라인은 무시하고 나머지만 반환."""
    when = datetime(2026, 5, 6, 9, 0, tzinfo=KST)
    record_error(tmp_path, "모닝", "ok", when=when)
    # 손상된 라인 끼워넣기
    path = tmp_path / "errors" / "2026-05-06.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write("not-json-line\n")
        f.write(json.dumps({"ts": "10:00:00", "source": "x", "msg": "valid"}) + "\n")

    entries = read_errors(tmp_path, when.date())
    assert [e["msg"] for e in entries] == ["ok", "valid"]


def test_format_error_lines():
    lines = format_error_lines([
        {"ts": "11:23:45", "source": "스냅샷 11:00", "msg": "API 타임아웃"},
        {"ts": "16:00:01", "source": "사후", "msg": "시간외 단일가 조회 실패: foo"},
    ])
    assert lines[0] == "[11:23:45] 스냅샷 11:00: API 타임아웃"
    assert lines[1] == "[16:00:01] 사후: 시간외 단일가 조회 실패: foo"
