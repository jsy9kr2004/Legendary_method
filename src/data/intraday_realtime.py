"""KIS API 기반 실시간 보조지표 fetcher (M6 모니터링용).

본 모듈은 09:00~10:30 평일 1~2초 단위 모니터링을 위해 4개 보조 지표를
조회한다. 분봉 거래대금 가속배율 계산은 `src.scalping.score.accel` 참고.

엔드포인트 / TR ID:
    분봉 시세    : /uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice
                   TR FHKST03010200 — 1분봉 30건 단위
    체결강도     : /uapi/domestic-stock/v1/quotations/inquire-ccnl
                   TR FHKST01010300 — 최근 30체결
    호가 잔량    : /uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn
                   TR FHKST01010200 — 매수/매도 10단계 호가 + 잔량
    외인기관 추정: /uapi/domestic-stock/v1/quotations/investor-trend-estimate
                   TR HHPTJ04160200 — 외인/기관 추정 누계 (09:30~14:30 4~5회 갱신)
    프로그램 매매: /uapi/domestic-stock/v1/quotations/program-trade-by-stock
                   TR FHPPG04650101 — 프로그램 체결 분봉 누계

⚠ 응답 필드명은 KIS open-trading-api / KIS Developer Portal 문서 기준 추정.
   운영 시 mock 모드로 한 번 검증 필요. 응답이 비어 있거나 필드명이 다를 경우
   각 fetcher 가 빈 결과(또는 None) 반환 + 경고 로그.
"""
from __future__ import annotations

from typing import Any

import httpx
import pandas as pd
from loguru import logger

from src.kis.client import KISApiError, KISClient

# ── 엔드포인트 / TR_ID ────────────────────────────────────────────────────────

_MINUTE_BAR_ENDPOINT = "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice"
_MINUTE_BAR_TR_ID = "FHKST03010200"

_CCNL_ENDPOINT = "/uapi/domestic-stock/v1/quotations/inquire-ccnl"
_CCNL_TR_ID = "FHKST01010300"

_ASKING_PRICE_ENDPOINT = "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn"
_ASKING_PRICE_TR_ID = "FHKST01010200"

# 2026-05-21 교체: inquire-investor (FHKST01010900) 가 외인/기관 빈 응답 + 프로그램
# 필드 자체 미제공 → KIS GitHub open-trading-api 확인 후 두 종목별 endpoint 로 교체.
_INVESTOR_TREND_ENDPOINT = "/uapi/domestic-stock/v1/quotations/investor-trend-estimate"
_INVESTOR_TREND_TR_ID = "HHPTJ04160200"

_PROGRAM_TRADE_ENDPOINT = "/uapi/domestic-stock/v1/quotations/program-trade-by-stock"
_PROGRAM_TRADE_TR_ID = "FHPPG04650101"


# ── 유틸 ──────────────────────────────────────────────────────────────────────


def _to_int(val: Any, default: int = 0) -> int:
    try:
        return int(str(val).strip() or default)
    except (TypeError, ValueError):
        return default


def _to_float(val: Any, default: float = float("nan")) -> float:
    try:
        return float(str(val).strip())
    except (TypeError, ValueError):
        return default


# ── 1) 분봉 시세 ──────────────────────────────────────────────────────────────


MINUTE_BAR_COLUMNS = [
    "code",
    "date",          # YYYYMMDD
    "time",          # HHMMSS
    "open",
    "high",
    "low",
    "close",
    "volume",
    "trading_value",
]


