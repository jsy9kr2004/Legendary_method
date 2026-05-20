"""KIS Open API 기반 장중 데이터 fetcher.

거래대금 순위 (★ 거래량 아님 — 함정 주의):
    TR_ID: FHPST01710000
    Endpoint: /uapi/domestic-stock/v1/quotations/volume-rank
    - 한 번 호출로 KIS 가 반환하는 상한 30 종목 (별도 페이지네이션 없음 — 2026-05-19
      round 41 후속 2 진단). `top_n` 인자는 우리 쪽 추가 컷일 뿐 endpoint 자체 확장 X.
      50 으로 늘리려면 별도 endpoint 또는 자체 정렬 필요 (TODO).

    ⚠ `FID_BLNG_CLS_CODE` 가 정렬축을 결정 — 절대 임의로 바꾸지 말 것:
        - "0" : 평균거래량 ← 이전 버그 (2026-05-19 발견). 삼성전자가 14위로 밀리고
                KODEX 200선물인버스2X 가 1위로 오는 이유. 거래량(주) 순.
        - "1" : 거래증가율
        - "2" : 평균거래회전율
        - "3" : 거래금액순 ← **종배/주도섹터 universe 는 반드시 이 값** (거래대금/원).
        - "4" : 평균거래금액회전율
    `_VOLUME_RANK_BLNG_CLS_TRADING_VALUE` 상수로 박아둠 — 테스트도 같이 검증.

종목 현재가:
    TR_ID: FHKST01010100
    Endpoint: /uapi/domestic-stock/v1/quotations/inquire-price
    - 종목별 1회 호출

정량 정의:
    - daily_return(%): (현재가 - 전일종가) / 전일종가 * 100
    - limit_up_price: 전일종가 * 1.30 (KOSPI/KOSDAQ 공통 +30%)
    - intraday_high: 당일 장중 고가 (stck_hgpr)
    - 거래대금(trading_value): acml_tr_pbmn (원) — 누적 체결금액
    - 거래량(volume): acml_vol (주) — 누적 체결주식수
      ★ 단타 universe 는 거래대금 기준. 거래량은 저가주 노이즈 큼 (KODEX 인버스류 1위 점령).
"""
from __future__ import annotations

from typing import Any

import httpx
import pandas as pd
from loguru import logger

from src.kis.client import KISApiError, KISClient

_VOLUME_RANK_ENDPOINT = "/uapi/domestic-stock/v1/quotations/volume-rank"
_VOLUME_RANK_TR_ID = "FHPST01710000"

# FID_BLNG_CLS_CODE — KIS volume-rank 정렬축. 종배/주도섹터는 거래대금 기준 "3" 만
# 허용. "0"(평균거래량) 으로 잘못 쓰면 KODEX 인버스류가 1위로 잡혀 universe 자체가
# 무너진다 (2026-05-19 발견 버그). docstring 표 + 회귀 테스트로 박아둠.
_VOLUME_RANK_BLNG_CLS_TRADING_VALUE = "3"

# KIS volume-rank 는 한 호출당 30개 hard cap. 페이지네이션(ctx_area_fk100/nk100)
# 미지원 + FID_COND_MRKT_DIV_CODE 분리 호출도 "J" 외 INVALID 응답 (2026-05-19
# round 41 후속 2 후속 진단). 50위 까지 가져오려면 가격 범위 분할 호출 후
# 합집합 → trading_value desc 정렬 우회 필요.
_KIS_PAGE_SIZE = 30

# 가격 버킷 — 진단 (`scripts/diag_volume_rank.py --plan-b`) 으로 검증됨:
#   저가(0~10,000원): KODEX 인버스류 + 저가 단타주
#   중가(10,001~100,000원): 코스모로보틱스 / 일반 단타주 / 일부 ETF
#   고가(100,001원~): 삼성전자 / SK하이닉스 / 대형주
# 3개 버킷 합집합 90 종목 → 거래대금 desc top 50 = 1위 삼성전자(8.4조) ~
# 50위 대우건설(2,195억) 완벽 cover.
_PRICE_BUCKETS: list[tuple[int, int]] = [
    (0, 10_000),
    (10_001, 100_000),
    (100_001, 9_999_999),
]

