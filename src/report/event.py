"""상한가 이벤트 알림 생성기 ★.

박민준 패턴: 상한가 진입 직후 매수.
→ 진입 즉시 텔레그램 푸시. 짧고 핵심만.

텔레그램 lock screen에서 즉시 인지 가능해야 함.
길이 목표: 300자 이내.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from src.report.formatting import (
    fmt_billion,
    fmt_pct,
    fmt_price,
    fmt_time,
    save_report,
)


def build_limit_up_alert(
    code: str,
    name: str,
    price: int,
    prev_close: int,
    daily_return: float,
    trading_value: int,
    rank: int,
    themes: list[str],
    layer2_stats: dict[str, Any],
    detected_at: datetime,
) -> str:
    """상한가 진입 이벤트 알림 마크다운 생성.

    Args:
        code: 종목코드
        name: 종목명
        price: 현재가 (상한가)
        prev_close: 전일종가
        daily_return: 당일 수익률(%)
        trading_value: 누적 거래대금
        rank: 거래대금 순위
        themes: 네이버 테마 리스트
        layer2_stats: historical Layer 2 통계 (상한가 사례)
        detected_at: 감지 시각 (KST)

    Returns:
        마크다운 문자열 (~300자).
    """
    t = fmt_time(detected_at)
    themes_str = " / ".join(themes[:3]) if themes else "테마 정보 없음"

    n = layer2_stats.get("n", 0)
    p = layer2_stats.get("p", float("nan"))
    avg_gap = layer2_stats.get("avg_gap", float("nan"))

    if n > 0 and p == p and avg_gap == avg_gap:
        hist_line = f"Historical(L2): n={n}  P={p*100:.0f}%  E[갭]={fmt_pct(avg_gap)}"
    else:
        hist_line = "Historical: 사례 부족 (데이터 적재 중)"

    lines = [
        f"🚨 [상한가] {t}",
        f"{name} ({code})",
        f"테마: {themes_str}",
        "",
        f"상한가: {fmt_price(price)}  ({fmt_pct(daily_return)})",
        f"거래대금: {fmt_billion(trading_value)}  ({rank}위)",
        "",
        hist_line,
        "",
        "→ 상세: 14:50 결정 레포트",
    ]
    return "\n".join(lines)


def build_limit_up_alert_from_quote(
    quote: dict[str, Any],
    themes: list[str],
    layer2_stats: dict[str, Any],
    detected_at: datetime,
) -> str:
    """fetch_quote / detect_new_limit_up 결과 dict에서 바로 생성하는 편의 함수."""
    return build_limit_up_alert(
        code=str(quote.get("code", "")),
        name=str(quote.get("name", "")),
        price=int(quote.get("price", 0)),
        prev_close=int(quote.get("prev_close", 0)),
        daily_return=float(quote.get("daily_return", 0.0)),
        trading_value=int(quote.get("trading_value", 0)),
        rank=int(quote.get("rank", 0)),
        themes=themes,
        layer2_stats=layer2_stats,
        detected_at=detected_at,
    )


def save_event_report(text: str, data_dir, dt: datetime) -> None:
    """이벤트 알림을 파일로 저장."""
    save_report(text, data_dir, dt, "limit_up_event")
