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


_ENDPOINT = "/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily"
_TR_ID = "FHPTJ04160001"


def diag(client: KISClient, code: str) -> None:
    today = date.today().strftime("%Y%m%d")
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": code,
        "FID_INPUT_DATE_1": today,
        "FID_ORG_ADJ_PRC": "",
        "FID_ETC_CLS_CODE": "",
    }
    print(f"\n===== {code} =====")
    print(f"  endpoint={_ENDPOINT}  tr_id={_TR_ID}  date={today}")

    try:
        payload = client.get(_ENDPOINT, _TR_ID, params=params)
    except KISApiError as e:
        print(f"  KIS API 실패: {e}")
        return

    rt_cd = payload.get("rt_cd")
    msg = payload.get("msg1") or payload.get("msg")
    print(f"  rt_cd={rt_cd} msg={msg}")

    top_keys = sorted(k for k in payload.keys() if k not in ("rt_cd", "msg_cd", "msg1"))
    print(f"  top-level keys: {top_keys}")

    for okey in ("output", "output1", "output2"):
        out = payload.get(okey)
        if out is None:
            continue
        print(f"\n  --- payload['{okey}'] ---")
        if isinstance(out, dict):
            print(f"  type=dict, keys ({len(out)}개): {list(out.keys())}")
            print(json.dumps(out, indent=2, ensure_ascii=False)[:1500])
        elif isinstance(out, list):
            print(f"  type=list, len={len(out)}")
            if out and isinstance(out[0], dict):
                print(f"  [0] keys ({len(out[0])}개): {list(out[0].keys())}")
                print(f"  [0] full:")
                print(json.dumps(out[0], indent=2, ensure_ascii=False))
                if len(out) > 1:
                    print(f"\n  [1]:")
                    print(json.dumps(out[1], indent=2, ensure_ascii=False)[:600])
                if len(out) >= 20:
                    print(f"\n  [{len(out)-1}] (가장 오래된?):")
                    print(json.dumps(out[-1], indent=2, ensure_ascii=False)[:600])
        else:
            print(f"  type={type(out).__name__} (unexpected) value={out!r}")


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
