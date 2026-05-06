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

import math
from datetime import datetime
from typing import Any

from src.report.formatting import (
    code_block,
    fmt_billion,
    fmt_date,
    fmt_layer_stats,
    fmt_pct,
    fmt_price,
    fmt_sizing_table,
    fmt_time,
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
    rank = c.get("rank", 0)
    themes = c.get("themes", [])
    priority = c.get("priority", "")
    layers = c.get("layers", {})
    sizing_layer = c.get("sizing_layer", "")

    priority_label = {
        "limit_up": "🔴 상한가",
        "high_pull": "🟡 고점풀백",
        "normal": "🟢 +20%↑",
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
    lines.append(f"거래대금:  {fmt_billion(trading_value)}  (현재 {rank}위)")

    # Historical 통계
    lines.append("")
    lines.append("[Historical 갭상 분석 — 1년 lookback]")
    for layer_key, label in [("layer1", "Layer 1 (+20%↑)"), ("layer2", "Layer 2 (상한가)"), ("layer3", "Layer 3 (종가위치 매칭)")]:
        stats = layers.get(layer_key, {})
        is_basis = layer_key == sizing_layer
        lines.append("  " + fmt_layer_stats(stats, label, is_sizing_basis=is_basis))

    layer4_note = layers.get("layer4", {}).get("note", "")
    if layer4_note:
        lines.append(f"  Layer 4: ⚠ {layer4_note}")

    return "\n".join(lines)


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


def build_decision_report(
    leading_themes: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    snapshot_dt: datetime,
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
        "[최종 주도테마]",
    ]

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
        "• NXT 청산 가능 여부: v1 예정",
    ]

    return "\n".join(lines)


def split_messages(report: str, max_len: int = TELEGRAM_MAX_LEN) -> list[str]:
    """텔레그램 4096자 제한 대응 — 종목 블록 단위로 분할.

    Returns:
        보내야 할 메시지 리스트 (보통 1개, 종목 많으면 여러 개).
    """
    if len(report) <= max_len:
        return [report]

    parts = report.split("\n▣ ")
    header = parts[0]
    stock_blocks = ["\n▣ " + p for p in parts[1:]]

    messages = []
    current = header
    for block in stock_blocks:
        if len(current) + len(block) > max_len:
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
