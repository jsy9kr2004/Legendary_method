"""KIS investor-trade-by-stock-daily (FHPTJ04160001) raw 응답 진단.

종목별 투자자매매동향(일별) — 외인/기관/프로그램 일별 history.
20일 평균 비교 기능 도입 전 응답 구조 / 필드명 / 응답 길이 확정용.

사용:
    python scripts/diag_investor_daily.py 005930
    python scripts/diag_investor_daily.py 005930 091340

확인할 것:
    1. output1 / output2 위치 — 어디에 일별 row 가 있는지
    2. row 개수 (한 번에 몇 일치 반환?)
    3. 외인/기관/개인/프로그램 수량·금액 필드명
    4. 일자 필드 (stck_bsop_date 등)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from src.config import load_settings
from src.kis.client import KISApiError, KISClient


# Phase 2 — 4 endpoint 통합 진단 (2026-05-22)
_ENDPOINTS = [
    {
        "name": "투자자매매동향(종목별 일별) — 외인/기관",
        "path": "/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily",
        "tr_id": "FHPTJ04160001",
        "params_fn": lambda code, today: {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": code,
            "FID_INPUT_DATE_1": today,
            "FID_ORG_ADJ_PRC": "",
            "FID_ETC_CLS_CODE": "",
        },
    },
    {
        "name": "프로그램매매(종목별 일별)",
        "path": "/uapi/domestic-stock/v1/quotations/program-trade-by-stock-daily",
        "tr_id": "FHPPG04650201",
        "params_fn": lambda code, today: {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": code,
            "FID_INPUT_DATE_1": today,
        },
    },
    {
        "name": "시장별 투자자매매동향(일별) — KOSPI",
        "path": "/uapi/domestic-stock/v1/quotations/inquire-investor-daily-by-market",
        "tr_id": "FHPTJ04040000",
        "market_only": True,
        "params_fn": lambda _code, today: {
            "FID_COND_MRKT_DIV_CODE": "U",
            "FID_INPUT_ISCD": "0001",
            "FID_INPUT_DATE_1": today,
            "FID_INPUT_ISCD_1": "KSP",
            "FID_INPUT_DATE_2": today,
            "FID_INPUT_ISCD_2": "0001",
        },
    },
    {
        "name": "시장별 투자자매매동향(일별) — KOSDAQ",
        "path": "/uapi/domestic-stock/v1/quotations/inquire-investor-daily-by-market",
        "tr_id": "FHPTJ04040000",
        "market_only": True,
        "params_fn": lambda _code, today: {
            "FID_COND_MRKT_DIV_CODE": "U",
            "FID_INPUT_ISCD": "1001",
            "FID_INPUT_DATE_1": today,
            "FID_INPUT_ISCD_1": "KSQ",
            "FID_INPUT_DATE_2": today,
            "FID_INPUT_ISCD_2": "1001",
        },
    },
    {
        "name": "프로그램매매 종합현황(일별) — KOSPI",
        "path": "/uapi/domestic-stock/v1/quotations/comp-program-trade-daily",
        "tr_id": "FHPPG04600001",
        "market_only": True,
        "params_fn": lambda _code, today: {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_MRKT_CLS_CODE": "K",   # K=KOSPI, Q=KOSDAQ
            "FID_INPUT_DATE_1": "",
            "FID_INPUT_DATE_2": today,
        },
    },
    {
        "name": "프로그램매매 종합현황(일별) — KOSDAQ",
        "path": "/uapi/domestic-stock/v1/quotations/comp-program-trade-daily",
        "tr_id": "FHPPG04600001",
        "market_only": True,
        "params_fn": lambda _code, today: {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_MRKT_CLS_CODE": "Q",
            "FID_INPUT_DATE_1": "",
            "FID_INPUT_DATE_2": today,
        },
    },
]


def diag(client: KISClient, code: str) -> None:
    today = date.today().strftime("%Y%m%d")
    print(f"\n\n#### {code} ####  (today={today})")
    market_done = False
    for ep in _ENDPOINTS:
        # 시장 endpoint 은 첫 종목 호출 시 1회만 (종목 무관)
        if ep.get("market_only"):
            if market_done and ep["name"] != _ENDPOINTS[2]["name"]:
                # 다른 시장 endpoint 는 종목 무관해도 호출 (KOSPI/KOSDAQ 각각)
                pass

        print(f"\n  ===== {ep['name']} =====")
        print(f"    path={ep['path']}  tr_id={ep['tr_id']}")
        params = ep["params_fn"](code, today)
        try:
            payload = client.get(ep["path"], ep["tr_id"], params=params)
        except KISApiError as e:
            print(f"    ❌ KIS API 실패: {e}")
            continue
        except Exception as e:  # noqa: BLE001
            print(f"    ❌ Exception: {type(e).__name__}: {e}")
            continue

        rt_cd = payload.get("rt_cd")
        msg = payload.get("msg1") or payload.get("msg")
        print(f"    rt_cd={rt_cd} msg={msg}")
        top_keys = sorted(k for k in payload.keys() if k not in ("rt_cd", "msg_cd", "msg1"))
        print(f"    top-level keys: {top_keys}")

        for okey in ("output", "output1", "output2"):
            out = payload.get(okey)
            if out is None:
                continue
            print(f"\n    --- payload['{okey}'] ---")
            if isinstance(out, dict):
                print(f"    type=dict, keys ({len(out)}개): {list(out.keys())}")
                print(json.dumps(out, indent=2, ensure_ascii=False)[:1500])
            elif isinstance(out, list):
                print(f"    type=list, len={len(out)}")
                if out and isinstance(out[0], dict):
                    print(f"    [0] keys ({len(out[0])}개): {list(out[0].keys())}")
                    print(f"    [0] full:")
                    print(json.dumps(out[0], indent=2, ensure_ascii=False))
                    if len(out) > 1:
                        print(f"\n    [1]:")
                        print(json.dumps(out[1], indent=2, ensure_ascii=False)[:600])
                    if len(out) >= 20:
                        print(f"\n    [{len(out)-1}] (가장 오래된?):")
                        print(json.dumps(out[-1], indent=2, ensure_ascii=False)[:600])
            else:
                print(f"    type={type(out).__name__} (unexpected) value={out!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("codes", nargs="+", help="종목코드 1개 이상 (예: 005930)")
    args = parser.parse_args()

    settings = load_settings()
    print(f"KIS_API_MODE={settings.kis_api_mode}")

    with KISClient(settings) as client:
        for code in args.codes:
            diag(client, code)

    return 0


if __name__ == "__main__":
    sys.exit(main())
