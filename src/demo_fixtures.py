"""Demo용 mock 데이터 생성기.

실제 KIS API / 일봉 데이터 없이도 파이프라인 전체를 실행해볼 수 있도록
2025-05-04 제룡전기 상한가 사례를 기반으로 현실적인 fixture 데이터를 생성한다.

검증 발화 근거 (CLAUDE.md):
    "5/4 주도주: 하이닉스, SK스퀘어, 삼성증권, 제룡전기"
    "제룡전기 91,300원에 상한가 칠 때 매수 → 갭상 유력"
    "전기/전선 섹터가 거래대금 상위 다수"
"""
from __future__ import annotations

import random
from datetime import date, timedelta

import numpy as np
import pandas as pd

# 재현성을 위한 시드 고정
_RNG = np.random.default_rng(42)

# ── 종목 정의 ─────────────────────────────────────────────────────────────────

DEMO_STOCKS = [
    # (code, name, market, base_price, theme)
    ("075180", "제룡전기",       "KOSDAQ", 70_230,  ["전기/전선", "원자력", "AI데이터센터"]),
    ("001440", "대한전선",       "KOSPI",  3_500,   ["전기/전선", "구리"]),
    ("229640", "LS에코에너지",   "KOSPI",  12_000,  ["전기/전선", "구리"]),
    ("010120", "LS ELECTRIC",    "KOSPI",  110_000, ["전기/전선", "원전"]),
    ("267260", "HD현대일렉트릭", "KOSPI",  290_000, ["전기/전선", "원전"]),
    ("000660", "SK하이닉스",     "KOSPI",  180_000, ["반도체", "AI칩"]),
    ("034730", "SK스퀘어",       "KOSPI",  68_000,  ["반도체", "지주회사"]),
    ("016360", "삼성증권",       "KOSPI",  44_000,  ["증권", "금융"]),
    ("005930", "삼성전자",       "KOSPI",  79_000,  ["반도체", "AI칩"]),
    ("035720", "카카오",         "KOSPI",  41_000,  ["IT서비스", "AI"]),
]

DEMO_THEMES = [
    ("075180", "전기/전선"), ("075180", "원자력"), ("075180", "AI데이터센터"),
    ("001440", "전기/전선"), ("001440", "구리"),
    ("229640", "전기/전선"), ("229640", "구리"),
    ("010120", "전기/전선"), ("010120", "원전"),
    ("267260", "전기/전선"), ("267260", "원전"),
    ("000660", "반도체"),    ("000660", "AI칩"),
    ("034730", "반도체"),    ("034730", "지주회사"),
    ("016360", "증권"),      ("016360", "금융"),
    ("005930", "반도체"),    ("005930", "AI칩"),
    ("035720", "IT서비스"),  ("035720", "AI"),
]


# ── 일봉 생성 ─────────────────────────────────────────────────────────────────

def _trading_days(end: date, n: int = 260) -> list[date]:
    """end 이전 n 영업일 목록 (주말 제외)."""
    days = []
    d = end - timedelta(days=1)
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return list(reversed(days))


def _make_ohlcv_series(
    base_price: int,
    n_days: int,
    special_day_idx: int | None,
    special_return: float = 0.30,
) -> pd.DataFrame:
    """단순 랜덤워크 일봉 시리즈 생성.

    Args:
        base_price: 기준 가격
        n_days: 거래일 수
        special_day_idx: 이날은 special_return으로 급등 (상한가 사례 삽입)
        special_return: 급등일 수익률
    """
    closes = [float(base_price)]
    for i in range(1, n_days):
        r = _RNG.normal(0.0005, 0.015)  # 일반일
        if special_day_idx and abs(i - special_day_idx) < 3:
            # 특별일 전후로 약간 관련 움직임
            r = _RNG.normal(0.005, 0.01)
        closes.append(max(closes[-1] * (1 + r), 1000))

    # 특별일에 급등 삽입
    if special_day_idx is not None and 0 < special_day_idx < n_days:
        prev = closes[special_day_idx - 1]
        closes[special_day_idx] = prev * (1 + special_return)

    records = []
    for i, close in enumerate(closes):
        prev = closes[i - 1] if i > 0 else close
        intraday_range = abs(_RNG.normal(0.02, 0.01))
        high = close * (1 + intraday_range / 2) if close >= prev else max(close, prev) * (1 + intraday_range / 3)
        low = close * (1 - intraday_range / 2)
        open_p = prev * (1 + _RNG.normal(0, 0.005))
        records.append({
            "open":  max(int(open_p), 1),
            "high":  max(int(high), int(close)),
            "low":   min(int(low), int(close)),
            "close": int(close),
            "volume": int(_RNG.integers(100_000, 10_000_000)),
            "trading_value": int(close * _RNG.integers(100_000, 10_000_000)),
            "change_rate": pd.NA,
        })
    return pd.DataFrame(records)


