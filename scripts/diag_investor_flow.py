"""KIS inquire-investor (FHKST01010900) 응답 진단.

round 36: round 22 정정으로 카드에서 제거된 외인/기관/프로그램 라인을
부활시키기 전 한 번 돌려서 raw 응답 구조 확인용. round 33/34 의 체결강도
사건(응답 필드명 추정 실패)과 동일 패턴을 사전 차단.

사용 예:
    python scripts/diag_investor_flow.py 005930
    python scripts/diag_investor_flow.py 005930 075180 091340

확인할 것:
    1. payload["output"] 가 dict 인지 list 인지
    2. list 라면 행 수, 어느 행에 누적 순매수가 들어있는지 (보통 최신 행)
    3. frgn_ntby_qty / orgn_ntby_qty / prsn_ntby_qty / pgtr_ntby_qty 존재 여부
    4. frgn_ntby_tr_pbmn (금액) 존재 여부 — 카드/결정 레포트 표시 단위 결정용
    5. 응답 시점에 값이 0인지 (장중 호출 시 누적이 살아있어야 정상)
"""
from __future__ import annotations

import argparse
import json
import sys

from src.config import load_settings
from src.kis.client import KISApiError, KISClient


_ENDPOINT = "/uapi/domestic-stock/v1/quotations/inquire-investor"
_TR_ID = "FHKST01010900"


def diag(client: KISClient, code: str) -> None:
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": code,
    }
    print(f"\n===== {code} =====")
    try:
        payload = client.get(_ENDPOINT, _TR_ID, params=params)
    except KISApiError as e:
        print(f"  KIS API 실패: {e}")
        return

    rt_cd = payload.get("rt_cd")
    msg = payload.get("msg1") or payload.get("msg")
    print(f"  rt_cd={rt_cd} msg={msg}")

    keys = sorted(k for k in payload.keys() if k not in ("rt_cd", "msg_cd", "msg1"))
    print(f"  top-level keys: {keys}")

    out = payload.get("output") or payload.get("output1") or payload.get("output2")
    if out is None:
        print("  ⚠ output / output1 / output2 모두 없음 — 응답 키 위 list 확인")
        return

    if isinstance(out, dict):
        print(f"  output type=dict, keys={list(out.keys())[:20]}")
        print(f"  raw (truncated):\n{json.dumps(out, indent=2, ensure_ascii=False)[:1200]}")
    elif isinstance(out, list):
        print(f"  output type=list, len={len(out)}")
        for i, row in enumerate(out[:3]):
            if isinstance(row, dict):
                print(f"  [{i}] keys={list(row.keys())[:14]}")
                snippet = {k: row.get(k) for k in (
                    "stck_bsop_date", "stck_cntg_hour", "bsop_hour",
                    "frgn_ntby_qty", "orgn_ntby_qty", "prsn_ntby_qty",
                    "pgtr_ntby_qty", "frgn_ntby_tr_pbmn", "orgn_ntby_tr_pbmn",
                )}
                print(f"  [{i}] selected: {snippet}")
        if len(out) > 3:
            last = out[-1]
            if isinstance(last, dict):
                print(f"  [last={len(out)-1}] keys={list(last.keys())[:14]}")
                snippet = {k: last.get(k) for k in (
                    "stck_bsop_date", "stck_cntg_hour", "bsop_hour",
                    "frgn_ntby_qty", "orgn_ntby_qty", "prsn_ntby_qty",
                    "pgtr_ntby_qty", "frgn_ntby_tr_pbmn", "orgn_ntby_tr_pbmn",
                )}
                print(f"  [last] selected: {snippet}")
    else:
        print(f"  output type={type(out).__name__} (unexpected) value={out!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("codes", nargs="+", help="종목코드 1개 이상 (예: 005930 075180)")
    args = parser.parse_args()

    settings = load_settings()
    print(f"KIS_API_MODE={settings.kis_api_mode}")
    print(f"endpoint={_ENDPOINT}  tr_id={_TR_ID}")

    with KISClient(settings) as client:
        for code in args.codes:
            diag(client, code)

    return 0


if __name__ == "__main__":
    sys.exit(main())
