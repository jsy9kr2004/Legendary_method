"""정기 추적 레포트 생성기 (11:00 / 13:00 / 14:00).

목적: 주도테마 변화 감지 + 신규 상한가 알림.
길이: 짧게 (텔레그램 1메시지).

또한 09:00~09:30 장 초반 고주파 모니터링용 알림도 여기서 생성.
장초반은 주도섹터/주도주 변화에 엄청 민감하게 반응해야 하므로
30초~1분 간격, 변화 있을 때만 발송.

변화 감지 기준:
    - 신규 주도테마 진입 (이전 스냅샷에 없던 테마가 ≥3개로 올라옴)
    - 기존 주도테마 탈락
    - 신규 상한가 진입 종목 (detect_new_limit_up 에서 관리)
    - 상위 5위 안에 신규 진입 종목
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from src.report.formatting import (
    fmt_billion,
    fmt_date,
    fmt_pct,
    fmt_price,
    fmt_time,
    save_report,
    sep,
)


def build_periodic_report(
    snapshot_df,
    leading_themes: list[dict[str, Any]],
    prev_leading_themes: list[dict[str, Any]],
    new_limit_up: list[dict[str, Any]],
    snapshot_dt: datetime,
    top_n: int = 10,
) -> str:
    """정기 추적 레포트 마크다운 생성.

    Args:
        snapshot_df: 거래대금 순위 스냅샷 DataFrame.
        leading_themes: 현재 주도테마 리스트.
        prev_leading_themes: 직전 스냅샷의 주도테마 리스트 (변화 감지용).
        new_limit_up: 직전 추적 이후 신규 상한가 진입 종목 리스트.
        snapshot_dt: 스냅샷 시각.
        top_n: 거래대금 상위 표시 종목 수.

    Returns:
        마크다운 문자열.
    """
    t = fmt_time(snapshot_dt)
    d = snapshot_dt.date()

    lines = [
        f"📊 [추적-{t}] {fmt_date(d)}",
        sep(),
        "",
        f"[거래대금 상위 {top_n}위]",
    ]

    if snapshot_df is not None and not snapshot_df.empty:
        top = snapshot_df.sort_values("rank").head(top_n)
        for _, row in top.iterrows():
            lup_mark = " 🔴" if row.get("is_limit_up") else ""
            lines.append(
                f"{int(row['rank']):>2}위  {str(row.get('name','')):<10}  "
                f"{fmt_pct(float(row.get('daily_return', 0)))}  "
                f"{fmt_billion(int(row.get('trading_value', 0)))}{lup_mark}"
            )
    else:
        lines.append("(데이터 없음)")

    # 주도테마
    lines += ["", "[주도테마 (≥3종목)]"]
    if leading_themes:
        prev_theme_names = {t["theme"] for t in prev_leading_themes}
        for lt in leading_themes:
            new_mark = " 🆕" if lt["theme"] not in prev_theme_names else ""
            codes_str = ", ".join(lt["codes"][:3])
            suffix = " ..." if len(lt["codes"]) > 3 else ""
            lines.append(f"• {lt['theme']} ({lt['count']}){new_mark}: {codes_str}{suffix}")
    else:
        lines.append("• 주도테마 없음")

    # 테마 변화 요약
    theme_changes = _detect_theme_changes(leading_themes, prev_leading_themes)
    if theme_changes:
        lines += ["", "[테마 변화]"]
        lines.extend(theme_changes)

    # 신규 상한가
    if new_limit_up:
        lines += ["", "[신규 상한가]"]
        for q in new_limit_up:
            lines.append(
                f"• {q.get('name','')} ({q.get('code','')})  "
                f"{fmt_pct(float(q.get('daily_return', 0)))}  "
                f"→ 상세: 14:50 결정 레포트"
            )

    return "\n".join(lines)


def _detect_theme_changes(
    current: list[dict[str, Any]],
    prev: list[dict[str, Any]],
) -> list[str]:
    """주도테마 변화 감지 → 요약 문자열 리스트."""
    prev_names = {t["theme"] for t in prev}
    curr_names = {t["theme"] for t in current}
    new_in = curr_names - prev_names
    dropped = prev_names - curr_names
    lines = []
    for name in sorted(new_in):
        lines.append(f"  🆕 신규 진입: {name}")
    for name in sorted(dropped):
        lines.append(f"  🔻 탈락: {name}")
    return lines


def build_early_morning_alert(
    snapshot_df,
    leading_themes: list[dict[str, Any]],
    prev_leading_themes: list[dict[str, Any]],
    leading_stocks: list[dict[str, Any]],
    prev_leading_stocks: list[dict[str, Any]],
    snapshot_dt: datetime,
) -> str | None:
    """09:00~10:00 장 초반 변화 감지 알림 생성.

    변화가 없으면 None 반환 → 호출부에서 발송 안 함.

    감지 기준 (사용자 명시):
        1. 주도섹터(테마) 변화 — 신규 진입/탈락
        2. 주도주(주도테마 내 first-mover 상한가 종목) 변화

    비주도테마 상한가는 별도 limit_up 폴링이 알림.

    Returns:
        마크다운 문자열 또는 None (변화 없음).
    """
    t = fmt_time(snapshot_dt)
    changes: list[str] = []

    # 주도테마 변화
    theme_changes = _detect_theme_changes(leading_themes, prev_leading_themes)
    changes.extend(theme_changes)

    # 주도주 변화
    prev_leader_codes = {s["code"] for s in prev_leading_stocks}
    curr_leader_codes = {s["code"] for s in leading_stocks}
    new_leaders = [s for s in leading_stocks if s["code"] not in prev_leader_codes]
    dropped_leaders = [s for s in prev_leading_stocks if s["code"] not in curr_leader_codes]

    for s in new_leaders:
        changes.append(
            f"  ⭐ 주도주 진입: {s['name']} ({s['code']}) — {s['theme']}  "
            f"{fmt_pct(s['daily_return'])}"
        )
    for s in dropped_leaders:
        changes.append(
            f"  💤 주도주 이탈: {s['name']} ({s['code']}) — {s['theme']}"
        )

    if not changes:
        return None

    lines = [f"⚡ [장초반-{t}]"] + changes

    if leading_themes:
        top = leading_themes[0]
        lines.append(f"현재 주도테마: {top['theme']} ({top['count']}종목)")
    if leading_stocks:
        top_stock = leading_stocks[0]
        lines.append(
            f"대표 주도주: {top_stock['name']} ({top_stock['theme']})"
        )

    return "\n".join(lines)


def has_significant_change(
    current_themes: list[dict[str, Any]],
    prev_themes: list[dict[str, Any]],
    new_limit_up: list[dict[str, Any]],
) -> bool:
    """유의미한 변화가 있는지 여부 (알림 발송 여부 결정용)."""
    if new_limit_up:
        return True
    prev_names = {t["theme"] for t in prev_themes}
    curr_names = {t["theme"] for t in current_themes}
    return prev_names != curr_names


def save_periodic_report(text: str, data_dir, dt: datetime) -> None:
    """정기 추적 레포트를 파일로 저장."""
    save_report(text, data_dir, dt, "periodic")
