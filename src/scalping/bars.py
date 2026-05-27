"""분봉 OHLC 집계 + 평균회귀 지표.

`docs/scalping-redesign-2026-05-27.md` §2 단저단고 v3 정의의 입력 데이터 모듈.

tick log → N분봉 OHLC + (MA5/MA20, zscore, Bollinger, RSI, Stochastic %K, ATR,
거래대금 spike) 산출. 백테스트 + 라이브 worker 양쪽에서 재사용.

본질: 단저단고 시그널의 지표 의존성을 raw price 기반으로 통합. SK하이닉스 같은
비-STRONG 종목도 모니터링 universe 에 들어가면 즉시 산출 가능 (worker fetch
의존 X).
"""
from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd


def aggregate_bars(
    ticks: pd.DataFrame,
    freq: str = "3min",
    price_col: str = "price",
    trading_value_col: str = "trading_value",
) -> pd.DataFrame:
    """tick log → N분봉 OHLC + 분봉 거래대금.

    ticks: ts-indexed DataFrame (또는 ts 컬럼 있는 DataFrame). price + trading_value 필요.
    freq: pandas resample freq 문자열 (default "3min").

    반환: ts-indexed DataFrame (open / high / low / close / tick_n / bar_tv).
    bar_tv = 누적 trading_value 의 분봉 diff (양수 clip).
    """
    if not isinstance(ticks.index, pd.DatetimeIndex):
        ticks = ticks.set_index("ts")
    ohlc = (
        ticks[price_col]
        .resample(freq, label="left", closed="left")
        .agg(["first", "max", "min", "last", "count"])
        .rename(columns={"first": "open", "max": "high", "min": "low", "last": "close", "count": "tick_n"})
        .dropna()
    )
    if trading_value_col in ticks.columns:
        tv = ticks[trading_value_col].resample(freq, label="left", closed="left").last().reindex(ohlc.index)
        ohlc["bar_tv"] = tv.diff().clip(lower=0).fillna(0)
    else:
        ohlc["bar_tv"] = 0.0
    return ohlc


def add_candle_features(bars: pd.DataFrame) -> pd.DataFrame:
    """봉 형태 컬럼 추가 (body / range / wick / consec_bear / consec_bull).

    in-place 수정. bars 반환.
    """
    bars["body"] = bars["close"] - bars["open"]
    bars["range"] = bars["high"] - bars["low"]
    bars["candle"] = np.where(bars["body"] > 0, "bull", np.where(bars["body"] < 0, "bear", "doji"))
    upper = bars["high"] - bars[["open", "close"]].max(axis=1)
    lower = bars[["open", "close"]].min(axis=1) - bars["low"]
    bars["upper_wick_pct"] = np.where(bars["range"] > 0, upper / bars["range"], 0.0)
    bars["lower_wick_pct"] = np.where(bars["range"] > 0, lower / bars["range"], 0.0)
    bars["body_pct"] = np.where(bars["range"] > 0, bars["body"].abs() / bars["range"], 0.0)

    is_bear = (bars["candle"] == "bear").astype(int).values
    is_bull = (bars["candle"] == "bull").astype(int).values
    cb = np.zeros(len(bars), dtype=int)
    cu = np.zeros(len(bars), dtype=int)
    c_bear = c_bull = 0
    for i in range(len(bars)):
        if is_bear[i] == 1:
            c_bear += 1
            c_bull = 0
        elif is_bull[i] == 1:
            c_bull += 1
            c_bear = 0
        else:
            c_bear = c_bull = 0
        cb[i] = c_bear
        cu[i] = c_bull
    bars["consec_bear"] = cb
    bars["consec_bull"] = cu
    return bars


