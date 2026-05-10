"""KIS API 기반 실시간 보조지표 fetcher (M6 모니터링용).

본 모듈은 09:00~10:30 평일 1~2초 단위 모니터링을 위해 4개 보조 지표를
조회한다. 분봉 거래대금 가속배율 계산은 `src.jongbae.momentum` 참고.

엔드포인트 / TR ID:
    분봉 시세    : /uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice
                   TR FHKST03010200 — 1분봉 30건 단위
    체결강도     : /uapi/domestic-stock/v1/quotations/inquire-ccnl
                   TR FHKST01010300 — 최근 30체결
    호가 잔량    : /uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn
                   TR FHKST01010200 — 매수/매도 10단계 호가 + 잔량
    투자자별 매매: /uapi/domestic-stock/v1/quotations/inquire-investor
                   TR FHKST01010900 — 외국인/기관/개인 누적 순매수

⚠ 응답 필드명은 KIS open-trading-api / KIS Developer Portal 문서 기준 추정.
   운영 시 mock 모드로 한 번 검증 필요. 응답이 비어 있거나 필드명이 다를 경우
   각 fetcher 가 빈 결과(또는 None) 반환 + 경고 로그.
"""
from __future__ import annotations

from typing import Any

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

_INVESTOR_ENDPOINT = "/uapi/domestic-stock/v1/quotations/inquire-investor"
_INVESTOR_TR_ID = "FHKST01010900"


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
    return df


# ── 2) 체결강도 ───────────────────────────────────────────────────────────────


def fetch_ccnl_strength(client: KISClient, code: str) -> dict[str, Any] | None:
    """체결강도 조회 (당일 누적 + 최근 30체결).

    Returns:
        {
            "code": code,
            "ccnl_strength": float,    # 체결강도 (cttr). 100 = 매수=매도, 100↑ = 매수 우세
            "buy_volume": int,         # 누적 매수 체결량
            "sell_volume": int,        # 누적 매도 체결량
            "buy_ratio": float,        # buy / (buy + sell) * 100
        }
        실패 시 None.

    참고 응답 필드 (output1):
        cttr             체결강도
        seln_cntg_qty    매도 체결량
        shnu_cntg_qty    매수 체결량
        seln_cntg_smtn   매도 체결 누적
        shnu_cntg_smtn   매수 체결 누적
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

    out = payload.get("output1") or payload.get("output") or {}
    if not out:
        # output 이 list 형태일 수도 있음 (체결 30건). 최근 1건 사용.
        rows = payload.get("output2") or []
        if not rows:
            return None
        out = rows[0]

    if isinstance(out, list):
        out = out[0] if out else {}

    ccnl_strength = _to_float(out.get("cttr"))
    sell_qty = _to_int(out.get("seln_cntg_smtn") or out.get("seln_cntg_qty"))
    buy_qty = _to_int(out.get("shnu_cntg_smtn") or out.get("shnu_cntg_qty"))

    total = sell_qty + buy_qty
    buy_ratio = (buy_qty / total * 100.0) if total > 0 else float("nan")

    return {
        "code": code,
        "ccnl_strength": ccnl_strength,
        "buy_volume": buy_qty,
        "sell_volume": sell_qty,
        "buy_ratio": buy_ratio,
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


def fetch_investor_flow(client: KISClient, code: str) -> dict[str, Any] | None:
    """외국인/기관/개인/프로그램 당일 누적 순매수 조회.

    Returns:
        {
            "code": code,
            "foreign_net_buy": int,    # 외국인 순매수 (단위: 주)
            "institution_net_buy": int,  # 기관 순매수
            "individual_net_buy": int,   # 개인 순매수
            "program_net_buy": int,     # 프로그램 순매수 (있으면)
            "foreign_net_buy_value": int,  # 외국인 순매수 금액 (원, 있으면)
        }

    참고 응답 필드 (output):
        frgn_ntby_qty   외국인 순매수 수량
        orgn_ntby_qty   기관 순매수 수량
        prsn_ntby_qty   개인 순매수 수량
        frgn_ntby_tr_pbmn 외국인 순매수 거래대금
        orgn_ntby_tr_pbmn 기관 순매수 거래대금
    """
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": code,
    }
    try:
        payload = client.get(_INVESTOR_ENDPOINT, _INVESTOR_TR_ID, params=params)
    except KISApiError as e:
        logger.error(f"{code} 투자자 매매 조회 실패: {e}")
        return None

    out = payload.get("output") or {}
    if isinstance(out, list):
        # 시간별 응답인 경우 최근 행 사용
        out = out[0] if out else {}
    if not out:
        return None

    return {
        "code": code,
        "foreign_net_buy": _to_int(out.get("frgn_ntby_qty")),
        "institution_net_buy": _to_int(out.get("orgn_ntby_qty")),
        "individual_net_buy": _to_int(out.get("prsn_ntby_qty")),
        "program_net_buy": _to_int(out.get("pgtr_ntby_qty")),
        "foreign_net_buy_value": _to_int(out.get("frgn_ntby_tr_pbmn")),
        "institution_net_buy_value": _to_int(out.get("orgn_ntby_tr_pbmn")),
    }
