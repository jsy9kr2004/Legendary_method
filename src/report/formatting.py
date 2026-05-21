"""레포트 공통 포매팅 유틸.

텔레그램/이메일 마크다운에서 일관된 숫자·표 포맷 제공.

규칙 (report-spec.md):
    - 등락률: 소수점 2자리, 부호 명시 (+5.23%)
    - 금액: 억 단위 (1,234,567,890 → 12.3억)
    - 시각: KST HH:MM
    - 종목코드: 6자리
    - 텔레그램 고정폭 표: 코드블록(```) 안에 정렬 (한글=2셀 폭 인식)
"""
from __future__ import annotations

from datetime import datetime, date

KST_WEEKDAYS = "월화수목금토일"


def _display_width(s: str) -> int:
    """텔레그램 고정폭 폰트 기준 표시 너비 추정.

    CJK / 한글 / 전각 문자 = 2셀, ASCII = 1셀.
    유니코드 코드포인트 범위 기반 단순 휴리스틱 (wcwidth 의존성 회피).
    """
    w = 0
    for c in s:
        cp = ord(c)
        if (
            0x1100 <= cp <= 0x115F                      # Hangul Jamo
            or 0x2E80 <= cp <= 0x303E                   # CJK Radicals · Kangxi
            or 0x3041 <= cp <= 0x33FF                   # Hiragana · Katakana · CJK Symbols
            or 0x3400 <= cp <= 0x4DBF                   # CJK Ext A
            or 0x4E00 <= cp <= 0x9FFF                   # CJK Unified Ideographs
            or 0xA000 <= cp <= 0xA4CF                   # Yi
            or 0xAC00 <= cp <= 0xD7A3                   # Hangul Syllables
            or 0xF900 <= cp <= 0xFAFF                   # CJK Compatibility Ideographs
            or 0xFE30 <= cp <= 0xFE4F                   # CJK Compatibility Forms
            or 0xFF00 <= cp <= 0xFF60                   # Fullwidth Forms
            or 0xFFE0 <= cp <= 0xFFE6                   # Fullwidth signs
        ):
            w += 2
        else:
            w += 1
    return w


def _pad(s: str, width: int, align: str = "left") -> str:
    """`s` 를 `width` 셀에 맞춰 한글 인지 패딩.

    align: 'left' | 'right'
    """
    deficit = max(0, width - _display_width(s))
    pad = " " * deficit
    return s + pad if align == "left" else pad + s


def _truncate_to_width(s: str, max_width: int) -> str:
    """`max_width` 셀을 넘지 않도록 잘라냄 (한글 인지)."""
    out = []
    w = 0
    for c in s:
        cw = _display_width(c)
        if w + cw > max_width:
            break
        out.append(c)
        w += cw
    return "".join(out)


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


def fmt_volume(value: int | float) -> str:
    """거래량 주 단위 — 만/주 자동 분기.

    Examples:
        5_000_000 → "500.0만주"
        12_345    → "12,345주"
        0         → "0주"
    """
    try:
        v = int(value)
        if v >= 10_000:
            return f"{v / 10_000:,.1f}만주"
        return f"{v:,}주"
    except (TypeError, ValueError):
        return "N/A"


def fmt_market_cap(value: int | float) -> str:
    """시가총액 표시 — 단위 **억원** (KIS mst 기준). 1조 이상은 조 단위로 자동 변환.

    Examples:
        5_000_000 → "500.0조"   (삼성전자 ~500조)
        14_500    → "1.5조"      (제주반도체 ~1.5조)
        5_000     → "5,000억"    (광전자 ~5천억)
        500       → "500억"
        0         → "N/A"
    """
    try:
        v = int(value)
        if v <= 0:
            return "N/A"
        if v >= 10_000:  # 1조 = 10,000억 이상
            return f"{v / 10_000:,.1f}조"
        return f"{v:,}억"
    except (TypeError, ValueError):
        return "N/A"


def fmt_rank(value: Any) -> str:
    """순위 정수 — NaN/None 시 '—'.

    Examples:
        11   → "11위"
        None → "—"
        NaN  → "—"
    """
    if value is None:
        return "—"
    try:
        f = float(value)
        if f != f:  # NaN
            return "—"
        return f"{int(f)}위"
    except (TypeError, ValueError):
        return "—"


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
        코드블록 안에 넣을 텍스트. 한글 종목명 wide-char 인지 정렬.
    """
    if not candidates:
        return "종배 후보 없음"

    # 컬럼 너비 (display cell 기준)
    name_w, p_w, gap_w, k_w, s_w, e_w = 12, 7, 7, 7, 7, 7

    def row(name: str, p: str, gap: str, k: str, s: str, e: str) -> str:
        return (
            f"{_pad(name, name_w, 'left')} "
            f"{_pad(p, p_w, 'right')} "
            f"{_pad(gap, gap_w, 'right')} "
            f"{_pad(k, k_w, 'right')} "
            f"{_pad(s, s_w, 'right')} "
            f"{_pad(e, e_w, 'right')}"
        )

    header = row("종목", "P(갭상)", "E[갭]", "Kelly", "Sharpe", "균등")
    sep_line = "-" * _display_width(header)
    lines = [header, sep_line]

    for c in candidates:
        raw_name = str(c.get("name", ""))
        name = _truncate_to_width(raw_name, name_w)
        p_str = f"{c['p_gap']*100:.0f}%" if c.get("p_gap") == c.get("p_gap") else "N/A"
        gap_str = fmt_pct(c["avg_gap"]) if c.get("avg_gap") == c.get("avg_gap") else "N/A"

        kelly = c.get("kelly")
        kelly_str = f"{kelly*100:.1f}%" if kelly is not None else "제외"
        sharpe_str = f"{c['sharpe']*100:.1f}%" if c.get("sharpe") is not None else "N/A"
        equal_str = f"{c['equal']*100:.1f}%"

        lines.append(row(name, p_str, gap_str, kelly_str, sharpe_str, equal_str))
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
