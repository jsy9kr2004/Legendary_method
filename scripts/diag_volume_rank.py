"""KIS volume-rank (FHPST01710000) 응답 진단 — 50위 확장 가능성 확인.

목적 (2026-05-19 round 41 후속 2 후속):
    `fetch_volume_rank` 가 한 호출당 30개만 반환하는 한계를 깨기 위해
    KIS API 의 페이지네이션 메커니즘이 살아있는지 확인.

KIS API 일반 페이지네이션 패턴:
    - 응답 헤더: `tr_cont` = "F"|"M" (다음 페이지 있음) / "D"|"E" (마지막)
    - 응답 본문: `ctx_area_fk100`, `ctx_area_nk100` (또는 200) — 다음 호출에
      그대로 전달
    - 다음 호출 요청 헤더: `tr_cont` = "N" + 위 ctx 값 동봉

본 스크립트는 production `KISClient` 를 우회하고 httpx 로 직접 호출 — 응답
헤더(tr_cont) 까지 보기 위함. 두 번 호출:
    1. 1st 호출 (tr_cont="") — top-level 키 + ctx_area_fk100/nk100 존재 확인
    2. 2nd 호출 (tr_cont="N" + ctx 값) — 31~60위가 나오는지 확인

발견 가능한 결과:
    A. ctx 키 존재 + 2nd 호출이 31~60위 반환 → 페이지네이션 가능. 코드 fix
       방향: `fetch_volume_rank` 가 top_n>30 일 때 반복 호출하며 ctx 이어 받기.
    B. ctx 키 존재하지만 2nd 호출이 1~30위 그대로 반환 → 페이지네이션 지원 X
       (껍데기만). Plan B (KOSPI/KOSDAQ 분리 또는 가격 범위 분할) 로 전환.
    C. ctx 키 없음 + tr_cont 헤더 무의미 → 본 endpoint 자체가 페이지네이션
       미지원. 위와 동일하게 Plan B.

⚠ 장중 실행 권장 (rt_cd=0 응답 보장). 장 외 시간엔 응답이 비거나 다를 수 있음.

사용:
    python scripts/diag_volume_rank.py
    python scripts/diag_volume_rank.py --blng 0   # 거래량 정렬로 비교
"""
from __future__ import annotations

import argparse
import json
import sys

import httpx

from src.config import load_settings
from src.kis import auth
from src.kis.client import KISClient


_ENDPOINT = "/uapi/domestic-stock/v1/quotations/volume-rank"
_TR_ID = "FHPST01710000"


def _build_params(blng: str) -> dict[str, str]:
    return {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_COND_SCR_DIV_CODE": "20171",
        "FID_INPUT_ISCD": "0000",
        "FID_DIV_CLS_CODE": "0",
        "FID_BLNG_CLS_CODE": blng,
        "FID_TRGT_CLS_CODE": "111111111",
        "FID_TRGT_EXLS_CLS_CODE": "000000",
        "FID_INPUT_PRICE_1": "",
        "FID_INPUT_PRICE_2": "",
        "FID_VOL_CNT": "",
        "FID_INPUT_DATE_1": "",
    }


def _summarize_top_keys(payload: dict) -> None:
    """상위 키 + 페이지네이션 의심 필드 출력."""
    top_keys = sorted(payload.keys())
    print(f"  top-level keys: {top_keys}")

    suspects = [
        "ctx_area_fk100", "ctx_area_nk100",
        "ctx_area_fk200", "ctx_area_nk200",
        "ctx_area_fk300", "ctx_area_nk300",
    ]
    for s in suspects:
        v = payload.get(s)
        if v is not None and v != "":
            print(f"  ★ {s} = {v!r}")
    if not any(payload.get(s) for s in suspects):
        print("  ⚠ ctx_area_fk*/nk* 페이지네이션 키 없음 — 본 endpoint 미지원 가능성")


