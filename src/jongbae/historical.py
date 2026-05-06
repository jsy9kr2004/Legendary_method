"""4-Layer Historical 갭상 통계 (R5).

각 종배 후보에 대해 lookback 252거래일(약 1년)의 일봉 데이터에서
유사 사례를 4단계로 매칭하고 다음날 갭상 통계를 계산.

Layer 1 — 전체 강한 양봉:
    조건: 일봉 수익률 >= +20%
    의미: 통계적 유의성 우선 (표본 큼)

Layer 2 — 상한가 사례만:
    조건: 일봉 수익률 >= +29.5%
    의미: 강한 시그널만 추출

Layer 3 — 종가 위치 매칭:
    조건: Layer 2 + 종가 위치 ±2% 일치
    종가 위치 = (close - low) / (high - low)
    의미: 오늘과 가장 유사한 마감 형태

Layer 4 — 종가 위치 + 고점 도달 시각 매칭:
    ⚠ v0 미구현: 분봉 히스토리 데이터 부재 (data-infra.md 참조)
    v1에서 분봉 적재 누적 후 구현 예정.

계산 메트릭 (각 Layer):
    n               사례 수
    p               갭상 확률 (다음날 시가 > 전일 종가)
    avg_gap         평균 갭(%)
    median_gap      중앙값 갭(%)
    std_gap         갭 표준편차
    avg_gap_when_up 갭상 시 평균 갭(%) — 사이징 Kelly 의 W 계산용
    avg_gap_when_dn 갭하 시 평균 갭(%) (절대값) — Kelly 의 L 계산용
    avg_close_return 다음날 종가 수익률 평균(%)
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd
from loguru import logger

LAYER1_RETURN_THRESHOLD = 20.0
LAYER2_RETURN_THRESHOLD = 29.5
CLOSE_POSITION_TOLERANCE = 0.02   # ±2%
LOOKBACK_TRADING_DAYS = 252
MIN_SAMPLES_FOR_CANDIDATE = 5     # n<5 이면 후보 제외 (R4 (c))


def close_position(open_p: float, high: float, low: float, close: float) -> float:
    """일봉의 종가 위치(0=저가 마감, 1=고가 마감).

    정량 정의:
        close_position = (close - low) / (high - low)
        high == low 인 경우 (변동 없음) → 0.5
    """
    if high == low:
        return 0.5
    return (close - low) / (high - low)


def _compute_returns(df: pd.DataFrame) -> pd.DataFrame:
    """일봉 DF에 daily_return, next_day 메트릭 추가.

    Args:
        df: 단일 종목의 일봉, date 오름차순 정렬 가정.
            columns: code, date, open, high, low, close, ...

    Returns:
        원본 + [daily_return, close_pos, next_open, next_close, gap_pct, next_close_return]
    """
    out = df.sort_values("date").copy()
    out["prev_close"] = out["close"].shift(1)
    out["daily_return"] = (out["close"] - out["prev_close"]) / out["prev_close"] * 100.0
    out["close_pos"] = (out["close"] - out["low"]) / (out["high"] - out["low"]).replace(0, np.nan)
    out["close_pos"] = out["close_pos"].fillna(0.5)
    out["next_open"] = out["open"].shift(-1)
    out["next_close"] = out["close"].shift(-1)
    out["gap_pct"] = (out["next_open"] - out["close"]) / out["close"] * 100.0
    out["next_close_return"] = (out["next_close"] - out["close"]) / out["close"] * 100.0
    return out


def _gap_metrics(matched: pd.DataFrame) -> dict[str, Any]:
    """매칭된 사례 DataFrame → 갭상 통계 dict."""
    if matched.empty or "gap_pct" not in matched.columns:
        return {
            "n": 0, "p": float("nan"),
            "avg_gap": float("nan"), "median_gap": float("nan"), "std_gap": float("nan"),
            "avg_gap_when_up": float("nan"), "avg_gap_when_dn": float("nan"),
            "avg_close_return": float("nan"),
        }
    valid = matched.dropna(subset=["gap_pct"])
    n = len(valid)
    if n == 0:
        return {
            "n": 0, "p": float("nan"),
            "avg_gap": float("nan"), "median_gap": float("nan"), "std_gap": float("nan"),
            "avg_gap_when_up": float("nan"), "avg_gap_when_dn": float("nan"),
            "avg_close_return": float("nan"),
        }
    gaps = valid["gap_pct"].astype(float)
    up = gaps[gaps > 0]
    dn = gaps[gaps <= 0]
    p = len(up) / n
    return {
        "n": n,
        "p": p,
        "avg_gap": float(gaps.mean()),
        "median_gap": float(gaps.median()),
        "std_gap": float(gaps.std(ddof=0)) if n > 1 else 0.0,
        "avg_gap_when_up": float(up.mean()) if len(up) > 0 else float("nan"),
        "avg_gap_when_dn": float(abs(dn.mean())) if len(dn) > 0 else float("nan"),
        "avg_close_return": float(valid["next_close_return"].mean()),
    }


def _filter_lookback(df: pd.DataFrame, today: date, lookback_days: int) -> pd.DataFrame:
    """today 기준 약 lookback_days 거래일 이전까지의 데이터만 (cross-stock pool 보존).

    주의:
        과거 버전에서 `.tail(lookback_days)` 를 적용했는데, 멀티-코드 long-format
        에서는 이게 마지막 N행만 남겨 대부분의 종목 historical 사례를 잘라버림.
        cutoff(달력일) 으로만 제한하고 tail 은 적용하지 않는다.
    """
    if df.empty:
        return df
    # 거래일 → 달력일 환산 (252영업일 ≈ 365 + alpha)
    cutoff = today - timedelta(days=int(lookback_days * 1.5))
    out = df[df["date"] >= cutoff]
    out = out[out["date"] < today]  # 오늘 데이터 제외 (look-ahead 방지)
    return out.sort_values("date")


def historical_4layer(
    daily_ohlcv: pd.DataFrame,
    today_close_pos: float,
    today: date,
    lookback_days: int = LOOKBACK_TRADING_DAYS,
) -> dict[str, dict[str, Any]]:
    """전체 일봉 데이터에서 4-Layer 통계 계산.

    Args:
        daily_ohlcv: 전종목 일봉 long format. columns: code, date, open, high, low, close, ...
                     Layer 1~3은 모든 종목의 사례를 풀(pool)로 사용 (cross-stock matching).
        today_close_pos: 오늘 후보 종목의 종가 위치 (Layer 3 매칭용).
        today: 오늘 날짜 (lookback 기준).
        lookback_days: 과거 몇 거래일까지 볼지.

    Returns:
        {"layer1": {n,p,avg_gap,...}, "layer2": {...}, "layer3": {...}, "layer4": {note: "v1"}}

    주의:
        Layer 4 는 분봉 히스토리 부재로 v0 미구현.
        결과 dict 에 {"n": 0, "note": "v1: 분봉 데이터 적재 후 구현"} 으로 채워 넣음.

    설계 결정 (D2, v0):
        Layer 1~3 은 cross-stock pool 로 매칭한다 — 즉 모든 종목의 historical
        사례를 한 풀로 섞어서 갭상 통계를 계산. 표본 확보엔 유리하지만 종목별
        고유 패턴(테마 의존성, 유동성 차이 등)은 못 잡는다.
        v1 에서 종목별 Layer 추가 + 표본 충분할 때만 사용하는 hybrid 검토.
    """
    if daily_ohlcv.empty:
        empty = {"n": 0, "p": float("nan"), "avg_gap": float("nan"),
                 "median_gap": float("nan"), "std_gap": float("nan"),
                 "avg_gap_when_up": float("nan"), "avg_gap_when_dn": float("nan"),
                 "avg_close_return": float("nan")}
        return {"layer1": empty, "layer2": empty, "layer3": empty,
                "layer4": {**empty, "note": "v1: 분봉 데이터 적재 후 구현"}}

    # 종목별로 returns 계산 (groupby로 shift 정확하게)
    enriched_parts = []
    for _, group in daily_ohlcv.groupby("code"):
        enriched_parts.append(_compute_returns(group))
    enriched = pd.concat(enriched_parts, ignore_index=True)

    in_window = _filter_lookback(enriched, today, lookback_days)

    layer1 = in_window[in_window["daily_return"] >= LAYER1_RETURN_THRESHOLD]
    layer2 = in_window[in_window["daily_return"] >= LAYER2_RETURN_THRESHOLD]
    layer3 = layer2[
        (layer2["close_pos"] >= today_close_pos - CLOSE_POSITION_TOLERANCE)
        & (layer2["close_pos"] <= today_close_pos + CLOSE_POSITION_TOLERANCE)
    ]

    result = {
        "layer1": _gap_metrics(layer1),
        "layer2": _gap_metrics(layer2),
        "layer3": _gap_metrics(layer3),
        "layer4": {
            **_gap_metrics(pd.DataFrame()),
            "note": "v1: 분봉 데이터 적재 후 구현",
        },
    }
    logger.debug(
        f"4-Layer 통계 (today={today}, close_pos={today_close_pos:.2f}): "
        f"L1 n={result['layer1']['n']}, L2 n={result['layer2']['n']}, L3 n={result['layer3']['n']}"
    )
    return result


def has_enough_samples(layer_stats: dict[str, Any], min_n: int = MIN_SAMPLES_FOR_CANDIDATE) -> bool:
    """R4 (c): historical 사례 >= 5건 인지 검증."""
    return layer_stats.get("n", 0) >= min_n


def pick_sizing_layer(layers: dict[str, dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    """사이징 기준 Layer 선택.

    원칙: Layer 3 (종가 위치 매칭) 기준.
    표본 부족(n < 5)이면 Layer 2 → Layer 1 순으로 fallback.

    Returns:
        (layer_name, layer_stats)
    """
    for name in ("layer3", "layer2", "layer1"):
        stats = layers.get(name, {})
        if has_enough_samples(stats):
            return name, stats
    return "layer1", layers.get("layer1", {})
