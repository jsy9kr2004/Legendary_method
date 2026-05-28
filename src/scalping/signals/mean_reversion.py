"""단저단고 (intraday mean reversion) 시그널.

`docs/scalping-redesign-2026-05-27.md` §2 v3 정의 구현.

사용:
    bars = build_bars(tick_df)   # 백테스트 — tick log 누적 데이터
    classify(bars)
    last = bars.iloc[-1]
    sigB, sigS = last['sigB'], last['sigS']

라이브 worker:
    sigB, sigS, reason = analyze_minute_bars(kis_minute_bars_df)
    # → MonitoredStock.mr_sigB / .mr_sigS / .mr_reason

매수 (sigB) — swing low confirm + oversold OR (다음 중 1개라도):
  - Stochastic %K ≤ 30
  - Williams %R ≤ -50
  - RSI ≤ 40
  - zscore ≤ -1.0

매도 (sigS) — swing high confirm + overbought OR (다음 중 1개라도):
  - Stochastic %K ≥ 70
  - Williams %R ≥ -30
  - RSI ≥ 60
  - zscore ≥ +1.0

5/27 backtest 결과:
  - sigS 매도 시그널: 승률 97~100% (v4/v5c)
  - sigB 매수 시그널: 자연 prof 60~67%, false ~10%

정정 이력:
  - 2026-05-27: vol_spike, bid_ask_ratio, vol_accel 매수 필수 조건 제거.
    AUC 분석 (0.50~0.52) 결과 noise 입증. CLAUDE.md "호가 잔량 비율만으로
    매수 판단 X" 정합.
  - 2026-05-27: 평균회귀 임계 단일 (zscore ≤ -1.5) → OR 조합 (4개 중 1개).
    단일 임계는 SK하이닉스 4 swing 중 1개만 잡음.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.scalping.bars import add_candle_features, add_mean_reversion, add_swing_labels
from src.scalping.signals.weighted_score import (
    add_score_features, grade as score_grade,
    compute_score_buy, compute_score_sell, grade_buy, grade_sell,
)


# 임계 (튜닝 가능, system tuning ritual 통과 후만 변경)
OVERSOLD_STOCH_K = 30.0
OVERSOLD_WILLIAMS_R = -50.0
OVERSOLD_RSI = 40.0
OVERSOLD_ZSCORE = -1.0

OVERBOUGHT_STOCH_K = 70.0
OVERBOUGHT_WILLIAMS_R = -30.0
OVERBOUGHT_RSI = 60.0
OVERBOUGHT_ZSCORE = 1.0

# 매도 시그널 추가 (윗꼬리 음봉)
UPPER_WICK_BEARISH_PCT = 0.5


def is_oversold(row) -> bool:
    """oversold 여부 — 평균회귀 지표 4개 중 1개라도 만족."""
    return (
        (pd.notna(row.get("stoch_k")) and row["stoch_k"] <= OVERSOLD_STOCH_K)
        or (pd.notna(row.get("williams_r")) and row["williams_r"] <= OVERSOLD_WILLIAMS_R)
        or (pd.notna(row.get("rsi")) and row["rsi"] <= OVERSOLD_RSI)
        or (pd.notna(row.get("zscore")) and row["zscore"] <= OVERSOLD_ZSCORE)
    )


def is_overbought(row) -> bool:
    """overbought 여부 — 평균회귀 지표 4개 중 1개라도 만족."""
    return (
        (pd.notna(row.get("stoch_k")) and row["stoch_k"] >= OVERBOUGHT_STOCH_K)
        or (pd.notna(row.get("williams_r")) and row["williams_r"] >= OVERBOUGHT_WILLIAMS_R)
        or (pd.notna(row.get("rsi")) and row["rsi"] >= OVERBOUGHT_RSI)
        or (pd.notna(row.get("zscore")) and row["zscore"] >= OVERBOUGHT_ZSCORE)
    )


def classify(bars: pd.DataFrame) -> pd.DataFrame:
    """bars (build_bars 결과) → sigB / sigS 컬럼 추가.

    sigB = is_local_low (직전 3봉 low 보다 낮음) AND oversold.
    sigS = is_local_high AND overbought AND (≥2 양봉 후 첫 음봉 OR 윗꼬리 음봉).

    in-place 수정. bars 반환.
    """
    # vectorized oversold/overbought
    oversold = (
        (bars["stoch_k"] <= OVERSOLD_STOCH_K)
        | (bars["williams_r"] <= OVERSOLD_WILLIAMS_R)
        | (bars["rsi"] <= OVERSOLD_RSI)
        | (bars["zscore"] <= OVERSOLD_ZSCORE)
    )
    overbought = (
        (bars["stoch_k"] >= OVERBOUGHT_STOCH_K)
        | (bars["williams_r"] >= OVERBOUGHT_WILLIAMS_R)
        | (bars["rsi"] >= OVERBOUGHT_RSI)
        | (bars["zscore"] >= OVERBOUGHT_ZSCORE)
    )
    # 매수: swing low + oversold
    bars["sigB"] = bars["is_local_low"] & oversold.fillna(False)
    # 매도: swing high + overbought + 봉 패턴 보강 (≥2 양봉 후 첫 음봉 OR 윗꼬리 음봉)
    prev_bull_2plus = bars["consec_bull"].shift(1) >= 2
    bear_after_bull = (bars["candle"] == "bear") & prev_bull_2plus
    upper_wick_bearish = (bars["candle"] == "bear") & (bars["upper_wick_pct"] >= UPPER_WICK_BEARISH_PCT)
    candle_S = bear_after_bull | upper_wick_bearish
    bars["sigS"] = bars["is_local_high"] & overbought.fillna(False) & candle_S
    return bars


def analyze_minute_bars(bars_minute: pd.DataFrame, code: str | None = None) -> tuple:
    """KIS 1분봉 fetch 결과 → 마지막 봉의 단저단고 시그널 (v11).

    2026-05-29 v11 — score_buy / score_sell 분리.
    2026-05-29 v11.1 — per-stock weight 적용 (code 주어지면).

    Args:
        bars_minute: KIS 1분봉 (≥ 25 봉 필요)
        code: 종목 코드 (per-stock weight 사용 위해, None 이면 global v11).

    Returns:
        (sigB, sigS, reason, score_buy, grade_buy, score_sell, grade_sell)

    표본 < 25 봉 시 (False, False, None, 0.0, "NEUTRAL", 0.0, "NEUTRAL").
    """
    if bars_minute is None or len(bars_minute) < 25:
        return False, False, None, 0.0, "NEUTRAL", 0.0, "NEUTRAL"
    df = bars_minute.copy()
    if "time" in df.columns and not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df["time"], format="%H%M%S", errors="coerce")
    if "bar_tv" not in df.columns and "trading_value" in df.columns:
        df["bar_tv"] = df["trading_value"].fillna(0).clip(lower=0)
    add_candle_features(df)
    add_mean_reversion(df)
    add_swing_labels(df, lookback=3)
    classify(df)
    add_score_features(df)
    # v11 추가 feature — touch_count / support_dist_pct 는 add_score_features 가 채움.
    # 봉 패턴 boolean (compute_score_v11 입력용)
    df["is_bullish"] = (df["candle"] == "bull").astype(int)
    df["is_bearish"] = (df["candle"] == "bear").astype(int)
    df["is_doji"] = (df["candle"] == "doji").astype(int)
    last = df.iloc[-1]
    sigB = bool(last.get("sigB", False))
    sigS = bool(last.get("sigS", False))
    # v11 score 계산 — code 주어지면 per-stock weight 사용 (없으면 global v11)
    sc_buy = compute_score_buy(last, code=code)
    sc_sell = compute_score_sell(last, code=code)
    g_buy = grade_buy(sc_buy)
    g_sell = grade_sell(sc_sell)
    if not (sigB or sigS or g_buy != "NEUTRAL" or g_sell != "NEUTRAL"):
        return False, False, None, sc_buy, g_buy, sc_sell, g_sell
    # reason 라인 (v10b 호환 — score 라벨)
    score_label = sc_sell if sigS else sc_buy
    grade_label = g_sell if sigS else g_buy
    parts: list[str] = [f"score {score_label:.2f} ({grade_label})"]
    if sigB:
        parts.append("단저")
        for k, v, fmt, t in [
            ("stoch_k", "STOCH", "{:.0f}", OVERSOLD_STOCH_K),
            ("rsi", "RSI", "{:.0f}", OVERSOLD_RSI),
            ("zscore", "Z", "{:.2f}", OVERSOLD_ZSCORE),
        ]:
            val = last.get(k)
            if pd.notna(val) and val <= t:
                parts.append(f"{v}={fmt.format(val)}")
    if sigS:
        parts.append("단고")
        for k, v, fmt, t in [
            ("stoch_k", "STOCH", "{:.0f}", OVERBOUGHT_STOCH_K),
            ("rsi", "RSI", "{:.0f}", OVERBOUGHT_RSI),
            ("zscore", "Z", "{:.2f}", OVERBOUGHT_ZSCORE),
        ]:
            val = last.get(k)
            if pd.notna(val) and val >= t:
                parts.append(f"{v}={fmt.format(val)}")
    # v11 reason 라인 — 핵심 feature 만 (atr_pct / touch_count 표시)
    if pd.notna(last.get("atr_pct")):
        parts.append(f"atr{last['atr_pct']:.2f}%")
    if pd.notna(last.get("support_dist_pct")):
        parts.append(f"추세선{last['support_dist_pct']:+.2f}%")
    if pd.notna(last.get("touch_count")):
        parts.append(f"매물대{int(last['touch_count'])}")
    reason = " ".join(parts) if parts else None
    return sigB, sigS, reason, sc_buy, g_buy, sc_sell, g_sell


def classify_tick_realtime(
    current_price: float,
    current_bar_low_so_far: float,
    current_bar_high_so_far: float,
    prev_3bar_low_min: float,
    prev_3bar_high_max: float,
    oversold_now: bool,
    overbought_now: bool,
) -> tuple[bool, bool]:
    """tick 단위 실시간 swing 감지 — 봉 close 안 기다림.

    봉 진행 중 tick 마다 호출. 현재 가격이 직전 3봉 low 깨고 양봉 회복하면
    즉시 sigB. 봉 close 지연 (~0.37% 슬리피지) 회피.

    반환: (sigB_now, sigS_now).
    """
    # 현재 봉 안의 진행 low 가 직전 3봉 low 보다 낮음 + 현재 가격이 그 low 위로 회복
    sigB_now = (
        current_bar_low_so_far < prev_3bar_low_min
        and current_price > current_bar_low_so_far
        and oversold_now
    )
    sigS_now = (
        current_bar_high_so_far > prev_3bar_high_max
        and current_price < current_bar_high_so_far
        and overbought_now
    )
    return sigB_now, sigS_now
