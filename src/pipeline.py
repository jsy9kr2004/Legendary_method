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
from src.overnight.candidates import accepted_candidates, extract_candidates
from src.overnight.gap_stats import (
    close_position,
    has_enough_samples,
    historical_4layer,
    pick_sizing_layer,
)
from src.common.theme import (
    codes_in_leading_themes,
    identify_leading_themes,
)
from src.overnight.sizing import compute_sizing
from src.logging_setup import setup_logging
from src.report.decision import (
    build_decision_report,
    save_decision_candidates,
    save_decision_report,
    split_messages,
)


def _demo_regime_by_date(daily_ohlcv) -> dict:
    """데모 fixture: 모든 사례 날짜를 강세장(True)으로 가정."""
    if daily_ohlcv is None or daily_ohlcv.empty:
        return {}
    return {d: True for d in daily_ohlcv["date"].unique().tolist()}


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

    # ── 2. 주도테마 식별 (Theme) ─────────────────────────────────────────────
    leading_themes = identify_leading_themes(snapshot_df, theme_df, threshold=3)
    leading_codes = codes_in_leading_themes(leading_themes)

    if not leading_themes:
        logger.info("주도테마 없음 — 후보 없이 레포트 생성")

    # ── 3. 종배 후보 추출 (Eod.Pick v2 round 41) ────────────────────────────────
    # leading_codes 우회 — 거래대금 50위 전체 universe + 10~27% 컷
    candidates_df = extract_candidates(snapshot_df, leading_theme_codes=None)
    accepted = accepted_candidates(candidates_df)

    # Eod.Pick v2 (c) 종가 고가-10% 이내 + (d) 52주 신고가 post-filter.
    # pipeline 은 fetch_quote 보강 path 가 없으므로 snapshot 의 intraday_high 가
    # 0 이면 (c) skip 되고 통과. 운영 환경(real)에서는 KIS volume-rank 가
    # intraday_high 를 채워주므로 정상 동작 — demo 모드 fixture 도 채워서 전달.
    from src.overnight.candidates import apply_r4v2_post_filters
    accepted_dicts = [row.to_dict() for _, row in accepted.iterrows()]
    if accepted_dicts and not daily_ohlcv.empty:
        accepted_dicts = apply_r4v2_post_filters(accepted_dicts, daily_ohlcv, target_date)

    # ── 4. Historical + Sizing (Eod.GapStats, Eod.Sizing) ───────────────────────────────────
    candidates_with_stats: list[dict[str, Any]] = []
    for row in accepted_dicts:
        code = str(row.get("code", ""))
        close = int(row.get("price") or 0)
        high = int(row.get("intraday_high") or close)
        # 일중 저가는 KIS API stck_lwpr 에서 받음 (H1 수정).
        # 스냅샷에 없거나 0이면 보수적 추정값 사용.
        low_raw = int(row.get("intraday_low") or 0)
        low = low_raw if low_raw > 0 else int(close * 0.85)

        cp = close_position(
            open_p=float(row.get("prev_close") or close),
            high=float(high),
            low=float(low),
            close=float(close),
        )

        # 데모는 강세장 가정 + 후보별 거래량 비율 mock
        if demo:
            today_strong = True
            regime_by_date = _demo_regime_by_date(daily_ohlcv)
            vol_ratio = 6.5  # 평균 대비 ×6.5배 (단타 자금 집중 가정)
        else:
            today_strong = None
            regime_by_date = None
            vol_ratio = None
        # 종목별 layer (사용자 정정 2026-05-21): code 인자로 해당 종목 historical 만.
        layers = historical_4layer(
            daily_ohlcv,
            today_close_pos=cp,
            today=target_date,
            today_strong_market=today_strong,
            market_regime_by_date=regime_by_date,
            today_volume_ratio=vol_ratio,
            code=code,
        )
        sizing_layer_name, sizing_stats = pick_sizing_layer(layers)

        # Eod.Pick v2 (f) Layer 표본 ≥5 — round 41 후속 2026-05-19: hard cut → soft.
        # 표본 부족도 후보 유지. Kelly 만 None 으로 나옴 (sample factor n<5 = None).
        sample_sufficient = has_enough_samples(sizing_stats)
        if not sample_sufficient:
            logger.info(f"[파이프라인] {code} Eod.Pick v2 (f) 표본 부족 (n<5) — soft 경고, 후보 유지")

        # 테마 조회
        if not theme_df.empty:
            themes = theme_df[theme_df["code"] == code]["theme"].tolist()
        else:
            themes = []

        # Eod.Pick v2 보조 지표 (round 41 ④) — 1년 ret≥10 + 갭상 비율
        # 사용자 정정 2026-05-21: 12 케이스 매트릭스 추가
        from src.overnight.gap_stats import (
            candle_count_aux,
            historical_aux_matrix,
            historical_ret10_gap_stats,
        )
        ret10_aux = historical_ret10_gap_stats(daily_ohlcv, code, target_date)
        aux_matrix = historical_aux_matrix(daily_ohlcv, code, target_date)

        c: dict[str, Any] = dict(row)
        c["themes"] = themes
        c["layers"] = layers
        c["sizing_layer"] = sizing_layer_name
        c["sizing_stats"] = sizing_stats
        c["historical_aux"] = ret10_aux
        c["historical_aux_matrix"] = aux_matrix
        c["candle_aux"] = candle_count_aux(daily_ohlcv, code, target_date)
        from src.overnight.nxt import is_nxt_tradable, load_nxt_tradable
        c["nxt_tradable"] = is_nxt_tradable(code, load_nxt_tradable(data_dir))
        c["sample_sufficient"] = sample_sufficient
        candidates_with_stats.append(c)

    # 사이징 계산
    sizing_results = compute_sizing(candidates_with_stats)
    for i, c in enumerate(candidates_with_stats):
        c["sizing"] = {
            "kelly":  sizing_results["kelly"][i],
            "sharpe": sizing_results["sharpe"][i],
            "equal":  sizing_results["equal"][i],
        }

    # Eod.Sizing v2 (2026-05-25): 거래대금순위 버킷 rolling Kelly (scheduler 와 동일).
    try:
        from src.data.storage import read_stock_master
        from src.overnight.sizing_bucket import build_bucket_stats, compute_bucket_sizing
        _master_df = read_stock_master(data_dir)
        _tradable = (
            set(_master_df["code"].astype(str))
            if _master_df is not None and not _master_df.empty
            else set()
        )
        _bsize = compute_bucket_sizing(
            candidates_with_stats,
            build_bucket_stats(daily_ohlcv, target_date, _tradable),
        )
        for i, c in enumerate(candidates_with_stats):
            c["sizing"]["kelly_bucket"] = _bsize["kelly_abs"][i]
            c["sizing"]["kelly_bucket_rel"] = _bsize["kelly_rel"][i]
            c["sizing_bucket"] = _bsize["buckets"][i]
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[파이프라인] 버킷 사이징 실패 — 생략: {e}")

    # 거래대금순위 정렬 + top3 플래그 (hold-3) — scheduler 와 동일.
    candidates_with_stats.sort(key=lambda c: (c.get("rank") or 9999))
    for i, c in enumerate(candidates_with_stats):
        c["rank_in_report"] = i + 1
        c["is_top3"] = i < 3

    # ── 5. 레포트 생성 ────────────────────────────────────────────────────
    hh2, mm2 = snapshot_time.split(":")
    snap_dt = datetime(
        target_date.year, target_date.month, target_date.day,
        int(hh2), int(mm2), tzinfo=KST,
    )
    market_stats: dict[str, Any] = {}
    if demo:
        # 데모: 강세장 가정 (제룡전기 5/4 시점 시뮬레이션)
        market_stats = {
            "kospi_current": 2680.45,
            "kospi_change_rate": 0.83,
            "kospi_ma200": 2615.20,
            "kospi_above_ma200": True,
            "kospi_60d_return": 3.42,
        }
        # 14:50 시그널 mock (실제 운영에선 scheduler가 KIS client로 fetch)
        for c in candidates_with_stats:
            c["intraday_signals"] = {
                "asking_price": {
                    "bid_total_volume": 3_200_000,
                    "ask_total_volume": 450_000,
                    "bid_ask_ratio": 7.1,
                },
                "ccnl_strength": {"ccnl_strength": 142.0},
                "investor_flow": {
                    "foreign_net_buy_value": 1_800_000_000,
                    "institution_net_buy_value": 4_200_000_000,
                },
            }
    # 후보 3거래일 추이 (거래대금/회전율/수급 + 순위 변동) — 2026-05-24.
    # demo/실데이터 공통 경로. 스냅샷/investor_daily 없으면 추이 셀만 비고 진행.
    try:
        from src.overnight.candidate_trends import attach_candidate_trends
        attach_candidate_trends(candidates_with_stats, daily_ohlcv, data_dir, target_date)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[파이프라인] 후보 추이 계산 실패 — 추이 생략: {e}")

    report = build_decision_report(
        leading_themes, candidates_with_stats, snap_dt, market_stats=market_stats
    )

    # ── 6. 저장 ───────────────────────────────────────────────────────────
    if save:
        save_decision_report(report, data_dir, snap_dt)
        save_decision_candidates(candidates_with_stats, data_dir, snap_dt)
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
