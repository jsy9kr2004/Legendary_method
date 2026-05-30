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

import os

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

# ── ZigZag swing + 강망치 모드 (2026-05-29~30 재설계, image/3.PNG) ───────────
# 사용자 의도: 추세·과매도 무관, 로컬 swing 저점에서 "강망치(긴 아래꼬리)" 매수.
# data/journal/2026-05-29.md + docs/scalping-redesign-2026-05-27.md 참조.
#
# 검증 (train 5/18~22 / OOS 5/27~29, surface universe, 지정가 0.2%):
#   - 진입 = swing저점(ZigZag floor 0.5) ∩ 강망치(아래꼬리 ≥ 0.6) — OOS 왕복 +0.131%
#     (기존 oversold STRONG 단저는 OOS 53종목일에 1건 발화 = 사실상 미작동).
#   - 청산 = trailing (고점 대비 -1.0%). 고정 익절(target)은 OOS 음수 = 과적합 배제.
#   - 단고(매도) STRONG = 폐기. 청산으로 쓰면 진입 alpha 를 죽임 (OOS -0.972%).
#     과매수 기반이라 강세장 영구 발화 + 매도 alpha 미입증.
#   - per-stock weight 부적합 (강망치 발화가 종목당 드물어 학습 표본 X) → 글로벌 룰.
#
# 한계: OOS 41건·3일 표본, net/건 작아 빈도 누적 구조. trailing 폭은 dry-run 으로 좁힘.
# 라이브는 카드 STRONG 단저(매수) 표시 + 사용자 직접 매매. 자동매매 X (CLAUDE.md).
# ★ 강망치가 기본 동작 — 재시작 즉시 적용. oversold sigB/sigS 와 단고(매도) STRONG 은
#   완전 폐기 (analyze_minute_bars 가 무조건 _analyze_zigzag). 롤백은 git revert.
#   classify()(oversold) 함수는 백테스트/회귀 테스트 호환 위해 남겨두나 라이브 미사용.
MR_ZIGZAG_FLOOR_DEFAULT = 0.5    # swing 반전 확정 임계 (%) — ZigZag floor (MR_ZIGZAG_FLOOR 로 조정)
HAMMER_LOWER_WICK = 0.6          # 강망치 — 아래꼬리가 봉 range 의 60% 이상 (저가 거부)
MR_TRAIL_PCT = 1.0               # 청산 trailing — 고점 대비 -1.0% (train-best, dry-run 조정)


def _zigzag_floor() -> float:
    try:
        return float(os.getenv("MR_ZIGZAG_FLOOR", MR_ZIGZAG_FLOOR_DEFAULT))
    except (TypeError, ValueError):
        return MR_ZIGZAG_FLOOR_DEFAULT


def classify_zigzag(bars: pd.DataFrame, floor_pct: float | None = None) -> pd.DataFrame:
    """ZigZag swing 저점 ∩ 강망치 매수 시그널 — look-ahead 없는 실시간 방식.

    저점 추적 중 저점 대비 +floor% 반등하는 봉에서, **그 저점 봉이 강망치
    (아래꼬리 ≥ HAMMER_LOWER_WICK)** 면 sigB (매수 confirm, 저점보다 floor% 비쌈).
    `zz_amp` = 확정된 직전 swing 진폭 (%).

    sigS(단고/매도)는 **폐기** — 항상 False. 청산은 trailing(MR_TRAIL_PCT)으로
    별도 처리 (단고 STRONG 으로 청산하면 진입 alpha 를 죽임, OOS 검증).
    oversold/overbought 게이트 안 씀. in-place 수정. bars 반환.
    """
    if floor_pct is None:
        floor_pct = _zigzag_floor()
    close = bars["close"].to_numpy()
    lw = (bars["lower_wick_pct"].to_numpy()
          if "lower_wick_pct" in bars.columns else np.zeros(len(close)))
    n = len(close)
    sigB = np.zeros(n, dtype=bool)
    sigS = np.zeros(n, dtype=bool)  # 단고 폐기 — 항상 False
    amp = np.full(n, np.nan)
    if n >= 2:
        trend = 0  # 0/-: 저점 탐색, +: 고점 탐색
        ext = close[0]
        ext_i = 0
        for i in range(1, n):
            if trend <= 0:
                if close[i] < ext:
                    ext = close[i]; ext_i = i
                elif (close[i] - ext) / ext * 100 >= floor_pct:
                    # 저점 확정 — 저점 봉(ext_i)이 강망치일 때만 매수 시그널
                    if lw[ext_i] >= HAMMER_LOWER_WICK:
                        sigB[i] = True
                        amp[i] = (close[i] - ext) / ext * 100
                    trend = 1; ext = close[i]; ext_i = i
            if trend >= 0:
                if close[i] > ext:
                    ext = close[i]; ext_i = i
                elif (close[i] - ext) / ext * 100 <= -floor_pct:
                    trend = -1; ext = close[i]; ext_i = i  # 고점 — 단고 폐기, 추적만
    bars["sigB"] = sigB
    bars["sigS"] = sigS
    bars["zz_amp"] = amp
    return bars


def _analyze_zigzag(df: pd.DataFrame, code: str | None) -> tuple:
    """ZigZag 모드 — analyze_minute_bars 반환 시그니처(7-tuple) 호환.

    강망치 swing 저점 매수 시그널만 반환 (단고 폐기). sigB 발화 = STRONG 단저.
    sc_buy 자리에 swing 진폭(%). 단고(sigS/g_sell)는 항상 False/NEUTRAL.
    """
    classify_zigzag(df)
    last = df.iloc[-1]
    sigB = bool(last.get("sigB", False))
    if not sigB:
        return False, False, None, 0.0, "NEUTRAL", 0.0, "NEUTRAL"
    amp = float(last["zz_amp"]) if pd.notna(last.get("zz_amp")) else 0.0
    reason = f"강망치 단저 swing-low 진폭 {amp:.2f}% (STRONG)"
    return True, False, reason, amp, "STRONG", 0.0, "NEUTRAL"


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
    """KIS 1분봉 fetch 결과 → 마지막 봉의 강망치 단저 시그널 (v4, 2026-05-30).

    **oversold sigB/sigS (v11) 및 단고(매도) STRONG 은 완전 폐기.** 라이브는 강망치
    swing 저점 매수 시그널만 사용 (`classify_zigzag` / `_analyze_zigzag`). OOS 검증
    근거는 docs/scalping-redesign-2026-05-27.md §v4 + data/journal/2026-05-29.md.

    Args:
        bars_minute: KIS 1분봉 (≥ 25 봉 필요)
        code: 종목 코드 (현재 미사용 — 글로벌 룰, per-stock weight 폐기).

    Returns:
        (sigB, sigS, reason, score_buy, grade_buy, score_sell, grade_sell)
        — sigS/grade_sell 은 항상 False/NEUTRAL (단고 폐기). score 자리엔 swing 진폭.

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
    add_swing_labels(df, lookback=3)
    return _analyze_zigzag(df, code)


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
