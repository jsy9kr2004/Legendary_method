"""4-Layer Historical 갭상 통계 (Eod.GapStats).

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

from datetime import date, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
from loguru import logger

LAYER1_RETURN_THRESHOLD = 20.0
LAYER2_RETURN_THRESHOLD = 29.5
CLOSE_POSITION_TOLERANCE = 0.02   # ±2%
LOOKBACK_TRADING_DAYS = 252
MIN_SAMPLES_FOR_CANDIDATE = 5     # n<5 이면 후보 제외 (Eod.Pick (c))
MARKET_MA_WINDOW = 200            # KOSPI 200일 이평 (강세장 판정)
VOLUME_AVG_WINDOW = 20            # 거래량 평균 윈도우 (배수 계산용)
VOLUME_RATIO_TOLERANCE = 0.5      # ±0.5배 — Layer 매칭 폭


def close_position(open_p: float, high: float, low: float, close: float) -> float:
    """일봉의 종가 위치(0=저가 마감, 1=고가 마감).

    정량 정의:
        close_position = (close - low) / (high - low)
        high == low 인 경우 (변동 없음) → 0.5
    """
    if high == low:
        return 0.5
    return (close - low) / (high - low)


def historical_ret10_gap_stats(
    daily_ohlcv: pd.DataFrame,
    code: str,
    today: date,
    lookback: int = LOOKBACK_TRADING_DAYS,
) -> dict[str, Any]:
    """Eod.Pick v2 보조 지표 — 1년 lookback 동안 ret≥10% 발생 횟수 + 그중 갭상 비율.

    plan.md round 41 ④: "historical 갭상 비율 (1년 ret≥10 횟수 + 그중 갭상 횟수 +
    비율) 은 카드 보조 정보로만 표시, 컷으로 사용 X". 단골 종배 종목 식별용.

    backward-compat: 기존 호출자가 받던 {"n_ret10", "n_gap_up", "ratio"} 그대로 유지.

    Returns:
        {
            "n_ret10": int, "n_gap_up": int, "ratio": float,  # 기존 key (1년 ret≥10)
        }
    """
    aux = historical_aux_matrix(daily_ohlcv, code, today, lookback)
    cell = aux.get(("year", 10), {"n": 0, "n_gap_up": 0, "ratio": float("nan")})
    return {
        "n_ret10": cell["n"],
        "n_gap_up": cell["n_gap_up"],
        "ratio": cell["ratio"],
    }


# 사용자 정정 2026-05-21: ret 빈도 표시 세분화 (4 기간 × 3 임계 = 12 케이스).
# 기간: 1개월(21거래일) / 3개월(63) / 6개월(126) / 1년(252).
# ret 임계: ≥0% / ≥10% / ≥20%.
_PERIOD_LABELS = [
    ("month", 21),
    ("3month", 63),
    ("6month", 126),
    ("year", 252),
]
_RET_THRESHOLDS = [0.0, 10.0, 20.0]


def historical_aux_matrix(
    daily_ohlcv: pd.DataFrame,
    code: str,
    today: date,
    lookback: int = LOOKBACK_TRADING_DAYS,
) -> dict[tuple[str, int], dict[str, Any]]:
    """ret 빈도 + 갭상 비율 매트릭스 — 4 기간 × 3 임계.

    Args:
        daily_ohlcv: 전체 종목 일봉.
        code: 6자리 종목 코드.
        today: 기준 날짜 (이 날짜 직전 데이터만 사용).
        lookback: 최대 lookback (기본 252).

    Returns:
        {
            ("month", 0): {"n": ..., "n_gap_up": ..., "ratio": ...},
            ("month", 10): {...},
            ("month", 20): {...},
            ("3month", 0): {...}, ..., ("year", 20): {...},
        }
        n=0 시 ratio=NaN.
    """
    empty = {"n": 0, "n_gap_up": 0, "ratio": float("nan")}
    out: dict[tuple[str, int], dict[str, Any]] = {
        (p, int(t)): dict(empty) for p, _ in _PERIOD_LABELS for t in _RET_THRESHOLDS
    }

    if daily_ohlcv is None or daily_ohlcv.empty:
        return out

    own = daily_ohlcv[daily_ohlcv["code"] == code]
    if own.empty:
        return out

    own = own[own["date"] < today].sort_values("date").tail(lookback)
    if own.empty:
        return out

    enriched = _compute_returns(own)

    for period_label, period_days in _PERIOD_LABELS:
        period_df = enriched.tail(period_days)
        for ret_th in _RET_THRESHOLDS:
            qualifying = period_df[period_df["daily_return"] >= ret_th]
            n = int(len(qualifying))
            if n == 0:
                out[(period_label, int(ret_th))] = dict(empty)
                continue
            gap_valid = qualifying.dropna(subset=["gap_pct"])
            n_gap_up = int((gap_valid["gap_pct"] > 0).sum())
            ratio_base = len(gap_valid) if len(gap_valid) > 0 else n
            ratio = float(n_gap_up / ratio_base) if ratio_base > 0 else float("nan")
            out[(period_label, int(ret_th))] = {
                "n": n,
                "n_gap_up": n_gap_up,
                "ratio": ratio,
            }
    return out


def is_52w_high(
    daily_ohlcv: pd.DataFrame,
    code: str,
    today: date,
    today_high: int | float,
    window: int = LOOKBACK_TRADING_DAYS,
) -> bool | None:
    """Eod.Pick v2 (d) — 오늘 일중 고가가 직전 N거래일(기본 250 ≈ 52주) 종가 최고치를
    돌파했는지.

    종가 기준 비교 (HTS 신고가 표시 관례). 일중 고가 데이터를 today_high 로 받음 —
    snapshot.intraday_high 또는 fetch_quote 보강 결과.

    Args:
        daily_ohlcv: 전체 종목 일봉.
        code: 6자리 종목 코드.
        today: 기준 날짜.
        today_high: 오늘 일중 고가.
        window: lookback 거래일 (기본 250).

    Returns:
        True / False. 데이터 부족 (lookback 60일 미만 또는 today_high 0/NaN) 시 None.
    """
    if today_high is None or today_high <= 0:
        return None
    if daily_ohlcv is None or daily_ohlcv.empty:
        return None
    own = daily_ohlcv[(daily_ohlcv["code"] == code) & (daily_ohlcv["date"] < today)]
    if own.empty:
        return None
    own = own.sort_values("date").tail(window)
    if len(own) < 60:  # 데이터 부족
        return None
    past_max_close = float(own["close"].max())
    if past_max_close <= 0:
        return None
    return float(today_high) > past_max_close


def _compute_returns(df: pd.DataFrame) -> pd.DataFrame:
    """일봉 DF에 daily_return, next_day 메트릭 + 거래량 비율 추가.

    Args:
        df: 단일 종목의 일봉, date 오름차순 정렬 가정.
            columns: code, date, open, high, low, close, volume, ...

    Returns:
        원본 + [daily_return, close_pos, next_open, next_close, gap_pct,
                next_close_return, volume_ratio]
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
    # 거래량 비율 = 당일 volume / 직전 N일 평균 (자기 자신 제외)
    if "volume" in out.columns:
        avg = out["volume"].shift(1).rolling(window=VOLUME_AVG_WINDOW, min_periods=5).mean()
        out["volume_ratio"] = out["volume"] / avg
    else:
        out["volume_ratio"] = float("nan")
    return out


