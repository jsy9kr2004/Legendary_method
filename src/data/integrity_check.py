"""적재 데이터 무결성 체크.

매일 incremental 적재 후 실행 권장. 체크 항목:

1. **종목 수 임계** — 가장 최근일 행수 / 직전 영업일 행수 ≥ 0.95
2. **가격 이상치** — 종목별 close pct_change 절대값 > 0.5 (50%)
3. **주말 적재** — date.weekday() ≥ 5 인 행 (버그 의심)

이슈 발견 시 stderr 에 출력하고 비-zero exit code 로 종료.
`--send` 옵션 시 실패 항목을 텔레그램 에러 알림으로도 발송 (M5+).

사용:
    python -m src.data.integrity_check
    python -m src.data.integrity_check --coverage 0.90 --outlier 0.30
    python -m src.data.integrity_check --send    # 이상 시 텔레그램 발송
"""
from __future__ import annotations

import argparse
import sys

import pandas as pd
from loguru import logger

from src.config import load_settings
from src.data import storage
from src.logging_setup import setup_logging


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="일봉 적재 무결성 체크")
    p.add_argument(
        "--coverage",
        type=float,
        default=0.95,
        help="가장 최근일 종목수 / 직전영업일 종목수 임계 (default 0.95)",
    )
    p.add_argument(
        "--outlier",
        type=float,
        default=0.5,
        help="가격 이상치 pct_change 절대값 임계 (default 0.50 = 50%%)",
    )
    p.add_argument(
        "--max-show",
        type=int,
        default=20,
        help="이슈별 표시 최대 행 수 (default 20)",
    )
    p.add_argument(
        "--send",
        action="store_true",
        help="이상 시 텔레그램 에러 알림 발송 (FAIL 만, WARN 은 발송 안 함)",
    )
    return p.parse_args(argv)


def build_alert_text(failures: list[str], warnings: list[str]) -> str:
    """이슈 리스트 → 텔레그램 알림용 plain text.

    Args:
        failures: FAIL 항목 (exit code 영향). 알림에 ❌
        warnings: WARN 항목 (exit code 영향 X). 알림에 ⚠

    Returns:
        한 줄 헤더 + 항목 bullet. 빈 알림은 "" 반환.
    """
    if not failures and not warnings:
        return ""
    lines = []
    if failures:
        lines.append(f"❌ FAIL ({len(failures)}건):")
        for f in failures:
            lines.append(f"  • {f}")
    if warnings:
        lines.append(f"⚠ WARN ({len(warnings)}건):")
        for w in warnings:
            lines.append(f"  • {w}")
    return "\n".join(lines)


def check_recent_coverage(
    df: pd.DataFrame, threshold: float
) -> tuple[bool, str]:
    """가장 최근 적재일의 종목 수가 직전 영업일의 threshold 이상인지."""
    if df.empty:
        return False, "데이터 없음"
    dates = sorted(df["date"].unique(), reverse=True)
    if len(dates) < 2:
        return True, f"비교할 직전 영업일 없음 (적재일 {len(dates)}개)"
    latest = dates[0]
    prev = dates[1]
    n_latest = (df["date"] == latest).sum()
    n_prev = (df["date"] == prev).sum()
    ratio = n_latest / n_prev if n_prev > 0 else 0.0
    ok = bool(ratio >= threshold)
    msg = (
        f"latest={latest}: {n_latest} 종목, "
        f"prev={prev}: {n_prev} 종목, ratio={ratio:.2%} (임계 {threshold:.0%})"
    )
    return ok, msg


def find_price_outliers(df: pd.DataFrame, threshold: float) -> pd.DataFrame:
    """종목별 close.pct_change() 절대값 > threshold 인 행 반환."""
    if df.empty:
        return df.iloc[0:0].copy()
    df_sorted = df.sort_values(["code", "date"]).copy()
    df_sorted["pct_change"] = (
        df_sorted.groupby("code")["close"].pct_change().astype("Float64")
    )
    mask = df_sorted["pct_change"].abs() > threshold
    return df_sorted[mask.fillna(False)].copy()


def find_weekend_rows(df: pd.DataFrame) -> pd.DataFrame:
    """date.weekday() >= 5 인 행 (주말 적재 버그 의심)."""
    if df.empty:
        return df.iloc[0:0].copy()
    dates_pd = pd.to_datetime(df["date"])
    weekend_mask = dates_pd.dt.weekday >= 5
    return df[weekend_mask].copy()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = load_settings()
    setup_logging(settings)

    df = storage.read_daily_ohlcv(settings.data_dir)
    if df.empty:
        msg = "일봉 데이터 없음 (`init_daily` 먼저 실행)"
        logger.error(msg)
        if args.send:
            _telegram_alert(settings, build_alert_text([msg], []))
        return 2

    logger.info(
        f"무결성 체크 시작: {len(df)} rows, "
        f"{df['code'].nunique()} 종목, "
        f"{df['date'].min()} ~ {df['date'].max()}"
    )

    failures: list[str] = []
    warnings_: list[str] = []

    # 1. 커버리지
    ok, msg = check_recent_coverage(df, args.coverage)
    if ok:
        logger.info(f"[OK]   커버리지 — {msg}")
    else:
        logger.warning(f"[FAIL] 커버리지 미달 — {msg}")
        failures.append(f"커버리지 미달 — {msg}")

    # 2. 가격 이상치 (수정주가 일관 사용 가정 → 50% 이상은 진짜 이상)
    outliers = find_price_outliers(df, args.outlier)
    if outliers.empty:
        logger.info(f"[OK]   가격 이상치 0건 (임계 {args.outlier:.0%})")
    else:
        logger.warning(f"[WARN] 가격 이상치 {len(outliers)} 건 (임계 {args.outlier:.0%}):")
        for _, row in outliers.head(args.max_show).iterrows():
            logger.warning(
                f"       {row['code']} {row['date']} "
                f"close={row['close']} pct_change={row['pct_change']:.1%}"
            )
        if len(outliers) > args.max_show:
            logger.warning(f"       ... (총 {len(outliers)} 건 중 {args.max_show}만 표시)")
        # WARN — exit code 영향 X (수정주가 보정 후에도 이상치는 정상 케이스도 많음)
        warnings_.append(
            f"가격 이상치 {len(outliers)}건 (임계 {args.outlier:.0%})"
        )

    # 3. 주말 적재 (있으면 버그)
    weekends = find_weekend_rows(df)
    if weekends.empty:
        logger.info("[OK]   주말 적재 행 0건")
    else:
        logger.error(f"[FAIL] 주말 적재 {len(weekends)} 행 (버그 의심):")
        for _, row in weekends.head(args.max_show).iterrows():
            logger.error(f"       {row['code']} {row['date']}")
        failures.append(f"주말 적재 {len(weekends)}행 (버그 의심)")

    if args.send and (failures or warnings_):
        _telegram_alert(settings, build_alert_text(failures, warnings_))

    if failures:
        logger.error(f"무결성 체크 실패: {len(failures)} 건")
        return 1
    logger.info("무결성 체크 통과")
    return 0


def _telegram_alert(settings, text: str) -> None:
    """텔레그램 에러 알림 발송 (실패해도 raise 안 함).

    Dispatcher.telegram_error() 사용 — parse_mode=None plain text + 이중 실패 방지.
    """
    if not text:
        return
    try:
        from src.notify.dispatcher import Dispatcher
        d = Dispatcher(settings)
        d.telegram_error(text, context="무결성 체크")
    except Exception as e:  # noqa: BLE001
        logger.error(f"텔레그램 알림 실패: {e}")


if __name__ == "__main__":
    sys.exit(main())
