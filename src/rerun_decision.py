"""저장된 14:50 스냅샷으로 결정 레포트 재발송 (수동 CLI).

목적: 14:50 cron 이 이미 돈 후 코드 fix 를 적용하려면 다음 영업일 14:50 까지
기다려야 했음 → 오늘 데이터로 fix 적용된 레포트를 즉시 재생성/재발송.
스냅샷은 `data/snapshots/YYYY-MM-DD/14_50.parquet` 에서 로드, 나머지 라이브
KIS 호출 (`fetch_quote` OHLCV 보강 / `compute_market_stats` 시장 국면 /
호가·체결·투자자) 은 **호출 시점 (after-hours)** 기준 데이터.

⚠ 주의:
    - OHLCV / 시장 국면 / 14:50 시그널은 **현 시각 KIS 응답** — 14:50 당시 값이
      아님. 시초 청산 시점에는 차이 미미하나 분석용으로만.
    - 텔레그램 발송됨. preview 만 원하면 `DRY_RUN=1` 환경변수.
    - 14:50 정시 cron 의 저장본 (`data/reports/.../14_50_decision.md`) 을 덮어씀.

사용법:
    python -m src.rerun_decision                 # 오늘 14:50 스냅샷으로 재발송
    python -m src.rerun_decision --date 2026-05-19 --snapshot 14:50
    DRY_RUN=1 python -m src.rerun_decision       # 발송 X, 로그만
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime

from loguru import logger

from src.config import KST, load_settings, now_kst
from src.data.snapshot import load_snapshot
from src.kis.client import KISClient
from src.notify.dispatcher import Dispatcher


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="결정 레포트 수동 재실행 (저장된 스냅샷 기반)")
    parser.add_argument(
        "--date", default=None,
        help="기준 날짜 YYYY-MM-DD (기본: 오늘 KST)",
    )
    parser.add_argument(
        "--snapshot", default="14:50",
        help="스냅샷 시각 HH:MM (기본: 14:50)",
    )
    args = parser.parse_args(argv)

    target_date: date = (
        datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else now_kst().date()
    )

    settings = load_settings()
    snapshot_df = load_snapshot(settings.data_dir, target_date, args.snapshot)
    if snapshot_df.empty:
        logger.error(
            f"스냅샷 없음: {target_date} {args.snapshot} — "
            f"data/snapshots/{target_date}/{args.snapshot.replace(':','_')}.parquet"
        )
        return 1
    logger.info(f"스냅샷 로드: {target_date} {args.snapshot}, {len(snapshot_df)}종목")

    # scheduler 의 dashboard cache (master/theme/daily) 로드 — _send_decision_report
    # 이 직접 참조하진 않지만 향후 fetch_volume_rank master_df 필터 연계 시 필요.
    from src.scheduler import _load_dashboard_data, _send_decision_report
    _load_dashboard_data(settings)

    dispatcher = Dispatcher(settings)
    hh, mm = args.snapshot.split(":")
    snap_dt = datetime(
        target_date.year, target_date.month, target_date.day,
        int(hh), int(mm), tzinfo=KST,
    )

    with KISClient(settings) as client:
        _send_decision_report(snapshot_df, settings, dispatcher, snap_dt, client=client)

    logger.info(f"결정 레포트 재발송 완료 ({target_date} {args.snapshot})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