_INQUIRE_PRICE_ENDPOINT = "/uapi/domestic-stock/v1/quotations/inquire-price"
_INQUIRE_PRICE_TR_ID = "FHKST01010100"

SNAPSHOT_COLUMNS = [
    "rank",          # KIS data_rank — **거래대금**(원, acml_tr_pbmn) 내림차순 절대 순위.
                     # ETF/펀드 포함 전체 시장 기준. master 필터로 제외된 종목은
                     # 응답에서 빠지지만 보통주의 rank 는 KIS 원본 값 그대로 유지 →
                     # HTS 거래대금 순위와 1:1 비교 가능 (2026-05-18 정정).
                     # ⚠ "거래량" 아님. 2026-05-19 까지 FID_BLNG_CLS_CODE="0" 버그로
                     # 사실상 평균거래량 순이었음 → fix 후 거래대금 순.
    "turnover_rank", # master 필터 통과 종목들의 turnover 내림차순 순위 (1~top_n).
                     # "거래대금 N위 안에서의 회전율 순위" — 절대 시장 순위 아님.
                     # KIS API 가 회전율 순위는 별도 제공 X.
                     # turnover = trading_value / market_cap (시총 정규화).
    "volume_rank",   # master 필터 통과 종목들의 **volume**(주) 내림차순 순위 (1~top_n).
                     # ⚠ rank(거래대금) 와 명확히 다른 축. snapshot universe 안의
                     # 상대 순위로만 의미 — KIS 가 절대 거래량 순위는 endpoint 분리.
    "code",
    "name",
    "price",
    "prev_close",
    "daily_return",
    "intraday_high",
    "intraday_low",
    "volume",
    "trading_value",
    "is_limit_up",
    "market_cap",   # 마스터 조인 결과. 0 이면 미상.
    "turnover",     # trading_value / market_cap. 0 이면 NaN
]