def fetch_minute_bars(
    client: KISClient,
    code: str,
    target_time: str | None = None,
) -> pd.DataFrame:
    """종목 1분봉 시세 조회.

    Args:
        client: KIS API client.
        code: 6자리 종목코드.
        target_time: 'HHMMSS' 형식. 해당 시각까지 직전 30개 분봉. None 이면 현재.

    Returns:
        MINUTE_BAR_COLUMNS 스키마 DataFrame, time 오름차순 (가장 최근이 마지막).
        실패 시 빈 DataFrame.

    참고 응답 필드 (output2):
        stck_bsop_date  영업일자 (YYYYMMDD)
        stck_cntg_hour  체결시간 (HHMMSS)
        stck_oprc       시가
        stck_hgpr       고가
        stck_lwpr       저가
        stck_prpr       현재가/종가
        cntg_vol        체결거래량
        acml_tr_pbmn    누적거래대금 (분봉당)
    """
    params = {
        "FID_ETC_CLS_CODE": "",
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": code,
        "FID_INPUT_HOUR_1": target_time or "",
        "FID_PW_DATA_INCU_YN": "Y",  # 과거 데이터 포함
    }
    try:
        payload = client.get(_MINUTE_BAR_ENDPOINT, _MINUTE_BAR_TR_ID, params=params)
    except KISApiError as e:
        logger.error(f"{code} 분봉 조회 실패: {e}")
        return pd.DataFrame(columns=MINUTE_BAR_COLUMNS)
    except httpx.HTTPError as e:
        # KIS 서버 5xx / 네트워크 단절 (tenacity 3회 재시도 후 실패). 종목 단위 격리.
        logger.warning(f"{code} 분봉 조회 HTTP 실패: {type(e).__name__}: {e}")
        return pd.DataFrame(columns=MINUTE_BAR_COLUMNS)

    rows: list[dict] = payload.get("output2") or []
    if not rows:
        return pd.DataFrame(columns=MINUTE_BAR_COLUMNS)

    records = []
    for r in rows:
        records.append({
            "code": code,
            "date": str(r.get("stck_bsop_date", "")),
            "time": str(r.get("stck_cntg_hour", "")),
            "open": _to_int(r.get("stck_oprc")),
            "high": _to_int(r.get("stck_hgpr")),
            "low": _to_int(r.get("stck_lwpr")),
            "close": _to_int(r.get("stck_prpr")),
            "volume": _to_int(r.get("cntg_vol")),
            "trading_value": _to_int(r.get("acml_tr_pbmn")),
        })
    df = pd.DataFrame(records, columns=MINUTE_BAR_COLUMNS)
    # 오름차순 (시간순)
    df = df.sort_values(["date", "time"]).reset_index(drop=True)
    # KIS acml_tr_pbmn 는 장 시작부터의 **누적 거래대금**.
    # 호출자 (compute_accel_ratio, worker recent_value sum 등) 는
    # 분봉당 거래대금을 가정하고 sum 한다 → diff 로 변환해야 정합.
    # 첫 봉은 diff NaN → 0 (응답은 30개라 한 봉 손실 영향 미미).
    df["trading_value"] = (
        df["trading_value"].diff().fillna(0).clip(lower=0).astype("int64")
    )
    return df


# ── 2) 체결강도 ───────────────────────────────────────────────────────────────