def make_daily_ohlcv(target_date: date, lookback_days: int = 260) -> pd.DataFrame:
    """전종목 일봉 long format DataFrame 생성.

    target_date를 포함한 lookback_days 거래일치.
    제룡전기는 target_date에 상한가(+30%) 삽입.
    다른 전기/전선 종목들도 target_date에 강세 삽입.
    """
    days = _trading_days(target_date + timedelta(days=1), lookback_days + 1)
    # target_date 포함하도록 조정
    if target_date not in days:
        days.append(target_date)
        days.sort()
    days = days[-lookback_days:]

    # 과거에도 충분한 강한 양봉 사례 삽입 (R4(c) n>=5 충족 + Layer 통계 의미있게)
    # Layer 1 cross-stock pool 이라 10종목 × 6건 = 60건 정도면 충분.
    historical_limit_up_days = [25, 60, 100, 140, 180, 220]

    all_rows = []
    for code, name, market, base_price, _ in DEMO_STOCKS:
        today_idx = len(days) - 1  # 마지막 날 = target_date

        if code == "075180":  # 제룡전기: 오늘 상한가 + 과거 다수 사례
            special_return = 0.30
            df_vals = _make_ohlcv_series(base_price, len(days), today_idx, special_return)
            for hist_idx in historical_limit_up_days:
                if hist_idx < len(days):
                    prev_close = int(df_vals.iloc[hist_idx - 1]["close"])
                    df_vals.at[hist_idx, "close"] = int(prev_close * 1.30)
                    df_vals.at[hist_idx, "high"] = int(prev_close * 1.30)
                    # 다음날 갭 삽입 (대부분 갭상)
                    if hist_idx + 1 < len(days) and hist_idx % 3 != 0:
                        df_vals.at[hist_idx + 1, "open"] = int(prev_close * 1.30 * 1.07)
                    elif hist_idx + 1 < len(days):
                        df_vals.at[hist_idx + 1, "open"] = int(prev_close * 1.30 * 0.99)
        elif code in ("001440", "229640", "010120", "267260"):  # 전기/전선 강세
            df_vals = _make_ohlcv_series(base_price, len(days), today_idx, 0.22)
            # 다른 종목에도 +20%↑ 과거 사례 일부 삽입 (Layer 1 표본 보강)
            for hist_idx in historical_limit_up_days[:3]:
                if hist_idx < len(days):
                    prev_close = int(df_vals.iloc[hist_idx - 1]["close"])
                    df_vals.at[hist_idx, "close"] = int(prev_close * 1.22)
                    df_vals.at[hist_idx, "high"] = int(prev_close * 1.25)
                    if hist_idx + 1 < len(days):
                        df_vals.at[hist_idx + 1, "open"] = int(prev_close * 1.22 * 1.03)
        else:
            df_vals = _make_ohlcv_series(base_price, len(days), None)
            # 일반 종목도 가끔 강한 양봉 — Layer 1 표본 보강용
            for hist_idx in historical_limit_up_days[:2]:
                if hist_idx < len(days):
                    prev_close = int(df_vals.iloc[hist_idx - 1]["close"])
                    df_vals.at[hist_idx, "close"] = int(prev_close * 1.21)
                    df_vals.at[hist_idx, "high"] = int(prev_close * 1.23)

        for i, d in enumerate(days):
            row = df_vals.iloc[i]
            all_rows.append({
                "code": code,
                "date": d,
                "open":  row["open"],
                "high":  row["high"],
                "low":   row["low"],
                "close": row["close"],
                "volume": row["volume"],
                "trading_value": row["trading_value"],
                "change_rate": pd.NA,
            })

    return pd.DataFrame(all_rows)


def make_snapshot(target_date: date) -> pd.DataFrame:
    """14:50 스냅샷 mock 생성.

    제룡전기 상한가 + 전기/전선 테마 3~4종목 강세.
    """
    daily = make_daily_ohlcv(target_date, lookback_days=10)
    today_data = daily[daily["date"] == target_date].set_index("code")
    # 실제 전일 종가를 사용해야 is_limit_up 계산이 일관성 있음
    prev_close_map = (
        daily[daily["date"] < target_date]
        .groupby("code")["close"]
        .last()
        .to_dict()
    )

    rows = []
    rank_order = ["075180", "000660", "001440", "229640", "010120",
                  "034730", "016360", "267260", "005930", "035720"]

    for rank, code in enumerate(rank_order, 1):
        if code not in today_data.index:
            continue
        row = today_data.loc[code]
        stock_info = next((s for s in DEMO_STOCKS if s[0] == code), None)
        if not stock_info:
            continue

        _, name, _, base_price, _ = stock_info
        close = int(row["close"])
        prev_close = int(prev_close_map.get(code, base_price))
        daily_return = (close - prev_close) / prev_close * 100.0
        is_lup = close >= int(prev_close * 1.30)

        rows.append({
            "rank": rank,
            "code": code,
            "name": name,
            "price": close,
            "prev_close": prev_close,
            "daily_return": round(daily_return, 2),
            "intraday_high": int(row["high"]),
            "intraday_low": int(row["low"]),
            "volume": int(row["volume"]),
            "trading_value": int(row["trading_value"]) * rank,  # 1위가 더 크도록
            "is_limit_up": is_lup,
        })

    return pd.DataFrame(rows)


def make_theme_mapping(crawled_at: date | None = None) -> pd.DataFrame:
    """네이버 테마 매핑 mock."""
    if crawled_at is None:
        crawled_at = date.today()
    return pd.DataFrame([
        {"code": code, "theme": theme, "crawled_at": crawled_at}
        for code, theme in DEMO_THEMES
    ])
