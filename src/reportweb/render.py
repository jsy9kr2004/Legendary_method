"""렌더 헬퍼 — 결정 후보 dict → 템플릿용 카드 뷰, 마크다운 → HTML.

표시 포맷은 텔레그램/레포트와 동일하게 보이도록 src.report.formatting 의 포맷터를
그대로 재사용한다 (회전율·갭상확률 등 단위 일관성).

카드 = 최소 요약(순위/종목명/코드/등락폭/거래대금+순위) + 상세 보기(원본 수준 전체).
"""
from __future__ import annotations

import ast
from typing import Any

import markdown as _md

from src.report.formatting import (
    fmt_billion,
    fmt_market_cap,
    fmt_pct,
    fmt_price,
    fmt_volume,
    is_num,
)

_LAYER_LABELS = {
    "layer1": "L1 전체",
    "layer2": "L2 상한가",
    "layer3": "L3 종가매칭",
    "layer3_high_vol": "L3+ 종가·거래량",
    "layer4": "L4 고점매칭",
}
_PERIODS = [("month", "1달"), ("3month", "3달"), ("6month", "6달"), ("year", "1년")]
_THS = [(0, "≥0%"), (10, "≥10%"), (20, "≥20%")]


def md_to_html(text: str) -> str:
    """레포트 마크다운 → HTML. 표/코드블록/줄바꿈 보존."""
    return _md.markdown(
        text,
        extensions=["tables", "fenced_code", "sane_lists", "nl2br"],
        output_format="html5",
    )


def _pct_frac(v: Any) -> str:
    """0~1 비율 → 백분율 (0.67 → 67%)."""
    return f"{v * 100:.0f}%" if is_num(v) else "—"


def _sizing_pct(v: Any) -> str:
    return f"{v * 100:.1f}%" if is_num(v) else "—"


def _signed_shares(v: Any) -> str:
    """순매수 주식수 부호 포함 (43000 → +43,000주)."""
    if not is_num(v):
        return "—"
    return f"{int(v):+,}주"


def _layers_view(c: dict[str, Any]) -> list[dict[str, Any]]:
    layers = c.get("layers") or {}
    pick = c.get("sizing_layer")
    out = []
    for key, label in _LAYER_LABELS.items():
        st = layers.get(key)
        if not st:
            continue
        out.append({
            "label": label,
            "picked": key == pick,
            "n": int(st["n"]) if is_num(st.get("n")) else 0,
            "p": _pct_frac(st.get("p")),
            "gap": fmt_pct(st["avg_gap"]) if is_num(st.get("avg_gap")) else "—",
            "median": fmt_pct(st["median_gap"]) if is_num(st.get("median_gap")) else "—",
        })
    return out


def _matrix_view(c: dict[str, Any]) -> dict[str, Any] | None:
    """historical_aux_matrix (기간×상승률임계) → 표 구조."""
    raw = c.get("historical_aux_matrix") or {}
    if not raw:
        return None
    cells: dict[tuple, dict] = {}
    for k, v in raw.items():
        try:
            period, th = ast.literal_eval(k)
            cells[(str(period), int(th))] = v
        except (ValueError, SyntaxError):
            continue
    rows = []
    for pkey, plabel in _PERIODS:
        cols = []
        for th, _ in _THS:
            cell = cells.get((pkey, th)) or {}
            ratio = cell.get("ratio")
            n = cell.get("n")
            cols.append({
                "ratio": _pct_frac(ratio) if is_num(ratio) else "—",
                "n": int(n) if is_num(n) else 0,
            })
        rows.append({"label": plabel, "cols": cols})
    return {"th_labels": [lbl for _, lbl in _THS], "rows": rows}


def _trends_view(c: dict[str, Any]) -> dict[str, Any]:
    t = c.get("trends") or {}
    val = [
        {"date": x.get("date"), "v": fmt_billion(x.get("value")) if is_num(x.get("value")) else "—",
         "rank": x.get("rank")}
        for x in (t.get("trading_value") or [])
    ]
    turn = [
        {"date": x.get("date"), "v": f"{x.get('value'):.2f}" if is_num(x.get("value")) else "—",
         "rank": x.get("rank")}
        for x in (t.get("turnover") or [])
    ]
    sup = [
        {"date": x.get("date"),
         "f": _signed_shares(x.get("foreign")),
         "i": _signed_shares(x.get("institution")),
         "p": _signed_shares(x.get("program"))}
        for x in (t.get("supply") or [])
    ]
    return {"value": val, "turnover": turn, "supply": sup}