def fetch_ccnl_strength(client: KISClient, code: str) -> dict[str, Any] | None:
    """체결강도 조회 (당일 누적, 최근 30체결 row 중 가장 최신).

    KIS 공식 응답 필드 (`inquire-ccnl` / TR FHKST01010300, output array):
        stck_cntg_hour    체결 시각 (HHMMSS)
        stck_prpr         현재가
        prdy_vrss         전일 대비
        prdy_vrss_sign    전일 대비 부호
        cntg_vol          체결량 (해당 체결 1건)
        tday_rltv         **당일 체결강도** ← 100 = 매수=매도, 100↑ = 매수 우세
        prdy_ctrt         전일 대비율 (%)

    Returns:
        {
            "code": code,
            "ccnl_strength": float,  # tday_rltv (당일 누적 체결강도)
            "cntg_vol":      int,    # 최근 체결 1건의 체결량
            "buy_ratio":     float,  # 이 API 응답에 매수/매도 누적이 없어 NaN.
                                     # 카드 표시는 ccnl_strength 가 충분.
        }
        실패/응답 비어있음 시 None.

    정정 이력 (round 34):
        round 22 까지 응답 필드명을 `cttr` / `seln_cntg_smtn` / `shnu_cntg_smtn` 으로
        잘못 추정. 사용자 보고로 카드의 체결강도가 항상 "—" 표시되는 현상 진단 →
        공식 KIS 샘플 (koreainvestment/open-trading-api) 의 `chk_inquire_ccnl.py`
        COLUMN_MAPPING 확인. 실제 필드는 `tday_rltv` (당일 체결강도, 100=균형).
        매수/매도 누적 체결량은 본 API 에 없어 buy_ratio 는 NaN 으로 보고 — 굳이
        필요하면 별도 API (inquire-time-itemconclusion 등).
    """
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": code,
    }
    try:
        payload = client.get(_CCNL_ENDPOINT, _CCNL_TR_ID, params=params)
    except KISApiError as e:
        logger.error(f"{code} 체결강도 조회 실패: {e}")
        return None
    except httpx.HTTPError as e:
        logger.warning(f"{code} 체결강도 조회 HTTP 실패: {type(e).__name__}: {e}")
        return None

    # 응답 구조: payload["output"] = [{...}, ...] (체결 30건, 최신순). 일부 응답에서
    # output1 으로 오는 케이스 호환 위해 둘 다 시도. 일반적으로는 output (단수, list).
    rows = payload.get("output") or payload.get("output1") or []
    if isinstance(rows, dict):
        # 예외적으로 dict 면 그대로 사용 (단일 객체 응답 대응)
        out = rows
    elif isinstance(rows, list) and rows:
        # 가장 최신 체결 = 첫 번째 행 (API 가 최신순 반환). 안전 위해 stck_cntg_hour
        # 가 가장 큰 행 — 종목별로 정렬 상이할 수 있어 max 로 확정.
        out = max(
            rows,
            key=lambda r: str(r.get("stck_cntg_hour", "")) if isinstance(r, dict) else "",
        )
        if not isinstance(out, dict):
            return None
    else:
        return None

    ccnl_strength = _to_float(out.get("tday_rltv"))
    cntg_vol = _to_int(out.get("cntg_vol"))

    # round 34: NaN 응답 진단 로그 — KIS 가 cttr 빈 값을 반환하는 케이스 추적용.
    # 1회만 보고 싶진 않으니 debug 레벨 — 운영 시 명시적으로 켜야 보임.
    if ccnl_strength != ccnl_strength:
        logger.debug(
            f"{code} 체결강도 응답 비어있음 (tday_rltv 누락 또는 빈 문자열). "
            f"output 키 목록: {list(out.keys())[:8] if isinstance(out, dict) else type(out).__name__}"
        )

    return {
        "code": code,
        "ccnl_strength": ccnl_strength,
        "cntg_vol": cntg_vol,
        "buy_ratio": float("nan"),  # 이 API 응답에 누적 매수/매도 체결량 없음
    }


# ── 3) 호가 잔량 ──────────────────────────────────────────────────────────────


def fetch_asking_price(client: KISClient, code: str) -> dict[str, Any] | None:
    """매수/매도 호가 10단계 + 잔량 조회.

    Returns:
        {
            "code": code,
            "ask_total_volume": int,   # 매도호가 1~10단계 잔량 합계
            "bid_total_volume": int,   # 매수호가 1~10단계 잔량 합계
            "bid_ask_ratio": float,    # bid_total / ask_total
            "ask1_price": int,         # 매도 1호가 가격
            "bid1_price": int,         # 매수 1호가 가격
            "ask1_volume": int,
            "bid1_volume": int,
        }

    참고 응답 필드 (output1):
        askp1~askp10            매도 1~10호가 가격
        bidp1~bidp10            매수 1~10호가 가격
        askp_rsqn1~askp_rsqn10  매도 1~10호가 잔량
        bidp_rsqn1~bidp_rsqn10  매수 1~10호가 잔량
        total_askp_rsqn         매도호가 잔량 합계
        total_bidp_rsqn         매수호가 잔량 합계
    """
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": code,
    }
    try:
        payload = client.get(_ASKING_PRICE_ENDPOINT, _ASKING_PRICE_TR_ID, params=params)
    except KISApiError as e:
        logger.error(f"{code} 호가 조회 실패: {e}")
        return None
    except httpx.HTTPError as e:
        logger.warning(f"{code} 호가 조회 HTTP 실패: {type(e).__name__}: {e}")
        return None

    out = payload.get("output1") or payload.get("output") or {}
    if not out:
        return None

    ask_total = _to_int(out.get("total_askp_rsqn"))
    bid_total = _to_int(out.get("total_bidp_rsqn"))
    if ask_total == 0 and bid_total == 0:
        # fallback: 1~10단계 합산
        ask_total = sum(_to_int(out.get(f"askp_rsqn{i}")) for i in range(1, 11))
        bid_total = sum(_to_int(out.get(f"bidp_rsqn{i}")) for i in range(1, 11))

    bid_ask_ratio = (bid_total / ask_total) if ask_total > 0 else float("nan")

    return {
        "code": code,
        "ask_total_volume": ask_total,
        "bid_total_volume": bid_total,
        "bid_ask_ratio": bid_ask_ratio,
        "ask1_price": _to_int(out.get("askp1")),
        "bid1_price": _to_int(out.get("bidp1")),
        "ask1_volume": _to_int(out.get("askp_rsqn1")),
        "bid1_volume": _to_int(out.get("bidp_rsqn1")),
    }


