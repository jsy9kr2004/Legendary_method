"""KIS API 기반 국내 지수(KOSPI/KOSDAQ) 시세 fetcher.

지수 현재가:
    TR_ID: FHPUP02100000
    Endpoint: /uapi/domestic-stock/v1/quotations/inquire-index-price
    FID_COND_MRKT_DIV_CODE: U
    FID_INPUT_ISCD: 0001 (코스피) / 1001 (코스닥)

지수 일자별 시세 (200일 이평 / 60일 수익률 계산):
    TR_ID: FHPUP02120000
    Endpoint: /uapi/domestic-stock/v1/quotations/inquire-index-daily-price
    output2: 일별 30건 (date, close)

⚠ 응답 필드명은 KIS Developer Portal 기준 추정. 운영 mock 검증 필요.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
from loguru import logger

from src.data.index_storage import (
    latest_loaded_index_date,
    read_index_daily,
    upsert_index_daily,
)
from src.kis.client import KISApiError, KISClient

_INDEX_PRICE_ENDPOINT = "/uapi/domestic-stock/v1/quotations/inquire-index-price"
_INDEX_PRICE_TR_ID = "FHPUP02100000"

_INDEX_DAILY_ENDPOINT = "/uapi/domestic-stock/v1/quotations/inquire-index-daily-price"
_INDEX_DAILY_TR_ID = "FHPUP02120000"

KOSPI_CODE = "0001"
KOSDAQ_CODE = "1001"


def _to_float(val: Any, default: float = float("nan")) -> float:
    try:
        return float(str(val).strip())
    except (TypeError, ValueError):
        return default


def fetch_index_quote(client: KISClient, index_code: str = KOSPI_CODE) -> dict[str, Any] | None:
    """지수 현재가 조회.

    Args:
        client: KIS client.
        index_code: "0001"(KOSPI) | "1001"(KOSDAQ).

    Returns:
        {
            "code": "0001",
            "current": float,    # 현재 지수
            "prev_close": float, # 전일 종가
            "change": float,     # 전일 대비
            "change_rate": float,# 등락률 (%)
        }
        실패 시 None.

    참고 응답 필드 (output):
        bstp_nmix_prpr      현재 지수
        prdy_nmix           전일 지수
        bstp_nmix_prdy_vrss 전일 대비
        prdy_ctrt           등락률
    """
    params = {
        "FID_COND_MRKT_DIV_CODE": "U",
        "FID_INPUT_ISCD": index_code,
    }
    try:
        payload = client.get(_INDEX_PRICE_ENDPOINT, _INDEX_PRICE_TR_ID, params=params)
    except KISApiError as e:
        logger.error(f"지수({index_code}) 현재가 조회 실패: {e}")
        return None
    except httpx.HTTPError as e:
        logger.warning(f"지수({index_code}) 현재가 조회 HTTP 실패: {type(e).__name__}: {e}")
        return None

    out = payload.get("output") or {}
    if isinstance(out, list):
        out = out[0] if out else {}
    if not out:
        return None

    return {
        "code": index_code,
        "current": _to_float(out.get("bstp_nmix_prpr")),
        "prev_close": _to_float(out.get("prdy_nmix")),
        "change": _to_float(out.get("bstp_nmix_prdy_vrss")),
        "change_rate": _to_float(out.get("prdy_ctrt")),
    }


def fetch_index_daily(
    client: KISClient,
    index_code: str = KOSPI_CODE,
    days: int = 252,
) -> pd.DataFrame:
    """지수 일자별 시세 (close 만 사용).

    Args:
        client: KIS client.
        index_code: "0001"(KOSPI) | "1001"(KOSDAQ).
        days: 최근 며칠 (200일 이평 계산엔 252 정도 권장).

    Returns:
        columns=[date, close], 오름차순 (가장 최근이 마지막).
        실패 또는 빈 응답 시 빈 DataFrame.

    참고 응답 필드 (output2):
        stck_bsop_date  영업일자 YYYYMMDD
        bstp_nmix_prpr  종가 지수
    """
    params = {
        "FID_COND_MRKT_DIV_CODE": "U",
        "FID_INPUT_ISCD": index_code,
        "FID_INPUT_DATE_1": "",
        "FID_INPUT_DATE_2": "",
        "FID_PERIOD_DIV_CODE": "D",  # D=일봉
    }
    try:
        payload = client.get(_INDEX_DAILY_ENDPOINT, _INDEX_DAILY_TR_ID, params=params)
    except KISApiError as e:
        logger.error(f"지수({index_code}) 일별 조회 실패: {e}")
        return pd.DataFrame(columns=["date", "close"])
    except httpx.HTTPError as e:
        logger.warning(f"지수({index_code}) 일별 조회 HTTP 실패: {type(e).__name__}: {e}")
        return pd.DataFrame(columns=["date", "close"])

    rows = payload.get("output2") or payload.get("output") or []
    if isinstance(rows, dict):
        rows = [rows]
    if not rows:
        return pd.DataFrame(columns=["date", "close"])

    records = []
    for r in rows[:days]:
        date = str(r.get("stck_bsop_date", "")).strip()
        close = _to_float(r.get("bstp_nmix_prpr"))
        if not date or close != close:  # NaN
            continue
        records.append({"date": date, "close": close})

    if not records:
        return pd.DataFrame(columns=["date", "close"])

    df = pd.DataFrame(records).sort_values("date").reset_index(drop=True)
    return df


def _parse_kis_date(s: Any) -> date | None:
    """KIS YYYYMMDD 문자열 → python date. 실패 시 None."""
    if isinstance(s, date) and not isinstance(s, str):
        return s
    s = str(s).strip()
    if len(s) == 8 and s.isdigit():
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    return None


def fetch_index_daily_range(
    client: KISClient,
    index_code: str,
    start_date: date,
    end_date: date,
    max_pages: int = 50,
) -> pd.DataFrame:
    """[start_date, end_date] 구간 지수 일봉 페이지네이션 fetch.

    KIS inquire-index-daily-price 가 한 호출당 최대 ~100건만 응답하므로,
    end_date 를 한 페이지씩 앞으로 당기며 누적 수집.

    Args:
        client: KIS client.
        index_code: "0001" KOSPI / "1001" KOSDAQ.
        start_date: 가장 오래된 날짜 (포함).
        end_date: 가장 최근 날짜 (포함).
        max_pages: 안전 가드 (무한 루프 방지).

    Returns:
        columns=[date(python date), close(float)], 오름차순. 빈 응답이면 빈 DF.
    """
    if start_date > end_date:
        return pd.DataFrame(columns=["date", "close"])

    all_records: list[dict[str, Any]] = []
    cursor_end = end_date
    pages = 0
    while cursor_end >= start_date and pages < max_pages:
        params = {
            "FID_COND_MRKT_DIV_CODE": "U",
            "FID_INPUT_ISCD": index_code,
            "FID_INPUT_DATE_1": start_date.strftime("%Y%m%d"),
            "FID_INPUT_DATE_2": cursor_end.strftime("%Y%m%d"),
            "FID_PERIOD_DIV_CODE": "D",
        }
        try:
            payload = client.get(_INDEX_DAILY_ENDPOINT, _INDEX_DAILY_TR_ID, params=params)
        except KISApiError as e:
            logger.error(f"지수({index_code}) 페이지 fetch 실패 (~{cursor_end}): {e}")
            break
        except httpx.HTTPError as e:
            logger.warning(
                f"지수({index_code}) 페이지 fetch HTTP 실패 (~{cursor_end}): "
                f"{type(e).__name__}: {e}"
            )
            break

        rows = payload.get("output2") or payload.get("output") or []
        if isinstance(rows, dict):
            rows = [rows]
        if not rows:
            break

        page_records: list[dict[str, Any]] = []
        for r in rows:
            d_obj = _parse_kis_date(r.get("stck_bsop_date"))
            close = _to_float(r.get("bstp_nmix_prpr"))
            if d_obj is None or close != close:
                continue
            if start_date <= d_obj <= cursor_end:
                page_records.append({"date": d_obj, "close": close})

        if not page_records:
            break

        all_records.extend(page_records)
        oldest = min(r["date"] for r in page_records)
        next_end = oldest - timedelta(days=1)
        if next_end >= cursor_end:  # 진행이 없음 — 무한 루프 방지
            break
        cursor_end = next_end
        pages += 1

    if not all_records:
        return pd.DataFrame(columns=["date", "close"])

    df = (
        pd.DataFrame(all_records)
        .drop_duplicates(subset=["date"])
        .sort_values("date")
        .reset_index(drop=True)
    )
    return df


def init_index_daily(
    client: KISClient,
    data_dir: Path,
    years: int = 3,
    index_codes: tuple[str, ...] = (KOSPI_CODE, KOSDAQ_CODE),
    today: date | None = None,
) -> dict[str, int]:
    """지수 일봉 초기 백필.

    Args:
        client: KIS client.
        data_dir: 저장 위치 ({DATA_DIR}/index/...).
        years: 몇 년치 백필할지 (기본 3년 — historical layer3_strong_mkt 200ma + 1년 lookback 커버).
        index_codes: 백필할 지수 코드들.
        today: 기준일 (기본 date.today()).

    Returns:
        {index_code: 적재된 행 수}
    """
    today = today or date.today()
    start_date = today - timedelta(days=int(years * 365.25))

    result: dict[str, int] = {}
    for code in index_codes:
        df = fetch_index_daily_range(client, code, start_date, today)
        if df.empty:
            logger.warning(f"[init_index] {code}: 응답 없음")
            result[code] = 0
            continue
        n = upsert_index_daily(df, data_dir, code)
        result[code] = n
        logger.info(f"[init_index] {code}: {len(df)}건 fetch → 누적 {n}건")
    return result


def update_index_daily(
    client: KISClient,
    data_dir: Path,
    index_codes: tuple[str, ...] = (KOSPI_CODE, KOSDAQ_CODE),
    today: date | None = None,
) -> dict[str, int]:
    """지수 일봉 incremental update — 마지막 적재일 다음날부터 오늘까지.

    Returns:
        {index_code: 신규 추가 행 수}
    """
    today = today or date.today()
    result: dict[str, int] = {}
    for code in index_codes:
        last = latest_loaded_index_date(data_dir, code)
        if last is None:
            logger.info(f"[update_index] {code}: 적재 이력 없음 — init 먼저 실행 필요")
            result[code] = 0
            continue
        if last >= today:
            logger.debug(f"[update_index] {code}: 이미 {last} 까지 적재됨, skip")
            result[code] = 0
            continue
        start = last + timedelta(days=1)
        df = fetch_index_daily_range(client, code, start, today)
        if df.empty:
            result[code] = 0
            continue
        before = len(read_index_daily(data_dir, code))
        after = upsert_index_daily(df, data_dir, code)
        added = after - before
        result[code] = added
        logger.info(f"[update_index] {code}: {start}~{today} → {added}건 추가")
    return result


def compute_market_stats(
    client: KISClient,
    daily_lookback_days: int = 252,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    """모닝/사후/결정 레포트용 시장 국면 통계 일괄 산출.

    KOSPI 일별 시계열은 우선순위:
        (1) data_dir 가 주어지고 적재된 KOSPI 가 있으면 적재본 사용 (영구 누적).
        (2) 없으면 client 로 한 번 fetch (~252건 한정).

    Returns:
        {
            "kospi_current", "kospi_change_rate",
            "kospi_ma200", "kospi_above_ma200",
            "kospi_60d_return",
            "kosdaq_current", "kosdaq_change_rate",
        }
        조회 실패한 항목은 NaN/None.
    """
    stats: dict[str, Any] = {}

    # KOSPI 현재
    kospi_quote = fetch_index_quote(client, KOSPI_CODE)
    if kospi_quote:
        stats["kospi_current"] = kospi_quote["current"]
        stats["kospi_change_rate"] = kospi_quote["change_rate"]

    # KOSDAQ 현재
    kosdaq_quote = fetch_index_quote(client, KOSDAQ_CODE)
    if kosdaq_quote:
        stats["kosdaq_current"] = kosdaq_quote["current"]
        stats["kosdaq_change_rate"] = kosdaq_quote["change_rate"]

    # KOSPI 일자별 — 적재본 우선
    kospi_daily = pd.DataFrame()
    if data_dir is not None:
        kospi_daily = read_index_daily(Path(data_dir), KOSPI_CODE)
    if kospi_daily.empty:
        kospi_daily = fetch_index_daily(client, KOSPI_CODE, days=daily_lookback_days)

    if not kospi_daily.empty and len(kospi_daily) >= 60:
        last_close = float(kospi_daily.iloc[-1]["close"])
        if len(kospi_daily) >= 200:
            ma200 = float(kospi_daily.tail(200)["close"].mean())
            stats["kospi_ma200"] = ma200
            stats["kospi_above_ma200"] = last_close > ma200
        # 60일 수익률 = (오늘 / 60거래일 전 - 1) × 100
        ref_close = float(kospi_daily.iloc[-60]["close"])
        if ref_close > 0:
            stats["kospi_60d_return"] = (last_close / ref_close - 1.0) * 100.0

    return stats
