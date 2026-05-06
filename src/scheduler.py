"""장중 스케줄러 데몬.

4시점 스냅샷 수집 (11:00, 13:00, 14:00, 14:50 KST) + 상한가 폴링.

실행:
    python -m src.scheduler

동작:
    - APScheduler BlockingScheduler 기반
    - 장 중 (09:00~15:30) 만 동작 (휴장일 = 빈 응답으로 자연 처리)
    - 상한가 폴링: 09:05~15:25 사이 LIMIT_UP_POLL_INTERVAL_SEC 간격 (기본 60초)
    - 스냅샷 수집 실패 시 에러 로그만 (fail-loud, 프로세스는 유지)
    - 상한가 신규 진입 시 표준 출력 (M4에서 텔레그램 연동)

환경변수:
    LIMIT_UP_POLL_INTERVAL_SEC: 상한가 폴링 간격 (초, 기본 60)
    LIMIT_UP_WATCH_TOP_N: 거래대금 상위 몇 개 종목을 감시할지 (기본 30)
"""
from __future__ import annotations

import os
import signal
import sys

from apscheduler.schedulers.blocking import BlockingScheduler
from loguru import logger

from src.calendar_kr import is_business_day
from src.config import KST, load_settings, now_kst
from src.data.intraday import fetch_volume_rank
from src.data.snapshot import save_snapshot
from src.jongbae.limit_up import detect_new_limit_up, filter_limit_up_from_snapshot
from src.kis.client import KISClient
from src.logging_setup import setup_logging

_SNAPSHOT_TIMES = ["11:00", "13:00", "14:00", "14:50"]

_POLL_INTERVAL_SEC = int(os.getenv("LIMIT_UP_POLL_INTERVAL_SEC", "60"))
_WATCH_TOP_N = int(os.getenv("LIMIT_UP_WATCH_TOP_N", "30"))

_already_limit_up: set[str] = set()
_watch_codes: list[str] = []


def _collect_snapshot(client: KISClient, data_dir, label: str) -> None:
    """거래대금 순위 스냅샷 수집 및 저장."""
    dt = now_kst()
    if not is_business_day(dt.date()):
        logger.debug(f"[스냅샷] {label} 스킵 — 주말/휴장일 ({dt.date()})")
        return
    logger.info(f"[스냅샷] {label} 수집 시작 ({dt.strftime('%H:%M:%S')})")
    try:
        df = fetch_volume_rank(client, top_n=_WATCH_TOP_N)
        if df.empty:
            logger.warning(f"[스냅샷] {label}: 데이터 없음 (휴장일 또는 API 오류)")
            return
        path = save_snapshot(df, data_dir, dt)
        logger.info(f"[스냅샷] {label}: {len(df)}종목 저장 → {path}")

        # 감시 종목 목록 갱신 (14:50 스냅샷에서 상한가 폴링 대상 업데이트)
        global _watch_codes
        _watch_codes = df["code"].tolist()

        # 스냅샷에서 바로 상한가 종목 체크
        lup_df = filter_limit_up_from_snapshot(df)
        if not lup_df.empty:
            for _, row in lup_df.iterrows():
                code = str(row["code"])
                if code not in _already_limit_up:
                    _already_limit_up.add(code)
                    logger.info(
                        f"[상한가] {row.get('name', code)}({code}) "
                        f"현재가={row.get('price')} "
                        f"수익률={row.get('daily_return', 0):.1f}%"
                    )
    except Exception as e:
        logger.error(f"[스냅샷] {label} 수집 실패: {e}")


def _poll_limit_up(client: KISClient) -> None:
    """감시 종목 상한가 폴링."""
    global _already_limit_up, _watch_codes
    if not is_business_day(now_kst().date()):
        return
    if not _watch_codes:
        return
    try:
        new_entries, _already_limit_up = detect_new_limit_up(
            client, _watch_codes, _already_limit_up
        )
        if new_entries:
            # M4에서 텔레그램 연동. 현재는 로그만.
            for entry in new_entries:
                logger.info(
                    f"[상한가 신규] {entry.get('name', '')}({entry.get('code')}) "
                    f"현재가={entry.get('price')} "
                    f"수익률={entry.get('daily_return', 0):.1f}%"
                )
    except Exception as e:
        logger.error(f"[상한가 폴링] 오류: {e}")


def run() -> None:
    settings = load_settings()
    setup_logging(settings)

    logger.info("장중 스케줄러 시작")
    logger.info(f"  API 모드: {settings.kis_api_mode}")
    logger.info(f"  DATA_DIR: {settings.data_dir}")
    logger.info(f"  상한가 폴링 간격: {_POLL_INTERVAL_SEC}초")

    client = KISClient(settings)

    scheduler = BlockingScheduler(timezone=KST)

    # 4시점 스냅샷 잡 등록
    for t in _SNAPSHOT_TIMES:
        hh, mm = t.split(":")
        scheduler.add_job(
            _collect_snapshot,
            trigger="cron",
            hour=int(hh),
            minute=int(mm),
            args=[client, settings.data_dir, t],
            id=f"snapshot_{t.replace(':', '')}",
            name=f"스냅샷 {t}",
            misfire_grace_time=300,
        )

    # 상한가 폴링 잡 (09:05~15:25, interval)
    scheduler.add_job(
        _poll_limit_up,
        trigger="interval",
        seconds=_POLL_INTERVAL_SEC,
        args=[client],
        id="limit_up_poll",
        name="상한가 폴링",
        start_date=now_kst().replace(hour=9, minute=5, second=0, microsecond=0),
        end_date=now_kst().replace(hour=15, minute=25, second=0, microsecond=0),
        misfire_grace_time=30,
    )

    def _shutdown(signum, frame):
        logger.info("종료 시그널 수신 — 스케줄러 셧다운")
        scheduler.shutdown(wait=False)
        client.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        scheduler.start()
    except Exception as e:
        logger.error(f"스케줄러 오류: {e}")
        client.close()
        raise


if __name__ == "__main__":
    run()
