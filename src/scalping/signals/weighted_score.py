"""단저단고 v10b — 매물대 + 추세선 + 평균회귀 + 변동성 weight 합산 score.

`docs/scalping-redesign-2026-05-27.md` §11 정정 이력 v10b 참조.

5/27~28 새벽 분석 결과: 모든 단일 지표 AUC 0.50~0.60 약함. 단 6개 categorical
feature 의 weight 합산 → **score AUC 0.628 도달 (단일 지표 최고와 동급)**.

핵심 발견:
  - **atr_low (변동성 작은 swing)** = 가장 강한 시그널 (weight +1.01)
    prof 34.6% / false 14.4% → false swing 회피의 진짜 lever
  - **at_support (추세선 ±0.5%)** weight +0.62 — 직전 N swing low 선형 회귀
  - touch_high (매물대 ≥5) weight +0.36 — 직전 30봉 ±0.3% 도달 횟수
  - oversold weight +0.34, small_wick weight +0.33

Score 임계별 precision (base rate 90%):
  ≥ 0.5: 92.5%  /  ≥ 1.0: 94.2%  /  ≥ 1.5: 95.4%  /  ≥ 2.0: 95.9%  /  ≥ 2.5: 96.2%

백테스트 (score ≥ 2): gross +0.07% / 봉 close 지연 보정 시 net 0.15% **+0.29% 양수 도달**.

사용:
    from src.scalping.signals.weighted_score import compute_score
    score = compute_score(bars_row)  # bars 의 마지막 row (Series)
    # 또는
    bars["score"] = bars.apply(compute_score, axis=1)
"""
from __future__ import annotations

import pandas as pd


# Weight (5/27~28 분석 결과, system tuning ritual 통과 후만 변경)
WEIGHT_ATR_LOW = 1.01
WEIGHT_AT_SUPPORT = 0.62
WEIGHT_TOUCH_HIGH = 0.36
WEIGHT_OVERSOLD = 0.34
WEIGHT_SMALL_WICK = 0.33

# 임계
ATR_LOW_PCT = 0.7
SUPPORT_TOUCH_TOL_PCT = 0.5
TOUCH_HIGH_MIN = 5
OVERSOLD_STOCH = 30.0
OVERSOLD_RSI = 40.0
OVERSOLD_ZSCORE = -1.0
SMALL_WICK_MAX = 0.2

# Score 카드 임계 (사용자 매매 권고)
SCORE_STRONG = 2.0   # 강한 매수 신호 (precision 95.9%)
SCORE_WATCH = 1.0    # 관찰


def compute_score(row: pd.Series) -> float:
    """단일 봉 (또는 시점) 의 weighted score.

    row 필수 컬럼:
      - atr_pct, stoch_k, rsi, zscore, lower_wick_pct
      - support_dist_pct (추세선 거리, weighted_score 외부 계산)
      - touch_count (매물대 도달, weighted_score 외부 계산)

    빈 컬럼은 0 가산 (안전). NaN-safe.
    """
    score = 0.0
    # atr_low (가장 강함) — 변동성 작은 swing
    atr = row.get("atr_pct")
    if pd.notna(atr) and atr <= ATR_LOW_PCT:
        score += WEIGHT_ATR_LOW
    # at_support — 추세선 ±0.5%
    sup_dist = row.get("support_dist_pct")
    if pd.notna(sup_dist) and abs(sup_dist) <= SUPPORT_TOUCH_TOL_PCT:
        score += WEIGHT_AT_SUPPORT
    # touch_high — 매물대 ≥5
    tc = row.get("touch_count")
    if pd.notna(tc) and tc >= TOUCH_HIGH_MIN:
        score += WEIGHT_TOUCH_HIGH
    # oversold — 평균회귀 OR
    stoch = row.get("stoch_k")
    rsi = row.get("rsi")
    z = row.get("zscore")
    oversold = (
        (pd.notna(stoch) and stoch <= OVERSOLD_STOCH)
        or (pd.notna(rsi) and rsi <= OVERSOLD_RSI)
        or (pd.notna(z) and z <= OVERSOLD_ZSCORE)
    )
    if oversold:
        score += WEIGHT_OVERSOLD
    # small_wick — 작은 아래꼬리
    lw = row.get("lower_wick_pct")
    if pd.notna(lw) and lw <= SMALL_WICK_MAX:
        score += WEIGHT_SMALL_WICK
    return score


