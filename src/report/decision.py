"""결정 레포트 생성기 (14:50) ★.

종배 매매 의사결정에 가장 중요한 레포트.
leading_theme → candidates → historical → sizing 파이프라인의 최종 출력.

입력:
    - leading_themes: identify_leading_themes() 결과
    - candidates: 종배 후보 리스트 (각 후보에 historical + sizing 통계 포함)
    - snapshot_dt: 스냅샷 시각 (datetime)

출력:
    마크다운 문자열 (텔레그램 발송용)
    4096자 초과 시 종목별 블록을 별도 메시지로 분리할 수 있도록
    split_messages() 도 제공.
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime
from pathlib import Path
from typing import Any

from loguru import logger

from src.report.formatting import (
    code_block,
    fmt_billion,
    fmt_date,
    fmt_layer_stats,
    fmt_pct,
    fmt_price,
    fmt_rank,
    fmt_sizing_table,
    fmt_time,
    fmt_volume,
    save_report,
    sep,
)

TELEGRAM_MAX_LEN = 4096


def _candidate_block(c: dict[str, Any]) -> str:
    """종목 1개에 대한 마크다운 블록."""
    code = c.get("code", "")
    name = c.get("name", "")
    price = c.get("price", 0)
    prev_close = c.get("prev_close", 0)
    daily_return = c.get("daily_return", float("nan"))
    intraday_high = c.get("intraday_high", 0)
    intraday_high_pct = c.get("intraday_high_pct", float("nan"))
    trading_value = c.get("trading_value", 0)
    volume = c.get("volume", 0)
    rank = c.get("rank", 0)
    volume_rank = c.get("volume_rank")
    themes = c.get("themes", [])
    priority = c.get("priority", "")
    layers = c.get("layers", {})
    sizing_layer = c.get("sizing_layer", "")

    # R4 v2 (e) round 41 — normal 범위 10~27% (이전 20%↑). limit_up 은 27% 상한
    # 컷에 자동 제외되므로 더는 후보로 안 나오나 backward-compat 위해 라벨 유지.
    priority_label = {
        "limit_up": "🔴 상한가",
        "high_pull": "🟡 고점풀백",
        "normal": "🟢 +10~27%",
    }.get(priority, "")

    lines = [
        f"▣ {name} ({code})  {priority_label}",
        "─" * 33,
    ]

    # 테마
    if themes:
        lines.append(f"테마: {' / '.join(themes)}")

    # 오늘 시그니처
    lines.append("")
    lines.append("[오늘 시그니처]")
    lines.append(f"일봉:      {fmt_pct(daily_return)}  ({fmt_price(prev_close)} → {fmt_price(price)})")
    lines.append(f"일중 고점: {fmt_price(intraday_high)}  ({fmt_pct(intraday_high_pct)})")
    # 거래대금 (KIS 절대 순위) + 거래량 (snapshot universe 내 상대 순위).
    # KIS volume-rank 는 거래대금 절대 순위만 — 거래량 절대 순위 별도 API 필요.
    lines.append(f"거래대금:  {fmt_billion(trading_value)}  (거래대금 {rank}위)")
    lines.append(f"거래량:    {fmt_volume(volume)}  (top50 내 {fmt_rank(volume_rank)})")

    # Historical 통계 + Layer 정의 설명 (라벨에 사례 매칭 조건 명시).
    # 각 Layer 는 동일 종목 1년 일봉에서 유사 사례 추출 → 다음날 갭상 통계.
    # ★ 사이징 기준 Layer 는 fmt_layer_stats 가 'sizing_basis' 라벨 부착.
    lines.append("")
    lines.append("[Historical 갭상 분석 — 1년 lookback]")
    layer_order = [
        ("layer1",            "Layer 1 (ret≥20% 모든 사례)"),
        ("layer2",            "Layer 2 (상한가 ret≥29.5%)"),
        ("layer3",            "Layer 3 (L2 + 종가위치 ±2% 일치)"),
        ("layer3_strong_mkt", "Layer 3 + KOSPI 200ma 위 매칭"),
        ("layer3_high_vol",   "Layer 3 + 거래량비율 ±0.5배 매칭"),
    ]
    for layer_key, label in layer_order:
        if layer_key not in layers:
            continue
        stats = layers[layer_key]
        is_basis = layer_key == sizing_layer
        lines.append("  " + fmt_layer_stats(stats, label, is_sizing_basis=is_basis))

    # Layer 4 — v1 (분봉 적재 후 구현). 사용자 자주 묻는 항목이라 무엇인지 명시.
    layer4_note = layers.get("layer4", {}).get("note", "")
    lines.append(
        f"  Layer 4 (L3 + 고점도달 시각 매칭): ⚠ v1 — 오늘 고점 시각과 유사 사례"
    )
    lines.append(
        f"          분봉 히스토리 적재 누적 후 구현 (현재 v0 미구현)"
    )
    if layer4_note and "분봉" not in layer4_note:
        # 기존 note 가 다른 사유로 채워졌으면 그것도 표시
        lines.append(f"          비고: {layer4_note}")

    # R4 v2 보조 지표 — 1년 ret≥10 횟수 + 갭상 비율 (round 41 ④, 컷 X 표시만)
    aux = c.get("historical_aux") or {}
    n_ret10 = int(aux.get("n_ret10", 0) or 0)
    if n_ret10 > 0:
        n_gap = int(aux.get("n_gap_up", 0) or 0)
        ratio = aux.get("ratio", float("nan"))
        if ratio == ratio:  # not NaN
            lines.append(
                f"  📊 1년 ret≥10: {n_ret10}회 / 갭상 {n_gap}회 ({ratio*100:.0f}%)"
            )
        else:
            lines.append(f"  📊 1년 ret≥10: {n_ret10}회 (갭상 데이터 없음)")

    # R4 v2 (c)(d) 통과 표시
    chk = c.get("r4v2_check") or {}
    chk_parts: list[str] = []
    if chk.get("close_within_10pct_high") is True:
        chk_parts.append("✅ 종가 고가-10% 이내")
    if chk.get("is_52w_high") is True:
        chk_parts.append("✅ 52주 신고가")
    elif chk.get("is_52w_high") is None and chk:
        chk_parts.append("— 52주 신고가 (lookback 부족)")
    if chk_parts:
        lines.append(f"  R4 v2: {' / '.join(chk_parts)}")

    # 14:50 시그널 (표시만, 점수화 X)
    signals = c.get("intraday_signals") or {}
    signal_lines = _intraday_signal_lines(signals)
    if signal_lines:
        lines.append("")
        lines.append("[14:50 시그널]  ※ 표시만 — 사이징 미반영")
        lines.extend(signal_lines)

    return "\n".join(lines)


def _intraday_signal_lines(signals: dict[str, Any]) -> list[str]:
    """후보 종목의 14:50 호가/체결/투자자 시그널 → 사람-읽는 줄들.

    Args:
        signals: {"asking_price": {...}, "ccnl_strength": {...}, "investor_flow": {...}}
                 각 dict는 src/data/intraday_realtime.py 의 fetch_xxx 결과.

    Returns:
        없는 시그널은 줄 생략. 셋 다 비어 있으면 [].
    """
    out: list[str] = []

    ap = signals.get("asking_price") or {}
    if ap:
        bid = ap.get("bid_total_volume", 0)
        ask = ap.get("ask_total_volume", 0)
        ratio = ap.get("bid_ask_ratio")
        # save_decision_candidates 가 NaN→None 으로 직렬화하므로 reload 시 None 가능
        ratio_ok = isinstance(ratio, (int, float)) and ratio == ratio and ratio > 0
        ratio_str = f"{ratio:.1f}배" if ratio_ok else "—"
        tag = ""
        if ratio_ok:
            if ratio >= 3.0:
                tag = "  🟢 매수 우세"
            elif ratio <= 0.5:
                tag = "  🔴 매도 우세"
        out.append(f"  호가:  매수 {bid:,}주 / 매도 {ask:,}주  (bid/ask {ratio_str}){tag}")

    cs = signals.get("ccnl_strength") or {}
    if cs:
        strength = cs.get("ccnl_strength")
        strength_ok = isinstance(strength, (int, float)) and strength == strength
        s_str = f"{strength:.0f}" if strength_ok else "—"
        tag = ""
        if strength_ok:
            if strength >= 120:
                tag = "  🟢 매수 우세"
            elif strength <= 80:
                tag = "  🔴 매도 우세"
        out.append(f"  체결:  체결강도 {s_str}{tag}")

    inv = signals.get("investor_flow") or {}
    if inv:
        fv = inv.get("foreign_net_buy_value", 0)
        iv = inv.get("institution_net_buy_value", 0)
        pq = inv.get("program_net_buy", 0)

        # round 36: 양수에 + 부호 명시 (음수는 fmt_billion 자체 처리). 프로그램은
        # KIS 응답에 금액 필드가 없어 수량(만주) 단위로 표시.
        def _signed_bil(v: int) -> str:
            if v == 0:
                return "0"
            sign = "+" if v > 0 else ""
            return f"{sign}{fmt_billion(v)}"

        program_str = ""
        if pq:
            sign = "+" if pq > 0 else "-"
            mag = abs(pq)
            if mag >= 1e4:
                program_str = f" / 프로그램 {sign}{mag / 1e4:,.0f}만주"
            else:
                program_str = f" / 프로그램 {sign}{int(mag):,}주"
        out.append(
            f"  수급:  외국인 {_signed_bil(fv)} / 기관 {_signed_bil(iv)}{program_str}"
        )

    return out


def _sizing_block(candidates: list[dict[str, Any]]) -> str:
    """사이징 제안 블록."""
    table_rows = []
    for c in candidates:
        stats = c.get("sizing_stats", {})
        sizing = c.get("sizing", {})
        table_rows.append({
            "name": c.get("name", ""),
            "p_gap": stats.get("p", float("nan")),
            "avg_gap": stats.get("avg_gap", float("nan")),
            "kelly": sizing.get("kelly"),
            "sharpe": sizing.get("sharpe"),
            "equal": sizing.get("equal", 0.0),
        })

    lines = [
        sep(),
        "[사이징 제안]",
        sep(),
        code_block(fmt_sizing_table(table_rows)),
        "* Kelly: 표본 보정 적용 (n<5 제외, n<20 ×0.6, n≥20 ×0.8), 25% 캡",
        "* 실제 사이징은 Zeta 판단",
    ]
    return "\n".join(lines)


def _market_regime_line(market_stats: dict[str, Any]) -> list[str]:
    """[시장 국면] 섹션 — KOSPI 현재가/200ma 위아래/60일 수익률 한 줄 요약.

    강세장 가정 무너지면 모든 종배 룰이 무효라 결정 레포트 최상단에 배치.
    market_stats 가 비어 있으면 빈 리스트 반환 (섹션 자체 생략).
    """
    if not market_stats:
        return []
    parts: list[str] = []
    if "kospi_current" in market_stats:
        cr = market_stats.get("kospi_change_rate", float("nan"))
        cr_str = fmt_pct(cr) if cr == cr else "—"
        parts.append(f"KOSPI {market_stats['kospi_current']:.2f} ({cr_str})")
    if "kospi_above_ma200" in market_stats:
        parts.append("200ma 위 ✅" if market_stats["kospi_above_ma200"] else "200ma 아래 ⚠")
    if "kospi_60d_return" in market_stats:
        parts.append(f"60일 {fmt_pct(market_stats['kospi_60d_return'])}")
    if not parts:
        return []

    lines = ["[시장 국면]", "  " + "  |  ".join(parts)]
    if market_stats.get("kospi_above_ma200") is False:
        lines.append("  ⚠ 강세장 가정 무너짐 — 룰 신뢰도 저하, 후보 사이징 보수적으로")
    lines.append("")
    return lines


def build_decision_report(
    leading_themes: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    snapshot_dt: datetime,
    market_stats: dict[str, Any] | None = None,
) -> str:
    """결정 레포트 마크다운 생성.

    Args:
        leading_themes: identify_leading_themes() 결과.
        candidates: accepted_candidates() 결과 + 각 후보에 아래 키 추가 필요:
            - themes: list[str] (네이버 테마)
            - layers: dict (historical_4layer 결과)
            - sizing_layer: str ("layer3" 등)
            - sizing_stats: dict (pick_sizing_layer 결과 stats)
            - sizing: {"kelly": float|None, "sharpe": float, "equal": float}
        snapshot_dt: 스냅샷 수집 시각 (KST datetime).
        market_stats: compute_market_stats() 결과. None/빈 dict 시 섹션 생략.

    Returns:
        마크다운 문자열.
    """
    d = snapshot_dt.date()
    t = fmt_time(snapshot_dt)
    accepted = [c for c in candidates if c.get("priority") != "excluded"]

    lines = [
        f"🎯 [결정-{t}] {fmt_date(d)}",
        sep(),
        "",
    ]
    lines += _market_regime_line(market_stats or {})
    lines.append("[최종 주도테마]")

    if leading_themes:
        for lt in leading_themes:
            codes_preview = ", ".join(lt["codes"][:3])
            suffix = " ..." if len(lt["codes"]) > 3 else ""
            lines.append(f"• {lt['theme']} ({lt['count']}): {codes_preview}{suffix}")
    else:
        lines.append("• 주도테마 없음 (임계값 미달)")

    lines += ["", sep(), f"[종배 후보 — {len(accepted)}종목]", sep(), ""]

    if not accepted:
        lines.append("⚠ 조건 만족 후보 없음")
    else:
        for c in accepted:
            lines.append(_candidate_block(c))
            lines.append("")

    if accepted:
        lines.append(_sizing_block(accepted))

    lines += [
        "",
        sep("─"),
        "• 본 레포트는 14:50 기준. 종가 직전 상황 변동 가능",
        "• R4 v2 룰: (a) 거래대금 50위 + (b) 일봉상승 + (c) 종가 고가-10% 이내 "
        "+ (d) 52주 신고가 + (e) 10≤ret≤27% + (f) Layer 표본≥5",
        "• NXT 청산 가능 여부: v1 예정",
    ]

    return "\n".join(lines)


def split_messages(report: str, max_len: int = TELEGRAM_MAX_LEN) -> list[str]:
    """텔레그램 4096자 제한 대응 — 종목 블록 + 사이징 블록 atomic split.

    종목 블록(`▣` 시작)과 사이징 블록(`[사이징 제안]` 시작)은 메시지 중간에서
    잘리지 않고 통째로 한 메시지로 유지된다. 동급 atomic 들이 합쳐서 max_len 을
    넘으면 새 메시지로 분리. 단독 atomic 이 max_len 초과면 경고 로그 + 그대로
    발송 (텔레그램이 거부할 수 있음 — 발생 시 _candidate_block 의 보조 지표
    일부 생략으로 회피).

    Returns:
        보내야 할 메시지 리스트 (보통 1개, 종목 많으면 여러 개).
    """
    if len(report) <= max_len:
        return [report]

    # 사이징 블록 atomic 분리 — "[사이징 제안]" 직전 separator 줄부터 끝까지
    sizing_anchor = report.find("[사이징 제안]")
    if sizing_anchor > 0:
        sep_pos = report.rfind("\n", 0, sizing_anchor)
        before_sizing = report[:sep_pos] if sep_pos > 0 else report
        sizing_block = report[sep_pos:] if sep_pos > 0 else ""
    else:
        before_sizing = report
        sizing_block = ""

    parts = before_sizing.split("\n▣ ")
    header = parts[0]
    stock_blocks = ["\n▣ " + p for p in parts[1:]]
    atomics = stock_blocks + ([sizing_block] if sizing_block else [])

    messages: list[str] = []
    current = header
    for block in atomics:
        if len(block) > max_len:
            logger.warning(
                f"[split_messages] atomic block {len(block)}자 > max_len {max_len}자 — "
                f"한 메시지로 발송 (텔레그램 거부 가능성). _candidate_block 보조 지표 "
                f"축소 필요. prefix: {block[:80]!r}"
            )
        if current and len(current) + len(block) > max_len:
            messages.append(current)
            current = block
        else:
            current += block
    if current:
        messages.append(current)
    return messages


def save_decision_report(text: str, data_dir, dt: datetime) -> None:
    """결정 레포트를 파일로 저장."""
    save_report(text, data_dir, dt, "decision")


def _to_serializable(obj: Any) -> Any:
    """numpy/pandas/날짜 스칼라를 JSON 가능한 native 타입으로 변환. NaN→None."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return {str(k): _to_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_to_serializable(v) for v in obj]
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, float):
        if obj != obj or obj in (float("inf"), float("-inf")):
            return None
        return obj
    if isinstance(obj, int):
        return obj
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if hasattr(obj, "item") and not isinstance(obj, str):
        try:
            return _to_serializable(obj.item())
        except (ValueError, AttributeError):
            pass
    return str(obj) if not isinstance(obj, str) else obj


def _decision_candidates_path(data_dir, d: date) -> Path:
    return Path(data_dir) / "decisions" / f"{d.strftime('%Y-%m-%d')}.json"


def save_decision_candidates(
    candidates: list[dict[str, Any]],
    data_dir,
    dt: datetime,
) -> Path:
    """결정 레포트의 후보 리스트를 JSON으로 영속화 (사후 레포트가 16:00에 재로딩).

    경로: {DATA_DIR}/decisions/YYYY-MM-DD.json
    """
    path = _decision_candidates_path(data_dir, dt.date())
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "report_date": dt.date().isoformat(),
        "report_time": dt.strftime("%H:%M:%S"),
        "candidates": [_to_serializable(c) for c in candidates],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_decision_candidates(data_dir, d: date) -> list[dict[str, Any]]:
    """저장된 결정 후보 리스트를 읽어 반환. 파일 없으면 빈 리스트."""
    path = _decision_candidates_path(data_dir, d)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return payload.get("candidates", []) or []
