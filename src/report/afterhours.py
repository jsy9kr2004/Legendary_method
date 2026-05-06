"""사후 레포트 생성기 (16:00, 이메일).

목적: 오늘 시그널 리뷰 + 시간외 단일가 흐름 + 데이터 적재 상태 확인.
길이: 제한 없음 (이메일 발송).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from src.report.formatting import (
    fmt_billion,
    fmt_date,
    fmt_pct,
    fmt_price,
    save_report,
    sep,
)


def build_afterhours_report(
    candidates: list[dict[str, Any]],
    afterhours_quotes: list[dict[str, Any]],
    data_status: dict[str, Any],
    report_dt: datetime,
) -> str:
    """사후 레포트 마크다운 생성.

    Args:
        candidates: 오늘 종배 후보 리스트 (결정 레포트와 동일 포맷).
        afterhours_quotes: 시간외 단일가 현황 [{code, name, price, prev_close, change_pct}, ...]
        data_status: {
            "ohlcv_updated": bool,
            "ohlcv_count": int,
            "snapshots_collected": int,  # 0~4
            "errors": list[str],
        }
        report_dt: 레포트 생성 시각 (KST).

    Returns:
        마크다운 문자열.
    """
    d = report_dt.date()
    accepted = [c for c in candidates if c.get("priority") != "excluded"]

    lines = [
        f"📝 [사후] {fmt_date(d)} 종배 일일 리뷰",
        sep(),
        "",
    ]

    # 오늘의 종배 후보 요약
    lines.append(f"[오늘 종배 후보 ({len(accepted)}종목)]")
    if accepted:
        for c in accepted:
            priority_label = {"limit_up": "상한가", "high_pull": "고점풀백", "normal": "일반"}.get(
                c.get("priority", ""), c.get("priority", "")
            )
            stats = c.get("sizing_stats", {})
            p = stats.get("p", float("nan"))
            avg = stats.get("avg_gap", float("nan"))
            p_str = f"P={p*100:.0f}%" if p == p else "P=N/A"
            avg_str = f"E[갭]={fmt_pct(avg)}" if avg == avg else "E[갭]=N/A"
            lines.append(
                f"  • {c.get('name','')} ({c.get('code','')})  [{priority_label}]  "
                f"{fmt_pct(float(c.get('daily_return', 0)))}  {p_str}  {avg_str}"
            )
    else:
        lines.append("  오늘 종배 후보 없음")

    # 시간외 단일가
    lines += ["", sep("─"), "[시간외 단일가 (16:00~)]"]
    if afterhours_quotes:
        for q in afterhours_quotes:
            chg = q.get("change_pct", float("nan"))
            direction = "→ 갭상 예고 ✅" if (chg == chg and chg > 0) else ("→ 주의 ⚠" if (chg == chg and chg < 0) else "")
            lines.append(
                f"  {q.get('name','')} ({q.get('code','')})  "
                f"{fmt_price(q.get('price', 0))}  ({fmt_pct(chg)})  {direction}"
            )
    else:
        lines.append("  시간외 데이터 없음 (16:00 이후 갱신)")

    # 데이터 적재 상태
    lines += ["", sep("─"), "[데이터 적재 상태]"]
    ohlcv_ok = data_status.get("ohlcv_updated", False)
    ohlcv_cnt = data_status.get("ohlcv_count", 0)
    snaps = data_status.get("snapshots_collected", 0)
    errors = data_status.get("errors", [])

    lines.append(f"  {'✅' if ohlcv_ok else '❌'} 일봉 OHLCV 갱신  ({ohlcv_cnt}종목)")
    lines.append(f"  {'✅' if snaps == 4 else '⚠'} 장중 스냅샷 {snaps}/4회 수집")

    if errors:
        lines += ["", "[알려진 이슈]"]
        for e in errors:
            lines.append(f"  ⚠ {e}")
    else:
        lines.append("  ✅ 오류 없음")

    lines += [
        "",
        sep("─"),
        "내일 09:30 모닝 레포트 자동 발송 예정",
    ]

    return "\n".join(lines)


def save_afterhours_report(text: str, data_dir, dt: datetime) -> None:
    save_report(text, data_dir, dt, "afterhours")