def _to_int(val: Any, default: int = 0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _to_float(val: Any, default: float = float("nan")) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _build_volume_rank_params(price_lo: str = "", price_hi: str = "") -> dict[str, str]:
    """KIS volume-rank 호출용 params. 가격 범위 빈 문자열 = 전체."""
    return {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_COND_SCR_DIV_CODE": "20171",
        "FID_INPUT_ISCD": "0000",
        "FID_DIV_CLS_CODE": "0",
        # ⚠ "0" 으로 두면 평균거래량 정렬 — 종배 universe 가 ETF/저가주로 오염됨.
        # 반드시 "3" (= 거래금액순 / 거래대금). 모듈 docstring 표 참조.
        "FID_BLNG_CLS_CODE": _VOLUME_RANK_BLNG_CLS_TRADING_VALUE,
        "FID_TRGT_CLS_CODE": "111111111",
        "FID_TRGT_EXLS_CLS_CODE": "000000",
        "FID_INPUT_PRICE_1": price_lo,
        "FID_INPUT_PRICE_2": price_hi,
        "FID_VOL_CNT": "",
        "FID_INPUT_DATE_1": "",
    }


def _fetch_volume_rank_page(
    client: KISClient,
    price_lo: str = "",
    price_hi: str = "",
) -> list[dict]:
    """단일 KIS volume-rank 호출 — 최대 30개 raw row 반환.

    KIS endpoint 가 한 호출당 30개 hard cap. 가격 분할 호출에서 페이지 단위로 사용.

    Args:
        price_lo, price_hi: FID_INPUT_PRICE_1/_2. 빈 문자열이면 전체.

    Returns:
        raw row list (dict). API 실패 시 빈 list — 호출부가 다른 버킷 결과로 보강 가능.
    """
    params = _build_volume_rank_params(price_lo, price_hi)
    label = f"가격 {price_lo}~{price_hi}" if price_lo or price_hi else "전체"
    try:
        payload = client.get(_VOLUME_RANK_ENDPOINT, _VOLUME_RANK_TR_ID, params=params)
    except KISApiError as e:
        logger.error(f"거래대금 순위 조회 실패 ({label}): {e}")
        return []
    except httpx.HTTPError as e:
        # KIS 5xx / 네트워크 단절 — tenacity 재시도 후에도 실패한 케이스.
        logger.warning(f"거래대금 순위 조회 HTTP 실패 ({label}): {type(e).__name__}: {e}")
        return []
    return payload.get("output") or []


def _parse_volume_rank_row(
    row: dict,
    master_lookup: dict[str, int],
    tradable_codes: set[str] | None,
) -> dict | None:
    """raw KIS row → snapshot record dict. master 필터 미통과 시 None.

    `rank` 는 row 의 `data_rank` 그대로 — 단일 호출 모드에서는 KIS 가 매긴
    절대 거래대금 순위. 가격 버킷 모드에서는 버킷 내부 순위라 호출부가 글로벌
    rank 로 덮어써야 한다.
    """
    code = str(row.get("mksc_shrn_iscd", "")).zfill(6)
    if tradable_codes is not None and code not in tradable_codes:
        return None
    price = _to_int(row.get("stck_prpr"))
    prev_close = _to_int(row.get("stck_prdy_clpr"))
    daily_return = _to_float(row.get("prdy_ctrt"))
    intraday_high = _to_int(row.get("stck_hgpr"))
    intraday_low = _to_int(row.get("stck_lwpr"))
    volume = _to_int(row.get("acml_vol"))
    trading_value = _to_int(row.get("acml_tr_pbmn"))
    lup = _is_limit_up_price(price, prev_close) if prev_close > 0 else False
    market_cap = master_lookup.get(code, 0)
    # KIS 는 거래대금회전율(`tr_pbmn_tnrt`, %)을 자체 계산해서 응답에 넣어준다.
    # master_df.market_cap=0(미적재) 케이스에도 회전율이 정상으로 나오게 KIS 값을
    # 우선 사용. 비어있을 때만 자체 계산으로 fallback.
    kis_turnover = _to_float(row.get("tr_pbmn_tnrt"))
    if pd.notna(kis_turnover) and kis_turnover > 0:
        turnover = kis_turnover
    else:
        turnover = compute_turnover(trading_value, market_cap)
    return {
        "rank": _to_int(row.get("data_rank"), 0),
        "turnover_rank": None,
        "volume_rank": None,
        "code": code,
        "name": str(row.get("hts_kor_isnm", "")),
        "price": price,
        "prev_close": prev_close,
        "daily_return": daily_return,
        "intraday_high": intraday_high,
        "intraday_low": intraday_low,
        "volume": volume,
        "trading_value": trading_value,
        "is_limit_up": lup,
        "market_cap": market_cap,
        "turnover": turnover,
    }


def fetch_volume_rank(
    client: KISClient,
    top_n: int = 30,
    master_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """거래대금 상위 top_n 종목 스냅샷.

    정량 정의:
        - FID_COND_MRKT_DIV_CODE=J: KOSPI+KOSDAQ 통합
        - FID_COND_SCR_DIV_CODE=20171: 거래대금 순위 스크리너
        - FID_BLNG_CLS_CODE=3: 거래금액순 (모듈 docstring 의 FID 표 참조)

    호출 모드 (2026-05-19 round 41 후속 2 후속):
        - **top_n ≤ 30**: 단일 호출. KIS data_rank 그대로 (HTS 거래대금 순위 1:1).
        - **top_n > 30**: 가격 버킷 3회 호출 (`_PRICE_BUCKETS`) → 합집합 →
          trading_value desc 재정렬 → top_n 컷 → rank 글로벌 재부여 (1..top_n).
          KIS endpoint 가 한 호출당 30개 hard cap + 페이지네이션 미지원이라
          가격 범위 분할로 우회. 진단 결과: 3회 호출 → 90 고유 종목 → 거래대금
          50위 완벽 cover (`scripts/diag_volume_rank.py --plan-b`).

    Args:
        client: KIS API client.
        top_n: 거래대금 상위 몇 위까지 가져올지 (30 초과 시 자동 분할 호출).
        master_df: 종목 마스터 (M5.5). `code, market_cap` 포함. 주어지면
            (1) 종배 후보 자격 종목만 필터링, (2) market_cap/turnover 부착.
            None 이면 v0 동작 — 모든 종목 통과, market_cap=0.

    Returns:
        SNAPSHOT_COLUMNS 스키마의 DataFrame (rank 오름차순).
        API 실패 시 빈 DataFrame. 가격 버킷 모드에서 일부 버킷만 실패하면 살아남은
        버킷으로 부분 응답 (호출부가 len 으로 정상/부분/실패 구분 가능).
    """
    # master 조인 준비
    master_lookup: dict[str, int] = {}
    tradable_codes: set[str] | None = None
    if master_df is not None and not master_df.empty:
        master_lookup = dict(zip(
            master_df["code"].astype(str),
            master_df["market_cap"].fillna(0).astype(int),
        ))
        tradable_codes = set(master_df["code"].astype(str).tolist())

    if top_n <= _KIS_PAGE_SIZE:
        # 단일 호출 모드 — KIS data_rank 그대로 (HTS 비교 가능)
        raw_rows = _fetch_volume_rank_page(client)
        records: list[dict] = []
        for row in raw_rows:
            rank = _to_int(row.get("data_rank"), 0)
            if rank < 1 or rank > top_n:
                continue
            parsed = _parse_volume_rank_row(row, master_lookup, tradable_codes)
            if parsed is not None:
                records.append(parsed)
    else:
        # 가격 버킷 모드 — 3회 호출 합집합. 같은 종목이 두 버킷에 등장 (가격
        # 경계 + intraday 가격 변동) 하면 trading_value 큰 쪽 채택.
        union: dict[str, dict] = {}
        for lo, hi in _PRICE_BUCKETS:
            raw_rows = _fetch_volume_rank_page(client, str(lo), str(hi))
            for row in raw_rows:
                parsed = _parse_volume_rank_row(row, master_lookup, tradable_codes)
                if parsed is None:
                    continue
                code = parsed["code"]
                prev = union.get(code)
                if prev is None or parsed["trading_value"] > prev["trading_value"]:
                    union[code] = parsed
        # 거래대금 desc 정렬 + top_n 컷 + rank 글로벌 재부여
        sorted_records = sorted(
            union.values(),
            key=lambda r: r["trading_value"],
            reverse=True,
        )
        records = sorted_records[:top_n]
        for i, r in enumerate(records, 1):
            # 버킷 내부 data_rank 는 의미 없음 — union 정렬 순위로 덮어씀.
            # 사용자가 HTS 거래대금 순위와 비교할 때 본 rank 는 추정치 (정확한
            # 시장 전체 순위는 KIS 가 1회 30개만 주므로 v0 한계).
            r["rank"] = i

    if not records:
        return pd.DataFrame(columns=SNAPSHOT_COLUMNS)
    df = pd.DataFrame(records, columns=SNAPSHOT_COLUMNS).sort_values("rank").reset_index(drop=True)
    # 회전율 순위 부여 — universe 내 turnover desc. NaN 은 끝으로.
    df["turnover_rank"] = (
        df["turnover"]
        .rank(method="min", ascending=False, na_option="bottom")
        .astype("Int64")
    )
    # 거래량 순위 — universe 내 volume desc (2026-05-19 round 41 후속).
    df["volume_rank"] = (
        df["volume"]
        .rank(method="min", ascending=False, na_option="bottom")
        .astype("Int64")
    )
    mode = "price_bucket" if top_n > _KIS_PAGE_SIZE else "single"
    logger.debug(
        f"거래대금 순위 조회 완료: {len(df)}종목 "
        f"(top_n={top_n}, mode={mode}, master 적용={master_df is not None})"
    )
    return df


def compute_turnover(trading_value: int, market_cap: int) -> float:
    """회전율 = 거래대금 / 시가총액.

    KIS mst 시가총액 단위는 보통 **억원**. 거래대금 acml_tr_pbmn 은 **원** 단위.
    따라서 단위 맞추기:
        turnover_pct(%) = (trading_value 원) / (market_cap 억원 × 1e8) × 100

    market_cap 0 이면 NaN (시총 데이터 없음을 의미, 호출부에서 fallback).

    Returns:
        회전율(%). 정상 종목은 0.x ~ 30 사이.
    """
    if market_cap <= 0 or trading_value <= 0:
        return float("nan")
    return (trading_value / (market_cap * 1e8)) * 100.0


def fetch_quote(client: KISClient, code: str) -> dict[str, Any] | None:
    """단일 종목 현재가 조회.

    Returns:
        {code, name, price, prev_close, daily_return, intraday_high,
         volume, trading_value, is_limit_up}
        조회 실패 시 None.
    """
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": code,
    }
    try:
        payload = client.get(_INQUIRE_PRICE_ENDPOINT, _INQUIRE_PRICE_TR_ID, params=params)
    except KISApiError as e:
        logger.error(f"{code} 현재가 조회 실패: {e}")
        return None
    except httpx.HTTPError as e:
        # KIS 서버 5xx (특정 종목 일시적 오류) / 네트워크 단절. tenacity 3회 재시도 후 실패.
        # 종목 단위 실패로 격리 — 폴링 사이클 전체를 죽이지 않는다. 사이클이 죽으면
        # `@_business_day_only` 가 텔레그램 "시스템 장애" 알림을 발사해 푸시 폭주가 된다.
        logger.warning(f"{code} 현재가 조회 HTTP 실패: {type(e).__name__}: {e}")
        return None

    out: dict = payload.get("output") or {}
    if not out:
        return None

    price = _to_int(out.get("stck_prpr"))
    prev_close = _to_int(out.get("stck_prdy_clpr"))
    daily_return = _to_float(out.get("prdy_ctrt"))
    intraday_high = _to_int(out.get("stck_hgpr"))
    intraday_low = _to_int(out.get("stck_lwpr"))
    volume = _to_int(out.get("acml_vol"))
    trading_value = _to_int(out.get("acml_tr_pbmn"))
    lup = _is_limit_up_price(price, prev_close) if prev_close > 0 else False

    return {
        "code": code,
        "name": str(out.get("hts_kor_isnm", "")),
        "price": price,
        "prev_close": prev_close,
        "daily_return": daily_return,
        "intraday_high": intraday_high,
        "intraday_low": intraday_low,
        "volume": volume,
        "trading_value": trading_value,
        "is_limit_up": lup,
        "market_cap": 0,
        "turnover": float("nan"),
    }


def fetch_quotes_bulk(
    client: KISClient,
    codes: list[str],
    master_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """복수 종목 현재가 일괄 조회. rate limit은 client가 처리.

    Args:
        client: KIS API client.
        codes: 종목코드 리스트.
        master_df: 종목 마스터 (M5.5). 주어지면 market_cap/turnover 부착.

    Returns:
        SNAPSHOT_COLUMNS 중 rank 제외한 컬럼 DataFrame.
        실패 종목은 제외된다.
    """
    master_lookup: dict[str, int] = {}
    if master_df is not None and not master_df.empty:
        master_lookup = dict(zip(
            master_df["code"].astype(str),
            master_df["market_cap"].fillna(0).astype(int),
        ))

    records = []
    for code in codes:
        q = fetch_quote(client, code)
        if q is not None:
            mc = master_lookup.get(code, 0)
            q["market_cap"] = mc
            q["turnover"] = compute_turnover(q["trading_value"], mc)
            records.append(q)
    if not records:
        cols = [c for c in SNAPSHOT_COLUMNS if c != "rank"]
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(records)


# 상한가 계산은 src.common.limit_up 의 단일 정의를 사용 (M1: SSoT 통합).
from src.common.limit_up import is_limit_up as _is_limit_up_price  # noqa: E402
from src.common.limit_up import limit_up_price  # noqa: E402, F401
