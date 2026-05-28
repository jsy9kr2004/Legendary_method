"""단저단고 Ground Truth 추출 — ZigZag + ATR 정규화 reversal threshold.

배경 (2026-05-29 사용자 명시):
    v10b score 가 매수(단저) 한정 weight 로 구성. 단고에 동일 score/grade 적용은
    부정합. ground truth 부터 재추출 → 단저/단고 별 후보 지표 AUC 분석 → score
    v11 (buy + sell 분리) 도출. ritual 정통 절차.

    GT 정의: 5/27 v10b 의 `is_local_low` (직전 3봉 단순 lookback) 는 false swing
    43% (정정 이력 명시). 사용자 지적: 차트 변동성에 따라 고정 window 한계 —
    변동성 큰 종목은 10분 안 2~3번 swing / 작은 종목은 하루 1~2번. 해결 = ZigZag
    알고리즘 + 종목별 ATR 로 reversal threshold 자동 정규화.

알고리즘:
    1. 분봉 close + ATR(N) 준비
    2. reversal threshold = ATR_pct × multiplier (default 1.0)
    3. 상태 머신 ZigZag:
       - up 상태: 최고가 갱신 시 ref 갱신, ref - threshold 도달 시 → 직전 ref 가 sigS_gt (단고 확정)
       - down 상태: 최저가 갱신 시 ref 갱신, ref + threshold 도달 시 → 직전 ref 가 sigB_gt (단저 확정)
    4. forward window 불필요 — chronological peak/valley 식별 자체로 정통

사용:
    from src.analysis.ground_truth import mark_zigzag_gt
    bars = build_bars(tick_df)  # close, high, low, atr_pct 컬럼 포함
    bars = mark_zigzag_gt(bars, atr_multiplier=1.0)
    # bars 에 sigB_gt / sigS_gt boolean 컬럼 추가
"""
from __future__ import annotations

import pandas as pd

DEFAULT_ATR_MULTIPLIER = 1.0
MIN_THRESHOLD_PCT = 0.3  # ATR 매우 작은 종목 floor (0.3% reversal 미만은 noise)


def mark_zigzag_gt(
    bars: pd.DataFrame,
    atr_multiplier: float = DEFAULT_ATR_MULTIPLIER,
    min_threshold_pct: float = MIN_THRESHOLD_PCT,
) -> pd.DataFrame:
    """분봉 데이터에 ZigZag GT 마킹 — sigB_gt / sigS_gt boolean 컬럼 추가.

    Args:
        bars: build_bars 결과 (close, high, low, atr_pct 컬럼 필수).
        atr_multiplier: reversal threshold = ATR_pct × multiplier (단위 %).
        min_threshold_pct: ATR 매우 작은 종목 floor (default 0.3%).

    Returns:
        bars (in-place 갱신) + sigB_gt / sigS_gt 컬럼.

    Note:
        - GT 마킹 위치 = peak/valley 가 확정된 시점 (다음 reversal 후 retro 마킹).
          → live 사용 X (look-ahead). 사후 분석 용도만.
        - 시작 state="neutral" → 첫 reversal threshold 도달 봉부터 추적.
    """
    out = bars.copy()
    n = len(out)
    out["sigB_gt"] = False
    out["sigS_gt"] = False
    if n < 2:
        return out

    close = out["close"].to_numpy()
    high = out["high"].to_numpy()
    low = out["low"].to_numpy()
    atr_pct = out["atr_pct"].fillna(1.0).to_numpy()

    # 각 봉의 threshold (%) — ATR_pct × multiplier, 최소 min_threshold_pct
    thresholds = [max(float(a) * atr_multiplier, min_threshold_pct) for a in atr_pct]

    sigB_idx: list[int] = []
    sigS_idx: list[int] = []

    state = "neutral"
    ref_idx = 0
    ref_price = float(close[0])

    for i in range(1, n):
        cur_high = float(high[i])
        cur_low = float(low[i])
        thr_pct = thresholds[i]

        if state == "neutral":
            up_change = (cur_high - ref_price) / ref_price * 100
            down_change = (cur_low - ref_price) / ref_price * 100
            if up_change >= thr_pct:
                state = "up"
                ref_idx = i
                ref_price = cur_high
            elif down_change <= -thr_pct:
                state = "down"
                ref_idx = i
                ref_price = cur_low

        elif state == "up":
            if cur_high > ref_price:
                ref_idx = i
                ref_price = cur_high
            else:
                down_change = (cur_low - ref_price) / ref_price * 100
                if down_change <= -thr_pct:
                    sigS_idx.append(ref_idx)
                    state = "down"
                    ref_idx = i
                    ref_price = cur_low

        elif state == "down":
            if cur_low < ref_price:
                ref_idx = i
                ref_price = cur_low
            else:
                up_change = (cur_high - ref_price) / ref_price * 100
                if up_change >= thr_pct:
                    sigB_idx.append(ref_idx)
                    state = "up"
                    ref_idx = i
                    ref_price = cur_high

    # in-place 마킹
    for idx in sigB_idx:
        out.iat[idx, out.columns.get_loc("sigB_gt")] = True
    for idx in sigS_idx:
        out.iat[idx, out.columns.get_loc("sigS_gt")] = True

    return out