def _summarize_output(payload: dict, label: str) -> list[dict]:
    out = payload.get("output")
    if not isinstance(out, list):
        print(f"  [{label}] output 가 list 아님: {type(out).__name__}")
        return []
    print(f"  [{label}] output len = {len(out)}")
    for row in out[:3] + out[-2:]:
        if not isinstance(row, dict):
            continue
        rank = row.get("data_rank")
        code = row.get("mksc_shrn_iscd")
        name = row.get("hts_kor_isnm")
        tv = row.get("acml_tr_pbmn")
        vol = row.get("acml_vol")
        print(f"     rank={rank:>3} {code} {name:<25} 거래대금={tv:>15} 거래량={vol}")
    return out


def _call_raw(
    settings,
    credential,
    params: dict[str, str],
    tr_cont: str = "",
    ctx_fk: str = "",
    ctx_nk: str = "",
) -> tuple[dict, dict]:
    """httpx 로 직접 호출 — 응답 헤더까지 노출.

    Returns:
        (payload_json, response_headers_dict)
    """
    base_url = auth.kis_base_url(settings.kis_api_mode)
    token = auth.get_token(settings, credential)
    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token.access_token}",
        "appkey": credential.app_key,
        "appsecret": credential.app_secret,
        "tr_id": _TR_ID,
        "custtype": "P",
        "tr_cont": tr_cont,
    }
    p = dict(params)
    if ctx_fk:
        p["CTX_AREA_FK100"] = ctx_fk
        p["CTX_AREA_NK100"] = ctx_nk

    with httpx.Client(timeout=10) as http:
        resp = http.get(f"{base_url}{_ENDPOINT}", headers=headers, params=p)
        resp.raise_for_status()
        return resp.json(), dict(resp.headers)


def _scenario_market_split(settings, cred, blng: str) -> dict[str, set]:
    """Plan B-1: FID_COND_MRKT_DIV_CODE 변형 시도.

    KIS docs 상 "J" 가 통합. "0"/"1"/"K"/"Q"/"00"/"01" 가 KOSPI/KOSDAQ 분리로
    동작하는지 확인. rt_cd=0 + 30개 응답 + 첫 종목이 J 와 다르면 분리 가능.
    """
    print("\n##### Plan B-1: FID_COND_MRKT_DIV_CODE 변형 #####")
    results: dict[str, set] = {}
    for mkt in ["J", "0", "1", "K", "Q", "00", "01"]:
        params = _build_params(blng)
        params["FID_COND_MRKT_DIV_CODE"] = mkt
        try:
            payload, _ = _call_raw(settings, cred, params, tr_cont="")
        except (httpx.HTTPError, Exception) as e:  # noqa: BLE001
            print(f"  mkt={mkt!r:5s} → 예외: {type(e).__name__}: {e}")
            continue
        rt = payload.get("rt_cd")
        msg = payload.get("msg1", "")
        out = payload.get("output") or []
        codes = {str(r.get("mksc_shrn_iscd", "")) for r in out if isinstance(r, dict)}
        first = next(iter(out), {}) if isinstance(out, list) else {}
        first_str = f"{first.get('data_rank','?')}.{first.get('hts_kor_isnm','')}" if first else "—"
        print(f"  mkt={mkt!r:5s} rt={rt} len={len(out):>2}  첫종목={first_str:<25}  msg={msg[:30]}")
        if rt == "0" and len(codes) > 0:
            results[mkt] = codes
    # 비교: "J" 와 다른 값이 다른 종목 set 이면 분리 작동
    j_codes = results.get("J", set())
    for mkt, codes in results.items():
        if mkt == "J":
            continue
        only_in_alt = codes - j_codes
        if only_in_alt:
            print(f"  ★ mkt={mkt!r} 는 J 와 다른 종목 {len(only_in_alt)} 개 — 분리 가능 hint")
    return results


