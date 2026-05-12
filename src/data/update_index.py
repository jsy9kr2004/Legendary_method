"""지수 일봉 (KOSPI/KOSDAQ) 적재 CLI.

실행:
    python -m src.data.update_index                  # incremental 업데이트 (기본)
    python -m src.data.update_index --init           # 3년치 초기 백필
    python -m src.data.update_index --init --years 5 # N년치 초기 백필
    python -m src.data.update_index --code 0001      # KOSPI 만

사용 시기:
    - 최초 1회: --init 으로 historical layer3_strong_mkt 200ma 매칭 사용 가능
      범위 확보 (~3년치 권장).
    - 매일 평일 16:00 이후 cron 또는 ./go start 의 스케줄러 통합.
"""
from __future__ import annotations

import argparse
import sys

from loguru import logger

from src.config import load_settings
from src.data.index import (
    KOSDAQ_CODE,
    KOSPI_CODE,
    init_index_daily,
    update_index_daily,
)
from src.kis.client import KISClient
from src.logging_setup import setup_logging


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="지수 일봉 적재 (KOSPI/KOSDAQ)")
    parser.add_argument("--init", action="store_true",
                        help="초기 백필 (N년치). 기본은 incremental")
    parser.add_argument("--years", type=int, default=3,
                        help="--init 시 백필 햇수 (기본 3)")
    parser.add_argument("--code", choices=[KOSPI_CODE, KOSDAQ_CODE],
                        help="특정 지수만 (기본: 둘 다)")
    args = parser.parse_args(argv)

    settings = load_settings()
    setup_logging(settings)

    codes = (args.code,) if args.code else (KOSPI_CODE, KOSDAQ_CODE)

    with KISClient(settings) as client:
        if args.init:
            logger.info(f"[init_index] {args.years}년치 백필 시작 ({codes})")
            result = init_index_daily(client, settings.data_dir, years=args.years, index_codes=codes)
        else:
            result = update_index_daily(client, settings.data_dir, index_codes=codes)

    for code, n in result.items():
        label = "KOSPI" if code == KOSPI_CODE else "KOSDAQ"
        action = "적재" if args.init else "추가"
        print(f"  {label} ({code}): {n}건 {action}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
