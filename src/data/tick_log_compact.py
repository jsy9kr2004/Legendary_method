"""tick_logs / trades jsonl → parquet 변환 (Phase 1 사후 cron).

운영 중엔 jsonl 로 append (crash 손실 ≤ 1 tick). 16:00 사후 또는 사용자 명령으로
parquet 변환. parquet 압축으로 일별 ~5MB. 분석은 pandas/duckdb 로 parquet 쿼리.

사용:
    python -m src.data.tick_log_compact 2026-05-15           # 특정 일자
    python -m src.data.tick_log_compact --yesterday          # 어제
    python -m src.data.tick_log_compact --all                # data/tick_logs/raw 의 모든 jsonl

원본 jsonl 은 변환 후 옵션에 따라 보존/삭제. 기본 보존 (안전망).
"""
from __future__ import annotations

import argparse
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
from loguru import logger


def _data_dir() -> Path:
    return Path(os.getenv("DATA_DIR", "data"))


def compact_tick_logs(day: date, *, delete_raw: bool = False) -> Path | None:
    """`data/tick_logs/raw/YYYY-MM-DD.jsonl` → `data/tick_logs/YYYY-MM-DD.parquet`.

    Args:
        day: 변환할 일자.
        delete_raw: True 면 변환 후 원본 jsonl 삭제. 기본 False (안전망).

    Returns:
        생성된 parquet 경로. raw 파일 없으면 None.
    """
    raw_path = _data_dir() / "tick_logs" / "raw" / f"{day.isoformat()}.jsonl"
    if not raw_path.exists():
        logger.info(f"[tick_log_compact] {raw_path} 없음 — skip")
        return None

    out_dir = _data_dir() / "tick_logs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{day.isoformat()}.parquet"

    # code 6자리 zero-padded 보존 — read_json 의 type inference 가 "091340" → 91340
    # int 로 떨어뜨려서 leading zero 손실. parquet 도 동일 오염. dtype 강제.
    df = pd.read_json(raw_path, lines=True, dtype={"code": str})
    rows = len(df)
    df.to_parquet(out_path, index=False, compression="snappy")

    size_mb = out_path.stat().st_size / 1024 / 1024
    logger.info(
        f"[tick_log_compact] {raw_path.name} → {out_path.name} "
        f"({rows:,} rows, {size_mb:.1f} MB)"
    )
    if delete_raw:
        raw_path.unlink()
        logger.info(f"[tick_log_compact] {raw_path} 삭제")
    return out_path


def compact_trades(day: date, *, delete_raw: bool = False) -> Path | None:
    """매수/매도 이벤트 jsonl → parquet. tick_logs 와 동일 패턴."""
    raw_path = _data_dir() / "trades" / f"{day.isoformat()}.jsonl"
    if not raw_path.exists():
        logger.info(f"[tick_log_compact] {raw_path} 없음 — skip")
        return None

    out_dir = _data_dir() / "trades"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{day.isoformat()}.parquet"

    df = pd.read_json(raw_path, lines=True, dtype={"code": str})
    rows = len(df)
    df.to_parquet(out_path, index=False, compression="snappy")
    size_mb = out_path.stat().st_size / 1024 / 1024
    logger.info(
        f"[tick_log_compact] trades {raw_path.name} → {out_path.name} "
        f"({rows} rows, {size_mb:.2f} MB)"
    )
    if delete_raw:
        raw_path.unlink()
    return out_path


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("date", nargs="?", type=_parse_date, default=None,
                       help="YYYY-MM-DD")
    group.add_argument("--yesterday", action="store_true")
    group.add_argument("--all", action="store_true",
                       help="data/tick_logs/raw 의 모든 jsonl 변환")
    parser.add_argument("--delete-raw", action="store_true",
                        help="변환 후 원본 jsonl 삭제 (기본 보존)")
    args = parser.parse_args()

    if args.yesterday:
        target = date.today() - timedelta(days=1)
        compact_tick_logs(target, delete_raw=args.delete_raw)
        compact_trades(target, delete_raw=args.delete_raw)
    elif args.all:
        raw_dir = _data_dir() / "tick_logs" / "raw"
        if not raw_dir.exists():
            logger.warning(f"{raw_dir} 없음")
            return 0
        for raw_file in sorted(raw_dir.glob("*.jsonl")):
            day = _parse_date(raw_file.stem)
            compact_tick_logs(day, delete_raw=args.delete_raw)
            compact_trades(day, delete_raw=args.delete_raw)
    else:
        compact_tick_logs(args.date, delete_raw=args.delete_raw)
        compact_trades(args.date, delete_raw=args.delete_raw)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