# ── 4) 투자자별 순매수 ────────────────────────────────────────────────────────


def fetch_investor_trend_estimate(client: KISClient, code: str) -> dict[str, Any] | None:
    """외국인/기관 추정 누계 순매수 조회 (HHPTJ04160200).

    MTS "투자자동향 탭 > 추정(주)" 화면 동일. 증권사 직원이 장중에 집계/입력한
    추정 누계. 갱신 시각:
        외국인:   09:30 / 11:20 / 13:20 / 14:30
        기관종합: 10:00 / 11:20 / 13:20 / 14:30

    응답 구조 (`output2`, list len=5):
        bsop_hour_gb       시간대 구분 ("1"=가장 이른~"5"=가장 늦은). 5 가 최신.
        frgn_fake_ntby_qty 외국인 추정 누계 순매수 수량 (주, sign+18 zero-padded)
        orgn_fake_ntby_qty 기관 추정 누계 순매수 수량
        sum_fake_ntby_qty  합계

    Returns:
        {
            "code": code,
            "foreign_net_buy": int,     # 외인 추정 누계 (주)
            "institution_net_buy": int, # 기관 추정 누계 (주)
            "bsop_hour_gb": int,        # 갱신 시각 식별 (1~5)
        }
        실패/응답 비어있음 시 None.
    """
    try:
        payload = client.get(
            _INVESTOR_TREND_ENDPOINT, _INVESTOR_TREND_TR_ID,
            params={"MKSC_SHRN_ISCD": code},
        )
    except KISApiError as e:
        logger.error(f"{code} 외인기관 추정 조회 실패: {e}")
        return None
    except httpx.HTTPError as e:
        logger.warning(f"{code} 외인기관 추정 조회 HTTP 실패: {type(e).__name__}: {e}")
        return None

    raw = payload.get("output2")
    if not isinstance(raw, list) or not raw:
        return None

    # 가장 큰 bsop_hour_gb row = 가장 최신. 누락 시 list 마지막.
    def _gb(r: Any) -> int:
        if not isinstance(r, dict):
            return -1
        try:
            return int(str(r.get("bsop_hour_gb") or "").strip() or -1)
        except ValueError:
            return -1

    latest = max((r for r in raw if isinstance(r, dict)), key=_gb, default=None)
    if not latest:
        return None

    return {
        "code": code,
        "foreign_net_buy": _to_int(latest.get("frgn_fake_ntby_qty")),
        "institution_net_buy": _to_int(latest.get("orgn_fake_ntby_qty")),
        "bsop_hour_gb": _gb(latest),
    }