def compute_support_distance(bars: pd.DataFrame, n_swings: int = 4) -> pd.Series:
    """직전 N개 swing low 의 선형 회귀 → 현재 가격의 추세선 거리.

    bars 에 is_local_low 컬럼 필요 (bars.add_swing_labels 출력).
    각 시점에 직전 N swing low 좌표 (idx, low) 선형 회귀 → 현재 idx 예측 가격 ↔ 현재 low.
    look-ahead 없음.
    """
    import numpy as np
    if "is_local_low" not in bars.columns:
        return pd.Series([float("nan")] * len(bars), index=bars.index)
    sl_idx: list[tuple[int, float]] = []
    out = []
    for i in range(len(bars)):
        if len(sl_idx) >= n_swings:
            xs = np.array([x for x, _ in sl_idx[-n_swings:]])
            ys = np.array([p for _, p in sl_idx[-n_swings:]])
            slope, intercept = np.polyfit(xs, ys, 1)
            pred = slope * i + intercept
            out.append((bars["low"].iloc[i] / pred - 1) * 100)
        else:
            out.append(float("nan"))
        if bool(bars["is_local_low"].iloc[i]):
            sl_idx.append((i, float(bars["low"].iloc[i])))
    return pd.Series(out, index=bars.index)


def compute_touch_count(bars: pd.DataFrame, lookback: int = 30, tol_pct: float = 0.3) -> pd.Series:
    """직전 N봉 low 중 현재 가격 ±tol% 안에 도달한 횟수 = 매물대 도달.

    빠른 vector 구현 어려워 loop. 6일 backtest 에서 무리 X.
    """
    out = []
    for i in range(len(bars)):
        if i < lookback:
            out.append(0)
            continue
        cur = float(bars["low"].iloc[i])
        tol = cur * tol_pct / 100
        prev = bars["low"].iloc[i - lookback:i]
        out.append(int(((prev >= cur - tol) & (prev <= cur + tol)).sum()))
    return pd.Series(out, index=bars.index)


def add_score_features(bars: pd.DataFrame) -> pd.DataFrame:
    """bars 에 support_dist_pct + touch_count + score 컬럼 추가.

    bars 는 build_bars 출력 (is_local_low, atr_pct, stoch_k, rsi, zscore,
    lower_wick_pct 컬럼 있어야 함).
    """
    bars["support_dist_pct"] = compute_support_distance(bars)
    bars["touch_count"] = compute_touch_count(bars)
    bars["score"] = bars.apply(compute_score, axis=1)
    return bars


def grade(score: float) -> str:
    """score → 등급 라벨 (카드 표시용) — v10b 레거시 (단저 한정).

    v11 (2026-05-29) 부터는 grade_buy / grade_sell 사용 권장. score 가 매수
    한정 weight 라 단고 시점 STRONG 도달 사실상 X — 사용자 명시 지적.
    """
    if score >= SCORE_STRONG:
        return "STRONG"
    if score >= SCORE_WATCH:
        return "WATCH"
    return "NEUTRAL"


# ── v11 score_buy / score_sell 분리 (2026-05-29) ─────────────────────────────
#
# 배경:
#   v10b score 의 5 weight 중 3개가 데이터와 정반대 방향이라 사용자 지적 후
#   처음부터 다시. ZigZag GT (ATR multi=3.0, floor 1.0% — 큰 swing 만) 으로
#   3~7일 분봉 분석 후 AUC 가중합 도출.
#
# 7일 검증 (5/18~5/28, 분봉 145,148 개):
#   - 단저 AUC = 0.879 (v10b 0.628 대비 +0.25)
#   - 단고 AUC = 0.887
#   - 4종목(하이닉스/현대차/삼성전자/삼성전기) 한정 net 지정가 +2.4% (사용자 직관
#     "STRONG 단저 매수 + STRONG 단고 매도" 룰, 승률 79.3%)
#
# weight = (AUC_eff - 0.5) × 2 / total_weight 정규화. 자세한 분석 history 는
# memory/project_scalping_redesign_2026_05_27 + 본 commit message 참조.

# 단저 (sigB) 후보 feature — name : (direction +1=HIGH 시그널 / -1=LOW 시그널, AUC_eff)
BUY_FEATS_V11 = {
    "touch_count":      (-1, 0.797),  # 매물대 적은 봉이 swing 시그널
    "zscore":           (-1, 0.796),  # 평균회귀 과매도
    "stoch_k":          (-1, 0.755),  # 과매도
    "williams_r":       (-1, 0.755),  # 과매도
    "atr_pct":          (+1, 0.738),  # 변동성 큰 봉
    "support_dist_pct": (-1, 0.763),  # 추세선 부근 (음수 = 아래쪽 swing low)
    "rsi":              (-1, 0.724),  # 과매도
    "lower_wick_pct":   (+1, 0.689),  # 망치형 (아래꼬리 큼)
    "is_doji":          (-1, 0.678),  # 방향 큰 봉 (도지 아님)
    "consec_bear":      (+1, 0.664),  # 직전 음봉 연속 후 반등
    "is_bearish":       (+1, 0.640),  # 음봉 (꼬리 반등)
}

