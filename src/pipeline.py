"""종배 시그널 통합 파이프라인.

leading_theme → candidates → historical → sizing → report → (dispatch)

실행:
    python -m src.pipeline                          # 오늘, 실제 데이터, dry-run
    python -m src.pipeline --date 2025-05-04        # 특정 날짜
    python -m src.pipeline --date 2025-05-04 --demo # 제룡전기 mock 데이터
    python -m src.pipeline --send                   # 실제 텔레그램 발송

옵션:
    --date YYYY-MM-DD   기준 날짜 (기본: 오늘)
    --snapshot HH:MM    스냅샷 시각 (기본: 14:50)
    --demo              KIS API 없이 mock 데이터로 실행
    --send              텔레그램/이메일 실제 발송 (없으면 stdout만)
    --save              레포트를 파일로 저장 (data/reports/)
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytz
from loguru import logger

from src.config import KST, load_settings
from src.data.snapshot import load_snapshot
from src.data.storage import read_daily_ohlcv, read_naver_themes
from src.jongbae.candidates import accepted_candidates, extract_candidates
from src.jongbae.historical import (
    close_position,
    has_enough_samples,
    historical_4layer,
    pick_sizing_layer,
)
from src.jongbae.leading_theme import (
    codes_in_leading_themes,
    identify_leading_themes,
)
from src.jongbae.sizing import compute_sizing
from src.logging_setup import setup_logging
from src.report.decision import build_decision_report, save_decision_report, split_messages


def run_pipeline(
    target_date: date,
    snapshot_time: str = "14:50",
    data_dir: Path | None = None,
    demo: bool = False,
    send: bool = False,
    save: bool = False,
) -> str:
    """파이프라인 실행 → 결정 레포트 텍스트 반환.

    Args:
        target_date: 기준 날짜
        snapshot_time: 스냅샷 시각 ('14:50' 등)
        data_dir: 데이터 디렉토리. None이면 Settings.data_dir 사용.
        demo: True이면 mock 데이터 사용 (KIS API 불필요)
        send: True이면 텔레그램 실제 발송
        save: True이면 레포트 파일 저장

    Returns:
        결정 레포트 마크다운 문자열.
    """
    settings = load_settings()
    if data_dir is None:
        data_dir = settings.data_dir

    # ── 1. 데이터 로드 ────────────────────────────────────────────────────
    if demo:
        logger.info(f"[DEMO] {target_date} mock 데이터 생성 중...")
        from src.demo_fixtures import make_daily_ohlcv, make_snapshot, make_theme_mapping
        snapshot_df = make_snapshot(target_date)
        daily_ohlcv = make_daily_ohlcv(target_date, lookback_days=260)
        theme_df = make_theme_mapping(target_date)
        logger.info(
            f"[DEMO] 스냅샷 {len(snapshot_df)}종목 / "
            f"일봉 {len(daily_ohlcv)}행 / 테마 {len(theme_df)}행"
        )
    else:
        hh, mm = snapshot_time.split(":")
        snap_dt = datetime(target_date.year, target_date.month, target_date.day,
                           int(hh), int(mm), tzinfo=KST)
        snapshot_df = load_snapshot(data_dir, target_date, snapshot_time)
        if snapshot_df.empty:
            logger.warning(f"스냅샷 없음: {target_date} {snapshot_time} — --demo 옵션을 사용하세요")
        daily_ohlcv = read_daily_ohlcv(data_dir)
        theme_df = read_naver_themes(data_dir)

    if snapshot_df.empty:
        return f"⚠ [{target_date}] 스냅샷 데이터 없음. --demo 옵션으로 실행하거나 스케줄러로 데이터를 수집하세요."

    # ── 2. 주도테마 식별 (R3) ─────────────────────────────────────────────
    leading_themes = identify_leading_themes(snapshot_df, theme_df, threshold=3)
    leading_codes = codes_in_leading_themes(leading_themes)

    if not leading_themes:
        logger.info("주도테마 없음 — 후보 없이 레포트 생성")

    # ── 3. 종배 후보 추출 (R4) ─────────────────────────────────────────────
    candidates_df = extract_candidates(snapshot_df, leading_codes)
    accepted = accepted_candidates(candidates_df)

    # ── 4. Historical + Sizing (R5, R6) ───────────────────────────────────
    candidates_with_stats: list[dict[str, Any]] = []
    for _, row in accepted.iterrows():
        code = str(row["code"])
        close = int(row.get("price", 0))
        high = int(row.get("intraday_high", close))
        # 일중 저가는 KIS API stck_lwpr 에서 받음 (H1 수정).
        # 스냅샷에 없거나 0이면 보수적 추정값 사용.
        low_raw = int(row.get("intraday_low", 0) or 0)
        low = low_raw if low_raw > 0 else int(close * 0.85)

        cp = close_position(
            open_p=float(row.get("prev_close", close)),
            high=float(high),
            low=float(low),
            close=float(close),
        )

        layers = historical_4layer(daily_ohlcv, today_close_pos=cp, today=target_date)
        sizing_layer_name, sizing_stats = pick_sizing_layer(layers)

        # R4 (c): 모든 layer 가 n<5 면 후보 제외 (표본 부족)
        if not has_enough_samples(sizing_stats):
            logger.info(f"[파이프라인] {code} R4(c) 표본부족 제외 (n<5)")
            continue

        # 테마 조회
        if not theme_df.empty:
            themes = theme_df[theme_df["code"] == code]["theme"].tolist()
        else:
            themes = []

        c: dict[str, Any] = row.to_dict()
        c["themes"] = themes
        c["layers"] = layers
        c["sizing_layer"] = sizing_layer_name
        c["sizing_stats"] = sizing_stats
        candidates_with_stats.append(c)

    # 사이징 계산
    sizing_results = compute_sizing(candidates_with_stats)
    for i, c in enumerate(candidates_with_stats):
        c["sizing"] = {
            "kelly":  sizing_results["kelly"][i],
            "sharpe": sizing_results["sharpe"][i],
            "equal":  sizing_results["equal"][i],
        }

    # ── 5. 레포트 생성 ────────────────────────────────────────────────────
    hh2, mm2 = snapshot_time.split(":")
    snap_dt = datetime(
        target_date.year, target_date.month, target_date.day,
        int(hh2), int(mm2), tzinfo=KST,
    )
    report = build_decision_report(leading_themes, candidates_with_stats, snap_dt)

    # ── 6. 저장 ───────────────────────────────────────────────────────────
    if save:
        save_decision_report(report, data_dir, snap_dt)
        logger.info(f"레포트 저장: {data_dir}/reports/{target_date}/{hh2}_{mm2}_decision.md")

    # ── 7. 발송 ───────────────────────────────────────────────────────────
    if send:
        from src.notify.dispatcher import Dispatcher
        dispatcher = Dispatcher(settings)
        parts = split_messages(report)
        dispatcher.send_decision(parts)

    return report


def _print_report(report: str) -> None:
    """레포트를 터미널에 보기 좋게 출력."""
    print("\n" + "=" * 60)
    print(report)
    print("=" * 60 + "\n")

    # 간단한 통계 출력
    lines = report.split("\n")
    candidate_lines = [l for l in lines if l.startswith("▣")]
    layer3_lines = [l for l in lines if "Layer 3" in l and "n=" in l]
    sizing_lines = [l for l in lines if "Kelly" in l or "Sharpe" in l]

    print(f"[요약]")
    print(f"  종배 후보: {len(candidate_lines)}종목")
    for cl in candidate_lines:
        print(f"  {cl}")
    for ll in layer3_lines:
        print(f"  {ll.strip()}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="종배 시그널 파이프라인")
    parser.add_argument("--date", default=None,
                        help="기준 날짜 YYYY-MM-DD (기본: 오늘)")
    parser.add_argument("--snapshot", default="14:50",
                        help="스냅샷 시각 HH:MM (기본: 14:50)")
    parser.add_argument("--demo", action="store_true",
                        help="mock 데이터로 실행 (KIS API 불필요)")
    parser.add_argument("--send", action="store_true",
                        help="텔레그램 실제 발송")
    parser.add_argument("--save", action="store_true",
                        help="레포트 파일 저장")
    args = parser.parse_args(argv)

    settings = load_settings()
    setup_logging(settings)

    if args.date:
        try:
            target = date.fromisoformat(args.date)
        except ValueError:
            print(f"날짜 형식 오류: {args.date} (YYYY-MM-DD 형식 사용)")
            return 1
    else:
        target = date.today()

    logger.info(
        f"파이프라인 시작: date={target} snapshot={args.snapshot} "
        f"demo={args.demo} send={args.send} save={args.save}"
    )

    report = run_pipeline(
        target_date=target,
        snapshot_time=args.snapshot,
        demo=args.demo,
        send=args.send,
        save=args.save,
    )

    _print_report(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