def fetch_program_trade_by_stock(client: KISClient, code: str) -> dict[str, Any] | None:
    """종목별 프로그램 체결 분봉 누계 조회 (FHPPG04650101).

    HTS [0465] / MTS 현재가 > 기타수급 > 프로그램 화면 동일. 분봉 30개 history
    (시간 desc) 의 가장 최신 row = 현재 시점 누계.

    응답 구조 (`output`, list len=30, 시간 desc):
        bsop_hour              시각 (HHMMSS)
        stck_prpr              현재가
        whol_smtn_ntby_qty     프로그램 합산 순매수 수량 (주)
        whol_smtn_ntby_tr_pbmn 프로그램 합산 순매수 거래대금 (원)
        whol_ntby_vol_icdc     직전 대비 증감 수량
        whol_ntby_tr_pbmn_icdc 직전 대비 증감 거래대금

    Returns:
        {
            "code": code,
            "program_net_buy": int,         # 프로그램 누계 순매수 (주)
            "program_net_buy_value": int,   # 프로그램 누계 순매수 거래대금 (원)
            "current_price": int,           # 응답 시점 현재가 (외인/기관 _value 추정용)
            "bsop_hour": str,               # 응답 시각 HHMMSS
        }
        실패/응답 비어있음 시 None.
    """
    try:
        payload = client.get(
            _PROGRAM_TRADE_ENDPOINT, _PROGRAM_TRADE_TR_ID,
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
        )
    except KISApiError as e:
        logger.error(f"{code} 프로그램매매 조회 실패: {e}")
        return None
    except httpx.HTTPError as e:
        logger.warning(f"{code} 프로그램매매 조회 HTTP 실패: {type(e).__name__}: {e}")
        return None

    raw = payload.get("output")
    if not isinstance(raw, list) or not raw:
        return None

    # list[0] = 가장 최신 분봉 (KIS 응답 시간 desc 정렬).
    latest = next((r for r in raw if isinstance(r, dict)), None)
    if not latest:
        return None

    return {
        "code": code,
        "program_net_buy": _to_int(latest.get("whol_smtn_ntby_qty")),
        "program_net_buy_value": _to_int(latest.get("whol_smtn_ntby_tr_pbmn")),
        "current_price": _to_int(latest.get("stck_prpr")),
        "bsop_hour": str(latest.get("bsop_hour") or "").strip(),
    }


def fetch_investor_flow(client: KISClient, code: str) -> dict[str, Any] | None:
    """외국인/기관/프로그램 당일 누적 순매수 조회.

    2026-05-21 본문 교체: 기존 inquire-investor (FHKST01010900) 가 외인/기관 빈
    응답 + 프로그램 필드 자체 미제공. 두 신규 종목별 endpoint 합산으로 교체:
        investor-trend-estimate (HHPTJ04160200)  — 외인/기관 추정 누계 (수량)
        program-trade-by-stock  (FHPPG04650101) — 프로그램 체결 누계 (수량+거래대금)

    사용자 정책 (2026-05-21): 모니터링 카드 매 tick 갱신, 캐싱 / 시간 분기 X.
    외인/기관은 KIS 자체가 09:30~14:30 4~5회만 갱신하지만 코드는 매번 동일 fetch.

    Returns:
        {
            "code": code,
            "foreign_net_buy": int,           # 외인 추정 누계 (주)
            "institution_net_buy": int,        # 기관 추정 누계 (주)
            "individual_net_buy": None,        # 신규 endpoint 미제공
            "program_net_buy": int,            # 프로그램 체결 누계 (주)
            "foreign_net_buy_value": int,      # 외인 수량 × 현재가 추정 (원)
            "institution_net_buy_value": int,  # 기관 수량 × 현재가 추정 (원)
            "program_net_buy_value": int,      # 프로그램 누계 거래대금 (KIS 직접 제공)
            "bsop_hour_gb": int | None,        # 외인/기관 갱신 시각 식별 (1~5)
            "bsop_hour": str | None,           # 프로그램 응답 시각 HHMMSS
        }
        두 endpoint 모두 실패 시 None.
    """
    trend = fetch_investor_trend_estimate(client, code)
    program = fetch_program_trade_by_stock(client, code)

    if trend is None and program is None:
        return None

    foreign = trend["foreign_net_buy"] if trend else 0
    inst = trend["institution_net_buy"] if trend else 0
    program_qty = program["program_net_buy"] if program else 0
    program_value = program["program_net_buy_value"] if program else 0

    # 외인/기관 거래대금은 KIS 가 미제공 — 수량 × 현재가 추정 (program 응답의 stck_prpr).
    # program 응답이 없으면 0 (수량은 정상 채워짐).
    price = program["current_price"] if program else 0
    foreign_value = foreign * price if price > 0 else 0
    inst_value = inst * price if price > 0 else 0

    return {
        "code": code,
        "foreign_net_buy": foreign,
        "institution_net_buy": inst,
        "individual_net_buy": None,  # 신규 endpoint 미제공
        "program_net_buy": program_qty,
        "foreign_net_buy_value": foreign_value,
        "institution_net_buy_value": inst_value,
        "program_net_buy_value": program_value,
        "bsop_hour_gb": trend["bsop_hour_gb"] if trend else None,
        "bsop_hour": program["bsop_hour"] if program else None,
    }
