"""분봉 거래대금 가속배율 / 신고가 돌파 등 모멘텀 지표 (M6).

용도:
    M6 실시간 모니터링에서 "치고 올라옴" / "자금 이탈" 을 정량적으로 감지.
    상태 머신 (TRANSITION/GRACE) 트리거 입력값 제공.

핵심 지표:
    - 가속배율(accel_ratio): (최근 N분 거래대금 / 최근 N분 직전 M분 평균 거래대금)
        양수 → 자금 유입 가속 / 음수 → 자금 이탈
    - 신고가 돌파(is_recent_high): 최근 X일 고가 갱신 여부

분봉 데이터 부재 시:
    KIS API 가 1~5분봉만 제공 — 일별 적재 후 누적되어야 신뢰도 상승.
    현재 시점에는 호출 시점의 직전 30분만 안정적.

함수는 모두 pure — 분봉 DataFrame 받아서 스칼라 반환. fetch 책임은
`src/data/intraday_realtime.py`.
"""
from __future__ import annotations

import math

import pandas as pd

from src.jongbae.config_thresholds import (
    ACCEL_BASELINE_MINUTES,
    ACCEL_RECENT_BAR_MINUTES,
    EXIT_ACCEL_RATIO,
    RECENT_HIGH_LOOKBACK_DAYS,
    STRONG_RISE_ACCEL_RATIO,
    TRANSITION_ACCEL_RATIO,
    TRANSITION_MIN_BAR_VALUE,
)


def compute_accel_ratio(
    minute_bars: pd.DataFrame,
    recent_minutes: int = ACCEL_RECENT_BAR_MINUTES,
    baseline_minutes: int = ACCEL_BASELINE_MINUTES,
) -> float:
    """분봉 거래대금 가속배율.

    정의:
        recent_value    = 가장 최근 `recent_minutes` 분의 거래대금 합계
        baseline_value  = 그 직전 `baseline_minutes` 분의 거래대금 합계 평균
                          (= 합계 / (baseline_minutes / recent_minutes) windows)

        accel_ratio = recent_value / baseline_avg_per_window

    예: recent=5, baseline=30, recent_value=10억, baseline_total=12억
        baseline_avg_per_5min = 12억 / 6 = 2억
        accel_ratio = 10억 / 2억 = 5.0배 → "5배 가속"

    Args:
        minute_bars: MINUTE_BAR_COLUMNS 스키마. time 오름차순 정렬 가정.
                     `trading_value` 컬럼 필수.
        recent_minutes: 최근 N분 (보통 5).
        baseline_minutes: 직전 M분 (보통 30).

    Returns:
        가속배율 (float). 데이터 부족 시 NaN. 분모 0 일 시 NaN.

    음수가 아닌 양수만 반환:
        "가속" 의 정의는 평균 대비 비율. 0~∞ 범위.
        "자금 이탈"은 ratio < 1 로 표현. < 0 은 안 나옴.
        호출부에서 이탈 판정은 `is_exit_signal()` 이용.
    """
    if minute_bars.empty or "trading_value" not in minute_bars.columns:
        return float("nan")
    if recent_minutes <= 0 or baseline_minutes <= 0:
        return float("nan")

    n = len(minute_bars)
    if n < recent_minutes + recent_minutes:
        # baseline 도 최소 1 window 는 있어야
        return float("nan")

    bars = minute_bars.tail(recent_minutes + baseline_minutes)
    recent = bars.tail(recent_minutes)
    baseline = bars.iloc[: -recent_minutes]
    if baseline.empty:
        return float("nan")

    recent_value = float(recent["trading_value"].sum())
    baseline_value = float(baseline["trading_value"].sum())
    if baseline_value <= 0:
        return float("nan")

    # baseline 을 recent 와 같은 윈도우 크기로 정규화 (평균 윈도우 거래대금)
    n_windows = len(baseline) / recent_minutes
    if n_windows <= 0:
        return float("nan")
    baseline_per_window = baseline_value / n_windows

    if baseline_per_window <= 0:
        return float("nan")
    return recent_value / baseline_per_window


def is_strong_rise(accel_ratio: float, recent_bar_value: int) -> bool:
    """강한 부상 신호 — 가속배율 ≥ 10배 AND 분봉 거래대금 ≥ 20억.

    "치고 올라옴" 의 결정적 신호. 이 임계 통과 시 별도 푸시 알림.
    """
    if accel_ratio != accel_ratio:  # NaN
        return False
    return (
        accel_ratio >= STRONG_RISE_ACCEL_RATIO
        and recent_bar_value >= TRANSITION_MIN_BAR_VALUE
    )


