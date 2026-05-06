"""모닝 레포트 생성기 (09:30).

목적: 시장 국면 판단 + 어제 보유 종목 갭 결과 확인.

입력:
    - market_stats: KOSPI 지표 dict (별도 API 호출, 없으면 N/A)
    - holdings: 어제 매수한 종목 결과 리스트 (Zeta 직접 입력)
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from src.report.formatting import (
    fmt_date,
    fmt_pct,
    fmt_price,
    fmt_time,
    save_report,
    sep,
)


def build_morning_report(
    market_stats: dict[str, Any],
    holdings: list[dict[str, Any]],
    report_dt: datetime,
) -> str:
    """모닝 레포트 마크다운 생성.

    Args:
        market_stats: {
            "kospi_current": float,       # KOSPI 시초가
            "kospi_prev_close": float,    # KOSPI 전일종가
            "kospi_ma200": float,         # KOSPI 200일 이평
            "kospi_60d_return": float,    # 60일 수익률(%)
            "vkospi": float,              # 변동성지수
            "bear_ratio_20d": float,      # 20일 음봉 비율(%)
        }
        holdings: [{"name": str, "code": str, "buy_price": int, "open_price": int}, ...]
        report_dt: 레포트 생성 시각 (KST).

    Returns:
        마크다운 문자열.
    """
    t = fmt_time(report_dt)
    d = report_dt.date()

    def _val(key: str, fmt_fn=None, fallback: str = "N/A") -> str:
        v = market_stats.get(key)
        if v is None or (isinstance(v, float) and v != v):
            return fallback
        return fmt_fn(v) if fmt_fn else str(v)

    kospi = market_stats.get("kospi_current")
    kospi_prev = market_stats.get("kospi_prev_close")
    ma200 = market_stats.get("kospi_ma200")

    if kospi and kospi_prev and kospi_prev > 0:
        kospi_chg = (kospi - kospi_prev) / kospi_prev * 100
        kospi_str = f"{kospi:,.2f}  ({fmt_pct(kospi_chg)})"
    else:
        kospi_str = "N/A"

    if kospi and ma200 and ma200 > 0:
        ma200_pos = "위 ✅" if kospi > ma200 else "아래 ⚠"
        ma200_str = f"{ma200:,.2f}  →  시초 {ma200_pos}"
    else:
        ma200_str = "N/A"

    lines = [
        f"📊 [모닝] {fmt_date(d)}",
        sep(),
        "",
        "[시장 국면 지표]",
        f"KOSPI 시초:    {kospi_str}",
        f"200일 이평:    {ma200_str}",
        f"60일 수익률:   {_val('kospi_60d_return', fmt_pct)}",
        f"VKOSPI:        {_val('vkospi')}",
        f"20일 음봉비율: {_val('bear_ratio_20d', lambda v: f'{v:.0f}%')}",
        "",
        "→ 대세상승장 판단: [Zeta 직관]",
    ]

    if holdings:
        lines += ["", sep("─"), "[보유 종목 갭 결과]"]
        for h in holdings:
            name = h.get("name", "")
            code = h.get("code", "")
            buy = h.get("buy_price", 0)
            open_p = h.get("open_price")
            if buy and open_p:
                gap = (open_p - buy) / buy * 100
                action = "→ 시초 익절 권장" if gap > 0 else "→ 손절 고려"
                lines.append(
                    f"{name} ({code})  매수가 {fmt_price(buy)} → 시가 {fmt_price(open_p)}"
                    f"  ({fmt_pct(gap)})  {action}"
                )
            else:
                lines.append(f"{name} ({code})  매수가 {fmt_price(buy)} → 시가 미확인")
    else:
        lines += ["", "보유 종목 없음 (또는 미입력)"]

    return "\n".join(lines)


def save_morning_report(text: str, data_dir, dt: datetime) -> None:
    save_report(text, data_dir, dt, "morning")
