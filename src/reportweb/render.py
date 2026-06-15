"""렌더 헬퍼 — 결정 후보 dict → 템플릿용 카드 뷰, 마크다운 → HTML.

표시 포맷은 텔레그램/레포트와 동일하게 보이도록 src.report.formatting 의 포맷터를
그대로 재사용한다 (회전율·갭상확률 등 단위 일관성).
"""
from __future__ import annotations

from typing import Any

import markdown as _md

from src.report.formatting import fmt_billion, fmt_pct, fmt_price, is_num

# 4-Layer 사람이 읽는 라벨 (sizing_layer → 표시명)
_LAYER_LABELS = {
    "layer1": "L1 전체",
    "layer2": "L2 상한가",
    "layer3": "L3 종가매칭",
    "layer4": "L4 고점매칭",
}


def md_to_html(text: str) -> str:
    """레포트 마크다운 → HTML. 표/코드블록/줄바꿈 보존."""
    return _md.markdown(
        text,
        extensions=["tables", "fenced_code", "sane_lists", "nl2br"],
        output_format="html5",
    )


def _sizing_pct(v: Any) -> str:
    return f"{v * 100:.1f}%" if is_num(v) else "—"


def candidate_card(c: dict[str, Any]) -> dict[str, Any]:
    """결정 후보 1건 → 카드 표시용 dict (요약 + 상세 원본).

    숫자 포맷을 미리 문자열로 만들어 템플릿엔 로직이 없게 한다.
    """
    layers = c.get("layers") or {}
    pick = c.get("sizing_layer")
    lstat = (layers.get(pick) if pick else None) or c.get("sizing_stats") or {}
    p = lstat.get("p")
    n = lstat.get("n")
    avg_gap = lstat.get("avg_gap")

    sizing = c.get("sizing") or {}
    aux = c.get("historical_aux") or {}
    ret = c.get("daily_return")

    return {
        "rank": c.get("rank_in_report") or c.get("rank"),
        "name": c.get("name") or c.get("code"),
        "code": c.get("code"),
        "top3": bool(c.get("is_top3")),
        "limit_up": bool(c.get("is_limit_up")),
        "themes": [t for t in (c.get("themes") or []) if t][:3],
        "themes_more": max(0, len(c.get("themes") or []) - 3),
        # 가격/등락
        "ret_str": fmt_pct(ret) if is_num(ret) else "—",
        "ret_pos": is_num(ret) and ret >= 0,
        "price_str": fmt_price(c.get("price")) if is_num(c.get("price")) else "—",
        # 거래대금/회전율
        "value_str": fmt_billion(c.get("trading_value")) if is_num(c.get("trading_value")) else "—",
        "value_rank": c.get("volume_rank") or c.get("rank"),
        "turnover_str": f"{c.get('turnover'):.2f}" if is_num(c.get("turnover")) else "—",
        "turnover_rank": c.get("turnover_rank"),
        # 갭상 통계 (선택된 사이징 layer)
        "gap_layer": _LAYER_LABELS.get(pick or "", pick or "—"),
        "gap_p": f"{p * 100:.0f}%" if is_num(p) else "N/A",
        "gap_e": fmt_pct(avg_gap) if is_num(avg_gap) else "N/A",
        "gap_n": int(n) if is_num(n) else 0,
        "sample_ok": bool(c.get("sample_sufficient")),
        # 사이징
        "kelly": _sizing_pct(sizing.get("kelly")),
        "sharpe": _sizing_pct(sizing.get("sharpe")),
        "equal": _sizing_pct(sizing.get("equal")),
        "kelly_bucket": _sizing_pct(sizing.get("kelly_bucket")),
        "bucket_name": c.get("sizing_bucket") or "—",
        # 보조 (1년 ret10 → 갭상)
        "aux_n10": aux.get("n_ret10"),
        "aux_gap": aux.get("n_gap_up"),
        "aux_ratio": f"{aux.get('ratio') * 100:.0f}%" if is_num(aux.get("ratio")) else "—",
        "nxt": c.get("nxt_tradable"),
        # 3거래일 거래대금 추이 (억 포맷, 있으면 상세에 표시)
        "trend_value": [
            fmt_billion(pt.get("value"))
            for pt in ((c.get("trends") or {}).get("trading_value") or [])
            if is_num(pt.get("value"))
        ],
    }


def build_decision_context(payload: dict[str, Any]) -> dict[str, Any]:
    """결정 JSON payload → 템플릿 컨텍스트 (헤더 + 카드 리스트)."""
    cands = payload.get("candidates") or []
    cards = [candidate_card(c) for c in cands]
    cards.sort(key=lambda c: (c.get("rank") or 9999))

    market = payload.get("market") or {}
    above = market.get("kospi_above_ma200")
    if above is True:
        regime = {"label": "강세장 (KOSPI > 200MA)", "cls": "bull"}
    elif above is False:
        regime = {"label": "약세장 (KOSPI < 200MA)", "cls": "bear"}
    else:
        regime = None

    return {
        "report_time": payload.get("report_time"),
        "regime": regime,
        "leading_themes": payload.get("leading_themes") or [],
        "cards": cards,
        "n_candidates": len(cards),
    }
