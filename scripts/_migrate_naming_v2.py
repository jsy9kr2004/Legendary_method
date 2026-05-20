"""임시 명명 마이그레이션 스크립트 (2026-05-21).

옛 R 번호 + A/B/C 트리거 → 새 명명 (Buy.*, Exit.*, Eod.*, A1~A5/P1~P3/E1~E5).
docs/ + 다른 텍스트 파일에 일괄 적용. 끝나면 본 스크립트 자체 삭제.

길이 긴 패턴부터 먼저 매칭 (Buy.Position → Buy.Position 이 Buy.Candle → Buy.Candle 보다 먼저).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# (옛, 새) 순서 — 길이 긴 패턴부터.
# word boundary 가 필요한 단순 명령 (Eod.Market → Eod.Market) 은 \b 로 안전화.
RENAMES_LONG_FIRST: list[tuple[str, str, bool]] = [
    # (pattern, replacement, is_regex)
    # === Trigger 컬럼 (코드/데이터) — 가장 명확한 패턴 먼저 ===
    (r"trigger_p1_take_profit_1", "trigger_p1_take_profit_1", True),
    (r"trigger_p2_take_profit_2", "trigger_p2_take_profit_2", True),
    (r"trigger_p3_trailing", "trigger_p3_trailing", True),
    (r"trigger_e1_vp_below_100", "trigger_e1_vp_below_100", True),
    (r"trigger_e2_bearish_divergence", "trigger_e2_bearish_divergence", True),
    (r"trigger_e3_vol_drain", "trigger_e3_vol_drain", True),
    (r"trigger_e4_bearish_candle", "trigger_e4_bearish_candle", True),
    (r"trigger_e5_vi_failure", "trigger_e5_vi_failure", True),
    # === Trigger ID enum 값 ===
    (r"P1_take_profit_1", "P1_take_profit_1", True),
    (r"P2_take_profit_2", "P2_take_profit_2", True),
    (r"P3_trailing", "P3_trailing", True),
    (r"E1_vp_below_100", "E1_vp_below_100", True),
    (r"E2_bearish_divergence", "E2_bearish_divergence", True),
    (r"E3_vol_drain", "E3_vol_drain", True),
    (r"E4_bearish_candle", "E4_bearish_candle", True),
    (r"E5_vi_failure", "E5_vi_failure", True),
    # === R 번호 (긴 것 먼저, word boundary) ===
    (r"\bR14d\b", "Buy.Score.d", True),
    (r"\bR14c\b", "Buy.Score.c", True),
    (r"\bR14b\b", "Buy.Score.b", True),
    (r"\bR14a\b", "Buy.Score.a", True),
    (r"\bR12\.5\b", "Buy.Position", True),
    (r"\bR15\b", "Exit.Triggers", True),
    (r"\bR14\b", "Buy.Score", True),
    (r"\bR13\b", "Buy.Div", True),
    (r"\bR12\b", "Buy.Candle", True),
    (r"\bR11\b", "Buy.Accel", True),
    (r"\bR10\b", "Buy.VP", True),
    (r"\bR9\b", "Monitor", True),
    (r"\bR8\b", "Eod.Exec", True),
    (r"\bR7\b", "Eod.Exit", True),
    (r"\bR6\b", "Eod.Sizing", True),
    (r"\bR5\b", "Eod.GapStats", True),
    (r"\bR4\b", "Eod.Pick", True),
    (r"Theme.Leader", "Theme.Leader", True),  # apostrophe 변형 패턴, word boundary 부적용
    (r"\bR3\b", "Theme", True),
    (r"\bR2\b", "Universe", True),
    (r"\bR1\b", "Eod.Market", True),
    # === 디렉토리 참조 (코드 import path 는 Phase 3 에서 별도 처리) ===
    # docs 안의 link/모듈 경로만 — src/jongbae/ → src/scalping/{score,exit}/ 또는 src/overnight/
    (r"src/jongbae/grader\.py", "src/scalping/score/grader.py", True),
    (r"src/jongbae/exit_triggers\.py", "src/scalping/exit/triggers.py", True),
    (r"src/jongbae/volume_power\.py", "src/scalping/score/vp.py", True),
    (r"src/jongbae/momentum\.py", "src/scalping/score/accel.py", True),
    (r"src/jongbae/candle\.py", "src/scalping/score/candle.py", True),
    (r"src/jongbae/divergence\.py", "src/scalping/score/divergence.py", True),
    (r"src/jongbae/paper_trade\.py", "src/scalping/paper_trade.py", True),
    (r"src/jongbae/historical\.py", "src/overnight/gap_stats.py", True),
    (r"src/jongbae/candidates\.py", "src/overnight/candidates.py", True),
    (r"src/jongbae/sizing\.py", "src/overnight/sizing.py", True),
    (r"src/jongbae/jongbae_exit\.py", "src/overnight/exit.py", True),
    (r"src/jongbae/leading_theme\.py", "src/common/theme.py", True),
    (r"src/jongbae/limit_up\.py", "src/common/limit_up.py", True),
    # config_thresholds 분리 — 일단 단타 thresholds 로 매핑
    (r"src/jongbae/config_thresholds\.py", "src/scalping/score/thresholds.py", True),
    # === docs 링크 ===
    # `scalping-strategy.md` 단독 참조는 컨텍스트별로 다르므로 일단 scalping 으로 매핑
    # (단타 영역이 더 큼). 종배 영역에서 참조하던 곳은 별도 손수정 필요.
    (r"jongbae-strategy\.md", "scalping-strategy.md", True),
    (r"r14-revision-proposal\.md", "buy-score-revision-proposal.md", True),
]


def migrate_file(path: Path, dry_run: bool = False) -> int:
    text = path.read_text(encoding="utf-8")
    original = text
    n_changes = 0
    for pattern, replacement, is_regex in RENAMES_LONG_FIRST:
        if is_regex:
            new_text, count = re.subn(pattern, replacement, text)
        else:
            count = text.count(pattern)
            new_text = text.replace(pattern, replacement)
        if count > 0:
            n_changes += count
            text = new_text
    if text != original:
        if dry_run:
            print(f"[dry-run] {path}: {n_changes} changes")
        else:
            path.write_text(text, encoding="utf-8")
            print(f"[updated] {path}: {n_changes} changes")
    return n_changes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("targets", nargs="+", help="files to migrate")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    total = 0
    for t in args.targets:
        p = Path(t)
        if p.is_dir():
            for f in p.rglob("*.md"):
                total += migrate_file(f, dry_run=args.dry_run)
            for f in p.rglob("*.py"):
                total += migrate_file(f, dry_run=args.dry_run)
        elif p.is_file():
            total += migrate_file(p, dry_run=args.dry_run)
        else:
            print(f"skip (not found): {p}")
    print(f"\nTotal changes: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