def is_transition_candidate(
    accel_ratio: float,
    recent_bar_value: int,
    candidate_turnover: float,
    incumbent_turnover: float,
    turnover_ratio_threshold: float,
) -> bool:
    """TRANSITION (주도주 교체 가능성) 진입 조건.

    a2 가속배율 ≥ TRANSITION_ACCEL_RATIO
    AND 분봉 거래대금 ≥ TRANSITION_MIN_BAR_VALUE (20억)
    AND a2 회전율 ≥ a1 × turnover_ratio_threshold (0.6)

    Args:
        accel_ratio: a2 의 가속배율.
        recent_bar_value: a2 의 최근 분봉 거래대금 (원).
        candidate_turnover: a2 회전율 (%).
        incumbent_turnover: 현재 주도주 a1 회전율 (%).
        turnover_ratio_threshold: 보통 0.6 (config_thresholds).
    """
    if accel_ratio != accel_ratio:  # NaN
        return False
    if accel_ratio < TRANSITION_ACCEL_RATIO:
        return False
    if recent_bar_value < TRANSITION_MIN_BAR_VALUE:
        return False
    if incumbent_turnover <= 0 or incumbent_turnover != incumbent_turnover:
        return False
    if candidate_turnover != candidate_turnover:
        return False
    return candidate_turnover >= incumbent_turnover * turnover_ratio_threshold


def is_exit_signal(accel_ratio: float, baseline_ratio_threshold: float | None = None) -> bool:
    """자금 이탈 경보 — 가속배율이 1 + EXIT_ACCEL_RATIO 이하.

    EXIT_ACCEL_RATIO = -0.4 → 가속배율 0.6 미만 (직전 30분 평균 대비 40%↓ 이하).
    a1 보유 중일 때 매도 검토 시그널.

    Args:
        accel_ratio: 가속배율 (>0).
        baseline_ratio_threshold: 임계. None 이면 1 + EXIT_ACCEL_RATIO.
    """
    if accel_ratio != accel_ratio:  # NaN
        return False
    if baseline_ratio_threshold is None:
        baseline_ratio_threshold = 1.0 + EXIT_ACCEL_RATIO
    return accel_ratio <= baseline_ratio_threshold and accel_ratio >= 0


def is_recent_high(
    daily_ohlcv: pd.DataFrame,
    today_high: int,
    code: str,
    today: pd.Timestamp | None = None,
    lookback_days: int = RECENT_HIGH_LOOKBACK_DAYS,
) -> bool:
    """최근 N거래일 고가 돌파 여부.

    매물대 통과 시그널. M6 모니터링 메시지 보조 표시 + 강한 신호 부스터.

    Args:
        daily_ohlcv: long format DataFrame, columns=[code, date, high].
        today_high: 오늘 일중 고가 (또는 현재가).
        code: 6자리 종목코드.
        today: 오늘 날짜. None 이면 ohlcv 의 max 사용.
        lookback_days: 직전 며칠 비교.

    Returns:
        True 면 직전 N거래일 고가를 모두 넘어선 신고가.
        데이터 부족 시 False.
    """
    if daily_ohlcv.empty or today_high <= 0:
        return False

    df = daily_ohlcv[daily_ohlcv["code"].astype(str) == code]
    if df.empty:
        return False
    if today is not None:
        df = df[df["date"] < today]

    df = df.sort_values("date").tail(lookback_days)
    if df.empty:
        return False

    return today_high > int(df["high"].max())


def short_trend_sparkline(
    minute_bars: pd.DataFrame,
    n_recent: int = 6,
) -> str:
    """직전 N개 분봉 거래대금 추세 sparkline.

    8단계 블록 문자 사용 (▁▂▃▄▅▆▇█). 모니터링 메시지 시각화.
    """
    blocks = " ▁▂▃▄▅▆▇█"
    if minute_bars.empty or "trading_value" not in minute_bars.columns:
        return ""

    tail = minute_bars.tail(n_recent)
    vals = tail["trading_value"].astype(float).tolist()
    if not vals:
        return ""

    vmax = max(vals)
    vmin = min(vals)
    rng = vmax - vmin
    if rng == 0:
        return blocks[5] * len(vals)

    out = []
    for v in vals:
        ratio = (v - vmin) / rng
        idx = int(math.floor(ratio * (len(blocks) - 1)))
        idx = max(1, min(len(blocks) - 1, idx))
        out.append(blocks[idx])
    return "".join(out)