def _coerce_date(val: Any) -> date | None:
    """다양한 date-like 입력을 python date 로 변환. 실패 시 None."""
    if val is None:
        return None
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, str):
        s = val.replace("-", "").strip()
        if len(s) == 8 and s.isdigit():
            return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        return None
    if hasattr(val, "date") and callable(val.date):
        try:
            return val.date()
        except Exception:  # noqa: BLE001
            return None
    return None


def market_regime_timeline(
    kospi_daily: pd.DataFrame,
    ma_window: int = MARKET_MA_WINDOW,
) -> dict[date, bool]:
    """KOSPI 일봉 시계열 → 각 날짜의 ma_window 평균 위/아래 boolean.

    Args:
        kospi_daily: columns=[date, close], 오름차순 정렬 또는 미정렬. date 는
            python date / datetime / "YYYYMMDD" 문자열 / "YYYY-MM-DD" 문자열 OK.
        ma_window: moving average 윈도우 (기본 200일 이평).

    Returns:
        {python date: True(위) | False(아래)} — ma_window 일 이상의
        과거 데이터가 누적된 날짜만 포함. 비어 있으면 빈 dict.
    """
    if kospi_daily is None or kospi_daily.empty or len(kospi_daily) < ma_window:
        return {}
    df = kospi_daily.copy()
    df["_date"] = df["date"].apply(_coerce_date)
    df = df.dropna(subset=["_date"]).sort_values("_date").reset_index(drop=True)
    df["_ma"] = df["close"].rolling(window=ma_window).mean()
    result: dict[date, bool] = {}
    for _, row in df.iterrows():
        ma = row["_ma"]
        if ma == ma:  # not NaN
            result[row["_date"]] = bool(row["close"] > ma)
    return result


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
    today_strong_market: bool | None = None,
    market_regime_by_date: dict[date, bool] | None = None,
    today_volume_ratio: float | None = None,
    code: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Historical 갭상 통계 계산 (4 + 2 layer).

    기본 4 layer (M3):
        layer1 — 전체 +20%↑
        layer2 — 상한가 (+29.5%↑)
        layer3 — layer2 + 종가 위치 ±2%
        layer4 — layer3 + 분봉 고점 도달 시각 (v1 슬롯)

    추가 layer (M3+ 한국 단타 통설 매칭):
        layer3_strong_mkt — layer3 사례 중 매칭 날짜의 KOSPI ma200 regime이 오늘과 일치
        layer3_high_vol — layer3 사례 중 거래량 비율이 오늘 ±tolerance 범위
        둘 다 자동으로 Kelly에 반영 (pick_sizing_layer 가 좁은 layer 우선 사용).

    Args:
        daily_ohlcv: 전종목 일봉 long format. columns: code, date, open, high, low, close, volume, ...
        today_close_pos: 오늘 후보 종목의 종가 위치 (Layer 3 매칭용).
        today: 오늘 날짜 (lookback 기준).
        lookback_days: 과거 몇 거래일까지 볼지.
        today_strong_market: 오늘 KOSPI > 200ma 여부. None이면 layer3_strong_mkt 산출 X.
        market_regime_by_date: 과거 날짜별 KOSPI > 200ma boolean.
                               `market_regime_timeline(kospi_daily)` 결과.
                               None이면 layer3_strong_mkt 산출 X.
        today_volume_ratio: 오늘 후보의 volume / 직전 20일 평균.
                            None/NaN이면 layer3_high_vol 산출 X.
        code: 6자리 종목 코드. 지정 시 해당 종목의 historical 만 사용 (종목별 layer).
              None 이면 cross-stock pool (시장 평균 — footer reference 용).

    Returns:
        layer dict. 추가 layer 인자 부재 시 해당 슬롯은 누락 (None 키 X).

    설계 결정 (D2, v0 → v1, 사용자 정정 2026-05-21):
        v0: Layer 1~3 cross-stock pool — 표본 확보 우선.
        v1: code 인자 추가 — 종목별 layer 가 본질 (Kelly 의 p/W/L 은 그 종목 특성).
            cross-stock pool 은 footer reference (시장 평균 비교용) 로만 유지.
        사용자 의도: "Layer 들은 시장 평균이 아니라 종목별로 체크해야 한다".

        시장 국면 매칭은 KIS API 일봉 limit(252)+ma200 윈도우 제약으로 사용 가능
        날짜가 좁다 (~52일). 더 멀리 가려면 KOSPI 영구 적재 인프라 필요 (TODO).
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

    # 종목별 layer (v1, 사용자 정정 2026-05-21): code 인자 있으면 해당 종목 historical 만.
    # cross-stock pool (code=None) 은 footer reference (시장 평균 비교) 용.
    if code is not None:
        in_window = in_window[in_window["code"] == code]

    layer1 = in_window[in_window["daily_return"] >= LAYER1_RETURN_THRESHOLD]
    layer2 = in_window[in_window["daily_return"] >= LAYER2_RETURN_THRESHOLD]
    layer3 = layer2[
        (layer2["close_pos"] >= today_close_pos - CLOSE_POSITION_TOLERANCE)
        & (layer2["close_pos"] <= today_close_pos + CLOSE_POSITION_TOLERANCE)
    ]

    result: dict[str, dict[str, Any]] = {
        "layer1": _gap_metrics(layer1),
        "layer2": _gap_metrics(layer2),
        "layer3": _gap_metrics(layer3),
        "layer4": {
            **_gap_metrics(pd.DataFrame()),
            "note": "v1: 분봉 데이터 적재 후 구현",
        },
    }

    # ── 시장 국면 매칭 (layer3 위) ──────────────────────────────────────────
    if today_strong_market is not None and market_regime_by_date:
        regime_match = layer3["date"].apply(
            lambda d: market_regime_by_date.get(_coerce_date(d))
        )
        layer3_mkt = layer3[regime_match == today_strong_market]
        result["layer3_strong_mkt"] = _gap_metrics(layer3_mkt)

    # ── 거래량 비율 매칭 (layer3 위) ────────────────────────────────────────
    if today_volume_ratio is not None and today_volume_ratio == today_volume_ratio:
        in_range = layer3[
            (layer3["volume_ratio"] >= today_volume_ratio - VOLUME_RATIO_TOLERANCE)
            & (layer3["volume_ratio"] <= today_volume_ratio + VOLUME_RATIO_TOLERANCE)
        ]
        result["layer3_high_vol"] = _gap_metrics(in_range)

    logger.debug(
        f"Historical layers (today={today}, close_pos={today_close_pos:.2f}, "
        f"strong_mkt={today_strong_market}, vol_ratio={today_volume_ratio}): "
        f"L1 n={result['layer1']['n']}, L2 n={result['layer2']['n']}, "
        f"L3 n={result['layer3']['n']}"
        + (f", L3_mkt n={result['layer3_strong_mkt']['n']}" if "layer3_strong_mkt" in result else "")
        + (f", L3_vol n={result['layer3_high_vol']['n']}" if "layer3_high_vol" in result else "")
    )
    return result


def has_enough_samples(layer_stats: dict[str, Any], min_n: int = MIN_SAMPLES_FOR_CANDIDATE) -> bool:
    """Eod.Pick (c): historical 사례 >= 5건 인지 검증."""
    return layer_stats.get("n", 0) >= min_n


def pick_sizing_layer(layers: dict[str, dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    """사이징 기준 Layer 선택.

    원칙: 좁은 매칭부터 우선 — 시장 국면 매칭 > 거래량 매칭 > 기본 layer3.
    표본 부족(n < 5)이면 다음 layer 로 fallback.

    우선순위:
        layer3_strong_mkt → layer3_high_vol → layer3 → layer2 → layer1

    Returns:
        (layer_name, layer_stats)
    """
    for name in ("layer3_strong_mkt", "layer3_high_vol", "layer3", "layer2", "layer1"):
        stats = layers.get(name, {})
        if has_enough_samples(stats):
            return name, stats
    return "layer1", layers.get("layer1", {})