def add_mean_reversion(bars: pd.DataFrame, n_ma: int = 20, n_rsi: int = 14, n_stoch: int = 14) -> pd.DataFrame:
    """평균회귀 지표 컬럼 추가 (MA / zscore / Bollinger / RSI / Stoch %K / ATR).

    AUC 기반 변별력 (5/27 분석):
      ma5_dist_pct 0.91 / zscore 0.78 / stoch_k 0.78 / RSI 0.67 / atr 0.73.

    in-place 수정. bars 반환.
    """
    bars["ma5"] = bars["close"].rolling(5).mean()
    bars["ma20"] = bars["close"].rolling(n_ma).mean()
    bars["std20"] = bars["close"].rolling(n_ma).std()
    bars["zscore"] = (bars["close"] - bars["ma20"]) / bars["std20"]
    bars["bb_lower"] = bars["ma20"] - 2 * bars["std20"]
    bars["bb_upper"] = bars["ma20"] + 2 * bars["std20"]
    bars["bb_pos"] = (bars["close"] - bars["ma20"]) / (2 * bars["std20"])
    bars["ma5_dist_pct"] = (bars["close"] / bars["ma5"] - 1) * 100
    bars["ma20_dist_pct"] = (bars["close"] / bars["ma20"] - 1) * 100

    delta = bars["close"].diff()
    gain = delta.clip(lower=0).rolling(n_rsi).mean()
    loss = -delta.clip(upper=0).rolling(n_rsi).mean()
    bars["rsi"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))

    low_n = bars["low"].rolling(n_stoch).min()
    high_n = bars["high"].rolling(n_stoch).max()
    bars["stoch_k"] = 100 * (bars["close"] - low_n) / (high_n - low_n).replace(0, np.nan)
    bars["williams_r"] = -100 * (high_n - bars["close"]) / (high_n - low_n).replace(0, np.nan)

    bars["atr"] = bars["range"].rolling(14).mean()
    bars["atr_pct"] = bars["atr"] / bars["close"] * 100

    bars["vol_ma20"] = bars["bar_tv"].rolling(n_ma).mean()
    bars["vol_spike"] = bars["bar_tv"] / bars["vol_ma20"].replace(0, np.nan)

    # VWAP (cumulative typical price × volume / cumulative volume).
    # 단저단고 v10b 의 vwap_above feature 입력.
    if bars["bar_tv"].sum() > 0:
        vol_proxy = bars["bar_tv"] / bars["close"]
        cum_vol = vol_proxy.cumsum()
        cum_tp_vol = (bars["close"] * vol_proxy).cumsum()
        bars["vwap"] = cum_tp_vol / cum_vol.replace(0, np.nan)
        bars["vwap_dist_pct"] = (bars["close"] / bars["vwap"] - 1) * 100
    else:
        bars["vwap"] = np.nan
        bars["vwap_dist_pct"] = np.nan
    return bars


def add_swing_labels(bars: pd.DataFrame, lookback: int = 3) -> pd.DataFrame:
    """swing low/high 라벨 추가 (직전 N봉 low/high 만 봄, look-ahead 없음).

    is_local_low = 현재 봉 low 가 직전 N봉 low 최소보다 낮음.
    is_local_high = 현재 봉 high 가 직전 N봉 high 최대보다 높음.

    주의: 진짜 swing 극점 확인은 직후 봉 필요 (look-ahead). 본 함수는 실시간
    매수 시점 sigB 후보 식별용 — false swing 비율 ~43% (5/27 검증).
    """
    bars["prev_low_min"] = bars["low"].shift(1).rolling(lookback).min()
    bars["prev_high_max"] = bars["high"].shift(1).rolling(lookback).max()
    bars["is_local_low"] = bars["low"] < bars["prev_low_min"]
    bars["is_local_high"] = bars["high"] > bars["prev_high_max"]
    return bars


def build_bars(
    ticks: pd.DataFrame,
    freq: str = "3min",
    price_col: str = "price",
    trading_value_col: str = "trading_value",
    swing_lookback: int = 3,
) -> pd.DataFrame:
    """전체 파이프라인 — tick log → N분봉 + candle + 평균회귀 + swing 라벨.

    한 줄 사용:
      bars = build_bars(ticks_df)

    반환 컬럼: open/high/low/close/tick_n/bar_tv + candle/body/range/wick +
              ma5/ma20/std20/zscore/bb_*/rsi/stoch_k/williams_r/atr/vol_spike +
              is_local_low/is_local_high.
    """
    bars = aggregate_bars(ticks, freq=freq, price_col=price_col, trading_value_col=trading_value_col)
    if len(bars) < 25:
        return bars
    add_candle_features(bars)
    add_mean_reversion(bars)
    add_swing_labels(bars, lookback=swing_lookback)
    return bars