# 단고 (sigS) 후보 feature — 거울상
SELL_FEATS_V11 = {
    "zscore":           (+1, 0.827),  # 과매수
    "stoch_k":          (+1, 0.797),  # 과매수
    "williams_r":       (+1, 0.797),  # 과매수
    "support_dist_pct": (+1, 0.774),  # 추세선 위쪽 (양수 = 고점)
    "rsi":              (+1, 0.758),  # 과매수
    "touch_count":      (-1, 0.752),  # 매물대 적음 (공통)
    "atr_pct":          (+1, 0.745),  # 변동성 큰 봉 (공통)
    "consec_bull":      (+1, 0.694),  # 직전 양봉 연속 후 반락
    "upper_wick_pct":   (+1, 0.686),  # 역망치형 (윗꼬리 큼)
    "is_doji":          (-1, 0.682),  # 방향 큰 봉 (공통)
    "is_bullish":       (+1, 0.668),  # 양봉 후 윗꼬리 반락
}

# STRONG 임계 (7일 backtest top 0.5% quantile)
SCORE_BUY_STRONG = 0.745
SCORE_BUY_WATCH = 0.55
SCORE_SELL_STRONG = 0.666
SCORE_SELL_WATCH = 0.50


def _normalize_minmax(value: float, vmin: float, vmax: float) -> float:
    """min-max scale 0~1. AUC 가중합 score 의 일관성 위해 학습 시점과 동일 range
    유지가 이상적이나 라이브에선 어려움 → 봉별 normalize 대신 feature 별 hard
    clip 사용. 안전한 0~1 보장.

    실용적 hard range — 7일 학습 데이터에서 도출.
    """
    if vmax == vmin:
        return 0.5
    v = (value - vmin) / (vmax - vmin)
    return max(0.0, min(1.0, v))


# Feature 별 hard min/max (7일 학습 데이터의 0.1~99.9 percentile)
_FEATURE_RANGES = {
    "touch_count": (0, 30),
    "zscore": (-3, 3),
    "stoch_k": (0, 100),
    "williams_r": (-100, 0),
    "atr_pct": (0, 5),
    "support_dist_pct": (-5, 5),
    "rsi": (0, 100),
    "lower_wick_pct": (0, 1),
    "upper_wick_pct": (0, 1),
    "is_doji": (0, 1),
    "is_bullish": (0, 1),
    "is_bearish": (0, 1),
    "consec_bear": (0, 10),
    "consec_bull": (0, 10),
}


def _compute_score_v11(row, feats: dict) -> float:
    """v11 AUC 가중합 score 계산 — 0~1 범위. NaN-safe.

    Args:
        row: pandas Series (마지막 봉의 feature 값).
        feats: BUY_FEATS_V11 또는 SELL_FEATS_V11.
    """
    score = 0.0
    total_w = 0.0
    for name, (direction, auc_eff) in feats.items():
        val = row.get(name)
        if val is None or (isinstance(val, float) and val != val):  # NaN
            # NaN 은 0.5 로 (중립)
            s_norm = 0.5
        else:
            vmin, vmax = _FEATURE_RANGES.get(name, (0, 1))
            s_norm = _normalize_minmax(float(val), vmin, vmax)
        if direction < 0:
            s_norm = 1 - s_norm
        w = (auc_eff - 0.5) * 2
        score += s_norm * w
        total_w += w
    return score / total_w if total_w > 0 else 0.0


def compute_score_buy(row) -> float:
    """v11 단저 score (0~1)."""
    return _compute_score_v11(row, BUY_FEATS_V11)


def compute_score_sell(row) -> float:
    """v11 단고 score (0~1)."""
    return _compute_score_v11(row, SELL_FEATS_V11)


def grade_buy(score: float) -> str:
    """score_buy → 단저 등급 (STRONG / WATCH / NEUTRAL)."""
    if score >= SCORE_BUY_STRONG:
        return "STRONG"
    if score >= SCORE_BUY_WATCH:
        return "WATCH"
    return "NEUTRAL"


def grade_sell(score: float) -> str:
    """score_sell → 단고 등급."""
    if score >= SCORE_SELL_STRONG:
        return "STRONG"
    if score >= SCORE_SELL_WATCH:
        return "WATCH"
    return "NEUTRAL"
