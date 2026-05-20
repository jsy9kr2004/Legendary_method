"""tick_log parquet 컬럼명 마이그레이션 (2026-05-21).

옛: trigger_b1_take_profit_1, trigger_c1_vp_below_100, ...
새: trigger_p1_take_profit_1, trigger_e1_vp_below_100, ...

5/18 ~ 5/20 기존 parquet 파일에 적용. 미래 데이터는 새 컬럼명으로 직접 생성.

사용:
    python -m scripts.migrate_tick_log_columns
    python -m scripts.migrate_tick_log_columns --dry-run
    python -m scripts.migrate_tick_log_columns --files data/tick_logs/2026-05-20.parquet

idempotent — 이미 새 컬럼명이면 skip.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

COLUMN_RENAMES = {
    "trigger_b1_take_profit_1": "trigger_p1_take_profit_1",
    "trigger_b2_take_profit_2": "trigger_p2_take_profit_2",
    "trigger_b3_trailing": "trigger_p3_trailing",
    "trigger_c1_vp_below_100": "trigger_e1_vp_below_100",
    "trigger_c2_bearish_divergence": "trigger_e2_bearish_divergence",
    "trigger_c3_vol_drain": "trigger_e3_vol_drain",
    "trigger_c4_bearish_candle": "trigger_e4_bearish_candle",
    "trigger_c5_vi_failure": "trigger_e5_vi_failure",
}


def migrate_parquet(path: Path, dry_run: bool = False) -> bool:
    df = pd.read_parquet(path)
    needs_rename = {old: new for old, new in COLUMN_RENAMES.items() if old in df.columns}
    if not needs_rename:
        print(f"[skip] {path} (already new schema)")
        return False
    print(f"[{'dry-run' if dry_run else 'migrate'}] {path}: {len(needs_rename)} columns → {list(needs_rename.values())}")
    if not dry_run:
        df = df.rename(columns=needs_rename)
        df.to_parquet(path, index=False)
        print(f"  wrote {len(df)} rows")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--files", nargs="+", help="specific parquet files (default: data/tick_logs/*.parquet)")
    args = ap.parse_args()

    if args.files:
        targets = [Path(f) for f in args.files]
    else:
        targets = sorted(Path("data/tick_logs").glob("*.parquet"))

    if not targets:
        print("no tick_log parquet files found")
        return 0

    migrated = 0
    for path in targets:
        if migrate_parquet(path, dry_run=args.dry_run):
            migrated += 1
    print(f"\nTotal: {migrated}/{len(targets)} migrated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
