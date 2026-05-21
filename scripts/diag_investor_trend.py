"""KIS investor-trend-estimate + program-trade-by-stock raw 응답 진단.

2026-05-21 발견: 기존 inquire-investor (FHKST01010900) 가 외인/기관 빈 응답 +
프로그램 필드 자체 미제공. KIS GitHub open-trading-api 에 두 종목별 endpoint 확인:

1. investor-trend-estimate (HHPTJ04160200) — 종목별 외인기관 추정가집계
   - MTS "투자자동향 탭 > 추정(주)" 화면
   - 갱신 시각: 외인 09:30/11:20/13:20/14:30, 기관종합 10:00/11:20/13:20/14:30

2. program-trade-by-stock (FHPPG04650101) — 종목별 프로그램매매추이(체결)
   - HTS [0465] / MTS 현재가 > 기타수급 > 프로그램
   - 체결 기반 실시간

사용 예:
    python scripts/diag_investor_trend.py 005930
    python scripts/diag_investor_trend.py 005930 091340 080220

확인할 것:
    1. payload["output"] / output1 / output2 위치 (각 endpoint 다름)
    2. dict 인지 list 인지 — list 라면 row 의미 (시각? 분봉? 누적?)
    3. 외국인/기관/프로그램 매수·매도 수량·금액 필드명
    4. 응답 시점에 0/빈값인지 (장중 호출 시 의미 있는 값이어야 정상)
"""
from __future__ import annotations

import argparse
import json
import sys

from src.config import load_settings
from src.kis.client import KISApiError, KISClient


_ENDPOINTS = [
    {
        "name": "investor-trend-estimate (외인/기관 추정)",
        "path": "/uapi/domestic-stock/v1/quotations/investor-trend-estimate",
        "tr_id": "HHPTJ04160200",
        "params_fn": lambda code: {"MKSC_SHRN_ISCD": code},
        "expected_output_key": "output2",  # KIS examples 기준 — 실제 확인 필요
    },
    {
        "name": "program-trade-by-stock (프로그램 체결)",
        "path": "/uapi/domestic-stock/v1/quotations/program-trade-by-stock",
        "tr_id": "FHPPG04650101",
        "params_fn": lambda code: {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": code,
        },
        "expected_output_key": "output",
    },
]


def diag(client: KISClient, code: str, ep: dict) -> None:
    print(f"\n===== {code} — {ep['name']} =====")
    print(f"  path={ep['path']}  tr_id={ep['tr_id']}")
    try:
        payload = client.get(ep["path"], ep["tr_id"], params=ep["params_fn"](code))
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
            print(f"  type=dict, keys={list(out.keys())[:25]}")
            print(f"  raw (truncated):")
            print(json.dumps(out, indent=2, ensure_ascii=False)[:2000])
        elif isinstance(out, list):
            print(f"  type=list, len={len(out)}")
            for i, row in enumerate(out[:3]):
                if isinstance(row, dict):
                    print(f"  [{i}] keys ({len(row)}개): {list(row.keys())[:25]}")
                    print(f"  [{i}] full:")
                    print(json.dumps(row, indent=2, ensure_ascii=False)[:1500])
            if len(out) > 3:
                last = out[-1]
                if isinstance(last, dict):
                    print(f"\n  [last={len(out)-1}] keys ({len(last)}개): {list(last.keys())[:25]}")
                    print(f"  [last] full:")
                    print(json.dumps(last, indent=2, ensure_ascii=False)[:1500])
        else:
            print(f"  type={type(out).__name__} (unexpected) value={out!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("codes", nargs="+", help="종목코드 1개 이상 (예: 005930 091340)")
    args = parser.parse_args()

    settings = load_settings()
    print(f"KIS_API_MODE={settings.kis_api_mode}")

    with KISClient(settings) as client:
        for code in args.codes:
            for ep in _ENDPOINTS:
                diag(client, code, ep)

    return 0


if __name__ == "__main__":
    sys.exit(main())
