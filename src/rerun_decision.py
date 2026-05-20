"""결정 레포트 수동 재실행 (수동 CLI).

목적: 14:50 cron 이 이미 돈 후 코드 fix 를 적용하려면 다음 영업일 14:50 까지
기다려야 했음 → 오늘 데이터로 fix 적용된 레포트를 즉시 재생성/재발송.

두 가지 모드:

1. **fresh (기본)** — 호출 시점에 `fetch_volume_rank` 새로 호출.
   거래대금 순위 + OHLCV 가 NOW 기준. 정상 14:50 cron 과 동일한 파이프라인
   이지만 호출 시각의 시장 상태로 동작.
   사용 케이스: 장 중 임의 시점에 "지금 후보가 뭐지?" 확인. 또는 14:50 cron
   이후 코드 fix 가 들어가서 같은 룰로 즉시 재계산하고 싶을 때.

2. **--from-saved** — 저장된 스냅샷 parquet 로드.
   거래대금 순위 + OHLCV 는 저장 시점 (보통 14:50) 값. 단, fetch_quote
   보강 / compute_market_stats / 14:50 시그널 (호가·체결·투자자) 은 여전히
   호출 시점 KIS 응답이라 미세 차이는 있음.
   사용 케이스: 14:50 정시 스냅샷 그대로 후보 필터 룰 변경 효과 검증.

공통 동작 (`scheduler._send_decision_report` 직접 호출):
    - Eod.Pick v2 (e) 컷, fetch_quote OHLCV 보강, market_stats fallback,
      14:50 시그널 fetch — 모든 fix 자동 적용.
    - 텔레그램 발송됨. preview 만 원하면 `DRY_RUN=1` 환경변수.
    - 정시 cron 저장본 (`data/reports/.../14_50_decision.md`) 을 덮어씀.

사용법:
    python -m src.rerun_decision                    # fresh 스냅샷
    python -m src.rerun_decision --from-saved       # 오늘 14:50 저장본
    python -m src.rerun_decision --from-saved --date 2026-05-19 --snapshot 14:50
    DRY_RUN=1 python -m src.rerun_decision          # 발송 X, 로그만
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime

import pandas as pd
from loguru import logger

from src.config import KST, load_settings, now_kst
from src.data.intraday import fetch_volume_rank
from src.data.snapshot import load_snapshot
from src.kis.client import KISClient
from src.notify.dispatcher import Dispatcher


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="결정 레포트 수동 재실행")
    parser.add_argument(
        "--from-saved", action="store_true",
        help="저장된 스냅샷 parquet 로드 (기본: fresh fetch)",
    )
    parser.add_argument(
        "--date", default=None,
        help="--from-saved 와 함께 기준 날짜 YYYY-MM-DD (기본: 오늘 KST)",
    )
    parser.add_argument(
        "--snapshot", default="14:50",
        help="--from-saved 와 함께 스냅샷 시각 HH:MM (기본: 14:50)",
    )
    parser.add_argument(
        "--top-n", type=int, default=None,
        help="fresh 모드일 때 거래대금 상위 N (기본: 환경변수 LIMIT_UP_WATCH_TOP_N 또는 30)",
    )
    args = parser.parse_args(argv)

    settings = load_settings()

    # scheduler 의 dashboard cache (master/theme/daily) 로드. fetch_volume_rank
    # master_df 필터 / _send_decision_report 의 후속 단계에서 사용.
    from src import scheduler as sched_mod
    sched_mod._load_dashboard_data(settings)

    dispatcher = Dispatcher(settings)
    now = now_kst()

    with KISClient(settings) as client:
        if args.from_saved:
            target_date: date = (
                datetime.strptime(args.date, "%Y-%m-%d").date()
                if args.date else now.date()
            )
            snapshot_df = load_snapshot(settings.data_dir, target_date, args.snapshot)
            if snapshot_df.empty:
                logger.error(
                    f"스냅샷 없음: {target_date} {args.snapshot} — "
                    f"data/snapshots/{target_date}/{args.snapshot.replace(':','_')}.parquet"
                )
                return 1
            logger.info(
                f"[from-saved] {target_date} {args.snapshot} 스냅샷 로드 — "
                f"{len(snapshot_df)}종목"
            )
            hh, mm = args.snapshot.split(":")
            snap_dt = datetime(
                target_date.year, target_date.month, target_date.day,
                int(hh), int(mm), tzinfo=KST,
            )
        else:
            top_n = args.top_n or sched_mod._WATCH_TOP_N
            logger.info(f"[fresh] 호출 시점({now:%H:%M:%S}) 거래대금 상위 {top_n} fetch...")
            snapshot_df = fetch_volume_rank(
                client, top_n=top_n, master_df=sched_mod._dashboard_master_df,
            )
            if snapshot_df.empty:
                logger.error("fetch_volume_rank 빈 응답 — KIS 휴장일/오류 가능. 로그 확인.")
                return 1
            logger.info(f"[fresh] {len(snapshot_df)}종목 수집 완료")
            snap_dt = now

        sched_mod._send_decision_report(snapshot_df, settings, dispatcher, snap_dt, client=client)

    logger.info(f"결정 레포트 재발송 완료 (snap_dt={snap_dt:%Y-%m-%d %H:%M})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
