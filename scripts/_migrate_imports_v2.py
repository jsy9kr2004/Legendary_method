"""임시 import path 마이그레이션 (2026-05-21).

src.jongbae.X → src.scalping.score/exit/* 또는 src.overnight/* 또는 src.common/*.
모든 .py 파일에 일괄 적용. 끝나면 본 스크립트 삭제.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# (옛 모듈 path, 새 모듈 path) — 길이 긴 것 먼저 일관성 유지
IMPORT_RENAMES: list[tuple[str, str]] = [
    # 단타 score
    ("src.scalping.score.grader", "src.scalping.score.grader"),
    ("src.scalping.score.vp", "src.scalping.score.vp"),
    ("src.scalping.score.accel", "src.scalping.score.accel"),
    ("src.scalping.score.candle", "src.scalping.score.candle"),
    ("src.scalping.score.divergence", "src.scalping.score.divergence"),
    ("src.scalping.score.thresholds", "src.scalping.score.thresholds"),
    # 단타 exit
    ("src.scalping.exit.triggers", "src.scalping.exit.triggers"),
    # 단타 paper trade
    ("src.scalping.paper_trade", "src.scalping.paper_trade"),
    # 종배
    ("src.overnight.gap_stats", "src.overnight.gap_stats"),
    ("src.overnight.candidates", "src.overnight.candidates"),
    ("src.overnight.sizing", "src.overnight.sizing"),
    ("src.overnight.exit", "src.overnight.exit"),
    # 공통
    ("src.common.theme", "src.common.theme"),
    ("src.common.limit_up", "src.common.limit_up"),
]


def migrate_file(path: Path, dry_run: bool = False) -> int:
    text = path.read_text(encoding="utf-8")
    original = text
    n = 0
    for old, new in IMPORT_RENAMES:
        # word boundary 로 안전화 — `src.scalping.score.grader` 가 `src.jongbae.grader_thresholds` 의 prefix 일 수 있음
        # 단 위 매핑에선 정확한 1:1 이라 단순 replace 도 안전.
        # 하지만 끝 단어 boundary 추가
        pattern = re.escape(old) + r"(?![A-Za-z0-9_])"
        new_text, count = re.subn(pattern, new, text)
        if count > 0:
            n += count
            text = new_text
    if text != original:
        if dry_run:
            print(f"[dry-run] {path}: {n}")
        else:
            path.write_text(text, encoding="utf-8")
            print(f"[updated] {path}: {n}")
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    total = 0
    # 모든 .py 파일 처리
    for root in ["src", "tests", "scripts"]:
        for f in Path(root).rglob("*.py"):
            total += migrate_file(f, dry_run=args.dry_run)
    print(f"\nTotal: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