def _scenario_price_split(settings, cred, blng: str) -> None:
    """Plan B-2: 가격 범위 분할.

    FID_INPUT_PRICE_1/_2 로 저가/고가 분리해서 두 번 호출. 거래대금 1위 대형주
    (보통 5만원~ 고가) + 저가 단타주 양쪽을 cover.

    추가로 FID_TRGT_EXLS_CLS_CODE 로 ETF 제외 가능한지도 시도.
    """
    print("\n##### Plan B-2: 가격 범위 분할 #####")
    cases = [
        ("저가", "0", "10000"),
        ("중가", "10001", "100000"),
        ("고가", "100001", "9999999"),
        ("전체", "", ""),
    ]
    union: dict[str, dict] = {}
    for label, lo, hi in cases:
        params = _build_params(blng)
        params["FID_INPUT_PRICE_1"] = lo
        params["FID_INPUT_PRICE_2"] = hi
        try:
            payload, _ = _call_raw(settings, cred, params, tr_cont="")
        except (httpx.HTTPError, Exception) as e:  # noqa: BLE001
            print(f"  {label}({lo}~{hi}) → 예외: {type(e).__name__}: {e}")
            continue
        rt = payload.get("rt_cd")
        out = payload.get("output") or []
        # 진짜 가격 범위 필터되는지 확인 — 첫 종목 가격
        first = next(iter(out), {}) if isinstance(out, list) else {}
        first_price = int(first.get("stck_prpr", 0) or 0) if first else 0
        first_str = f"{first.get('data_rank','?')}.{first.get('hts_kor_isnm','')}({first_price:,}원)" if first else "—"
        print(f"  {label:5s} ({lo or '∞-':>8}~{hi or '-∞':>8}) rt={rt} len={len(out):>2}  첫종목={first_str}")
        for r in out:
            if isinstance(r, dict):
                code = str(r.get("mksc_shrn_iscd", ""))
                tv = int(r.get("acml_tr_pbmn", 0) or 0)
                if code and (code not in union or tv > union[code]["tv"]):
                    union[code] = {
                        "tv": tv,
                        "name": r.get("hts_kor_isnm", ""),
                        "price": int(r.get("stck_prpr", 0) or 0),
                        "label": label,
                    }
    # 합집합 후 거래대금 desc 정렬
    sorted_union = sorted(union.values(), key=lambda x: x["tv"], reverse=True)
    print(f"  ── 가격 분할 union: 고유 종목 {len(sorted_union)}개 ──")
    print(f"     거래대금 desc top 50:")
    for i, r in enumerate(sorted_union[:50], 1):
        print(f"       {i:>3}. {r['name']:<25} ({r['price']:>8,}원) "
              f"거래대금={r['tv']/1e8:>10,.0f}억  via={r['label']}")
    if len(sorted_union) >= 50:
        print(f"  ✅ 가격 분할로 거래대금 50위 cover 가능 (호출 {len(cases)-1}회)")
    else:
        print(f"  ⚠ 가격 분할 합집합도 {len(sorted_union)}개 — 50위 cover 부족")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--blng", default="3",
        help="FID_BLNG_CLS_CODE. '3'=거래금액순(기본), '0'=평균거래량(비교용)",
    )
    parser.add_argument(
        "--plan-b", action="store_true",
        help="페이지네이션 미지원 확인 후 Plan B (시장 분리 + 가격 분할) 검증",
    )
    args = parser.parse_args()

    settings = load_settings()
    base_url = auth.kis_base_url(settings.kis_api_mode)
    print(f"KIS_API_MODE={settings.kis_api_mode} base_url={base_url}")
    print(f"endpoint={_ENDPOINT} tr_id={_TR_ID} blng={args.blng}")

    # 1st credential 만 사용 (라운드 로빈 무시) — 진단 의미
    with KISClient(settings) as client:
        cred = client._slots[0][0]

    params = _build_params(args.blng)

    # ── 1st call ─────────────────────────────────────────────────────────
    print("\n===== 1st call (tr_cont='') =====")
    payload1, headers1 = _call_raw(settings, cred, params, tr_cont="")
    print(f"  rt_cd={payload1.get('rt_cd')} msg={payload1.get('msg1')}")
    resp_tr_cont = headers1.get("tr_cont") or headers1.get("Tr_cont")
    print(f"  response header tr_cont = {resp_tr_cont!r}")
    _summarize_top_keys(payload1)
    out1 = _summarize_output(payload1, "1st")

    fk = payload1.get("ctx_area_fk100", "")
    nk = payload1.get("ctx_area_nk100", "")

    has_ctx = bool((fk and str(fk).strip()) or (nk and str(nk).strip()))
    has_more_hint = resp_tr_cont in ("F", "M")

    print(f"\n  → 페이지네이션 가능 hint: ctx={has_ctx}, tr_cont={resp_tr_cont!r}")

    # ── 2nd call (페이지네이션 시도) ───────────────────────────────────
    if has_ctx or has_more_hint:
        print("\n===== 2nd call (tr_cont='N', ctx 동봉) =====")
        payload2, headers2 = _call_raw(
            settings, cred, params,
            tr_cont="N",
            ctx_fk=str(fk),
            ctx_nk=str(nk),
        )
        print(f"  rt_cd={payload2.get('rt_cd')} msg={payload2.get('msg1')}")
        resp_tr_cont2 = headers2.get("tr_cont") or headers2.get("Tr_cont")
        print(f"  response header tr_cont = {resp_tr_cont2!r}")
        _summarize_top_keys(payload2)
        out2 = _summarize_output(payload2, "2nd")

        # 1st vs 2nd 가 같은 종목인지 (= 페이지네이션 효과 없음) 비교
        codes1 = {str(r.get("mksc_shrn_iscd", "")) for r in out1}
        codes2 = {str(r.get("mksc_shrn_iscd", "")) for r in out2}
        overlap = codes1 & codes2
        only_in_2 = codes2 - codes1
        print()
        print(f"  ── 비교 ──")
        print(f"  1st ∩ 2nd = {len(overlap)} 종목 (겹침)")
        print(f"  2nd 에만 = {len(only_in_2)} 종목 (= 진짜 31위~ 후보)")
        if only_in_2:
            print(f"     예: {sorted(only_in_2)[:10]}")
        if len(only_in_2) == 0 and len(overlap) == len(codes1):
            print("  ❌ 결론: ctx 동봉해도 같은 1~30위 반복 → 페이지네이션 미지원")
        elif len(only_in_2) >= 20:
            print("  ✅ 결론: 진짜 31~60위 반환 → 페이지네이션 가능! "
                  "fetch_volume_rank 에 반복 호출 로직 추가하면 50위 확보")
        else:
            print(f"  ⚠ 결론: 일부만 새 종목 (overlap={len(overlap)}, new={len(only_in_2)}) "
                  f"— ctx 동작이 모호. raw 응답 추가 분석 필요")
    else:
        print("\n===== 2nd call 스킵 — 페이지네이션 hint 없음 =====")
        print("  ctx 키도 없고 tr_cont 도 더 없음. 본 endpoint 자체가 1회 30개 hard cap.")
        print("  → Plan B (KOSPI/KOSDAQ 분리 또는 가격 범위 분할) 로 전환")

    # 응답 본문 일부 dump (FID_BLNG_CLS_CODE 검증용)
    print("\n===== 응답 본문 일부 (1st payload, 처음 600자) =====")
    print(json.dumps(payload1, ensure_ascii=False, indent=2)[:600])

    # Plan B 시도 (페이지네이션 미지원 확정 시)
    if args.plan_b:
        _scenario_market_split(settings, cred, args.blng)
        _scenario_price_split(settings, cred, args.blng)

    return 0


if __name__ == "__main__":
    sys.exit(main())