def _intraday_view(c: dict[str, Any]) -> dict[str, Any]:
    sig = c.get("intraday_signals") or {}
    out: dict[str, Any] = {}
    ap = sig.get("asking_price")
    if ap:
        out["asking"] = {
            "ratio": f"{ap['bid_ask_ratio']:.2f}" if is_num(ap.get("bid_ask_ratio")) else "—",
            "bid1": f"{fmt_price(ap.get('bid1_price'))} × {ap.get('bid1_volume')}",
            "ask1": f"{fmt_price(ap.get('ask1_price'))} × {ap.get('ask1_volume')}",
            "bid_total": ap.get("bid_total_volume"),
            "ask_total": ap.get("ask_total_volume"),
        }
    cs = sig.get("ccnl_strength")
    if cs:
        out["ccnl"] = {
            "strength": f"{cs['ccnl_strength']:.1f}" if is_num(cs.get("ccnl_strength")) else "—",
            "buy_ratio": _pct_frac(cs.get("buy_ratio")),
        }
    inv = sig.get("investor_flow")
    if inv:
        out["investor"] = {
            "foreign": _signed_shares(inv.get("foreign_net_buy")),
            "foreign_val": fmt_billion(inv.get("foreign_net_buy_value")) if is_num(inv.get("foreign_net_buy_value")) else None,
            "inst": _signed_shares(inv.get("institution_net_buy")),
            "inst_val": fmt_billion(inv.get("institution_net_buy_value")) if is_num(inv.get("institution_net_buy_value")) else None,
            "prog": _signed_shares(inv.get("program_net_buy")),
            "prog_val": fmt_billion(inv.get("program_net_buy_value")) if is_num(inv.get("program_net_buy_value")) else None,
        }
    avg = sig.get("investor_nday_avg")
    if avg:
        out["investor_avg"] = {
            "n_days": avg.get("n_days"),
            "foreign": _signed_shares(avg.get("foreign_net_buy_avg")),
            "inst": _signed_shares(avg.get("institution_net_buy_avg")),
            "prog": _signed_shares(avg.get("program_net_buy_avg")),
        }
    return out


def candidate_card(c: dict[str, Any]) -> dict[str, Any]:
    """결정 후보 1건 → 카드 뷰 (요약 5필드 + 상세 d)."""
    ret = c.get("daily_return")
    aux = c.get("historical_aux") or {}
    candle = c.get("candle_aux") or {}
    r4 = c.get("r4v2_check") or {}
    sizing = c.get("sizing") or {}
    high = c.get("intraday_high")

    detail = {
        # 가격/위치
        "price_str": fmt_price(c.get("price")) if is_num(c.get("price")) else "—",
        "prev_str": fmt_price(c.get("prev_close")) if is_num(c.get("prev_close")) else "—",
        "high_str": fmt_price(high) if is_num(high) else "—",
        "high_pct": fmt_pct(c["intraday_high_pct"]) if is_num(c.get("intraday_high_pct")) else None,
        "low_str": fmt_price(c.get("intraday_low")) if is_num(c.get("intraday_low")) else "—",
        "mcap_str": fmt_market_cap(c.get("market_cap")) if is_num(c.get("market_cap")) else "—",
        # 거래
        "volume_str": fmt_volume(c.get("volume")) if is_num(c.get("volume")) else "—",
        "volume_rank": c.get("volume_rank"),
        "turnover_str": f"{c.get('turnover'):.2f}" if is_num(c.get("turnover")) else "—",
        "turnover_rank": c.get("turnover_rank"),
        # 테마
        "themes": [t for t in (c.get("themes") or []) if t],
        # 갭상 4-Layer + 매트릭스
        "layers": _layers_view(c),
        "matrix": _matrix_view(c),
        # 사이징
        "kelly": _sizing_pct(sizing.get("kelly")),
        "sharpe": _sizing_pct(sizing.get("sharpe")),
        "equal": _sizing_pct(sizing.get("equal")),
        "kelly_bucket": _sizing_pct(sizing.get("kelly_bucket")),
        "kelly_bucket_rel": _sizing_pct(sizing.get("kelly_bucket_rel")),
        "bucket_name": c.get("sizing_bucket") or "—",
        "sizing_layer": _LAYER_LABELS.get(c.get("sizing_layer") or "", c.get("sizing_layer") or "—"),
        "sample_ok": bool(c.get("sample_sufficient")),
        # 보조 통계
        "aux_ratio": _pct_frac(aux.get("ratio")),
        "aux_n10": aux.get("n_ret10"),
        "aux_gap": aux.get("n_gap_up"),
        "candle": {
            "consec_up": candle.get("consec_up_days"),
            "big_count": candle.get("big_candle_count"),
            "big_th": candle.get("big_threshold"),
            "nth_big": candle.get("today_is_nth_big"),
        } if candle else None,
        # 장중 시그널
        **_intraday_view(c),
        # 3일 추이
        "trends": _trends_view(c),
        # 기타
        "nxt": c.get("nxt_tradable"),
        "within10": r4.get("close_within_10pct_high"),
        "is_52w": r4.get("is_52w_high"),
    }

    return {
        "rank": c.get("rank_in_report") or c.get("rank"),
        "name": c.get("name") or c.get("code"),
        "code": c.get("code"),
        "top3": bool(c.get("is_top3")),   # 노란 테두리용 (배지 X)
        "limit_up": bool(c.get("is_limit_up")),
        "ret_str": fmt_pct(ret) if is_num(ret) else "—",
        "ret_pos": is_num(ret) and ret >= 0,
        "value_str": fmt_billion(c.get("trading_value")) if is_num(c.get("trading_value")) else "—",
        "value_rank": c.get("rank") or c.get("volume_rank"),
        "d": detail,
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
