"""레포트 공통 포매팅 유틸.

텔레그램/이메일 마크다운에서 일관된 숫자·표 포맷 제공.

규칙 (report-spec.md):
    - 등락률: 소수점 2자리, 부호 명시 (+5.23%)
    - 금액: 억 단위 (1,234,567,890 → 12.3억)
    - 시각: KST HH:MM
    - 종목코드: 6자리
    - 텔레그램 고정폭 표: 코드블록(```) 안에 정렬
"""
from __future__ import annotations

from datetime import datetime, date

KST_WEEKDAYS = "월화수목금토일"


def fmt_pct(value: float, digits: int = 2, sign: bool = True) -> str:
    """등락률 포맷.

    Examples:
        5.23   → "+5.23%"
        -2.1   → "-2.10%"
        0.0    → "+0.00%"
    """
    if value != value:  # NaN
        return "N/A"
    prefix = "+" if (sign and value >= 0) else ""
    return f"{prefix}{value:.{digits}f}%"


def fmt_price(value: int | float) -> str:
    """원화 가격 천단위 콤마.

    Examples:
        91300  → "91,300"
    """
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "N/A"


def fmt_billion(value: int | float) -> str:
    """거래대금 억 단위 변환.

    Examples:
        400_000_000_000 → "4,000.0억"
        12_345_678_900  → "123.5억"
    """
    try:
        bil = value / 1e8
        if bil >= 1000:
            return f"{bil:,.0f}억"
        return f"{bil:.1f}억"
    except (TypeError, ValueError):
        return "N/A"


def fmt_date(d: date) -> str:
    """날짜 + 요일.

    Example: date(2026, 5, 6) → "2026-05-06 (수)"
    """
    dow = KST_WEEKDAYS[d.weekday()]
    return f"{d.strftime('%Y-%m-%d')} ({dow})"


def fmt_time(dt: datetime) -> str:
    """HH:MM 포맷."""
    return dt.strftime("%H:%M")


def sep(char: str = "═", width: int = 35) -> str:
    """텔레그램 구분선."""
    return char * width


def code_block(text: str) -> str:
    """텔레그램 고정폭 코드블록."""
    return f"```\n{text}\n```"


def fmt_layer_stats(stats: dict, label: str, is_sizing_basis: bool = False) -> str:
    """단일 Layer 통계 1줄 요약.

    Example:
        "Layer 2: n=4  P=100%  E[갭]=+7.8%  σ=3.1%  ★"
    """
    n = stats.get("n", 0)
    if n == 0:
        return f"{label}: 사례 없음"

    p = stats.get("p", float("nan"))
    avg = stats.get("avg_gap", float("nan"))
    std = stats.get("std_gap", float("nan"))

    p_str = f"{p*100:.0f}%" if p == p else "N/A"
    avg_str = fmt_pct(avg) if avg == avg else "N/A"
    std_str = f"{std:.1f}%" if std == std else "N/A"
    star = "  ★ 사이징 기준" if is_sizing_basis else ""

    return f"{label}: n={n}  P={p_str}  E[갭]={avg_str}  σ={std_str}{star}"


def fmt_sizing_table(candidates: list[dict]) -> str:
    """사이징 제안 고정폭 표 (코드블록용).

    Args:
        candidates: 각 dict에 name, p_gap, avg_gap, kelly, sharpe, equal 키 포함.

    Returns:
        코드블록 안에 넣을 텍스트.
    """
    if not candidates:
        return "종배 후보 없음"

    header = f"{'종목':<10} {'P(갭상)':>7} {'E[갭]':>7} {'Kelly':>7} {'Sharpe':>7} {'균등':>7}"
    lines = [header, "-" * len(header)]
    for c in candidates:
        name = str(c.get("name", ""))[:9]
        p_str = f"{c['p_gap']*100:.0f}%" if c.get("p_gap") == c.get("p_gap") else "N/A"
        gap_str = fmt_pct(c["avg_gap"]) if c.get("avg_gap") == c.get("avg_gap") else "N/A"

        kelly = c.get("kelly")
        kelly_str = f"{kelly*100:.1f}%" if kelly is not None else "제외"
        sharpe_str = f"{c['sharpe']*100:.1f}%" if c.get("sharpe") is not None else "N/A"
        equal_str = f"{c['equal']*100:.1f}%"

        lines.append(
            f"{name:<10} {p_str:>7} {gap_str:>7} {kelly_str:>7} {sharpe_str:>7} {equal_str:>7}"
        )
    return "\n".join(lines)


def save_report(text: str, data_dir, dt: datetime, label: str) -> None:
    """레포트를 파일로 저장.

    경로: {DATA_DIR}/reports/YYYY-MM-DD/{HH_MM}_{label}.md
    """
    from pathlib import Path
    d = dt.date()
    path: Path = Path(data_dir) / "reports" / d.strftime("%Y-%m-%d") / f"{dt.strftime('%H_%M')}_{label}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
