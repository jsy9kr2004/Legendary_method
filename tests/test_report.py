"""src.report 모듈 테스트."""
from __future__ import annotations

import math
from datetime import datetime, date

import pandas as pd
import pytz
import pytest

from src.report.formatting import (
    _display_width,
    _pad,
    _truncate_to_width,
    fmt_billion,
    fmt_pct,
    fmt_price,
    fmt_rank,
    fmt_layer_stats,
    fmt_sizing_table,
    fmt_sizing_table,
    fmt_volume,
    fmt_date,
)
from src.report.decision import (
    build_decision_report,
    load_decision_candidates,
    save_decision_candidates,
    split_messages,
)
from src.report.event import build_limit_up_alert
from src.report.periodic import (
    build_periodic_report,
    has_significant_change,
)
from src.report.morning import build_morning_report
from src.report.afterhours import build_afterhours_report

KST = pytz.timezone("Asia/Seoul")
_DT = datetime(2026, 5, 6, 14, 50, 0, tzinfo=KST)
_DT_1100 = datetime(2026, 5, 6, 11, 0, 0, tzinfo=KST)
_DT_0910 = datetime(2026, 5, 6, 9, 10, 0, tzinfo=KST)


# ── formatting ───────────────────────────────────────────────────────────────

def test_fmt_pct_positive():
    assert fmt_pct(5.23) == "+5.23%"

def test_fmt_pct_negative():
    assert fmt_pct(-2.1) == "-2.10%"

def test_fmt_pct_nan():
    assert fmt_pct(float("nan")) == "N/A"

def test_fmt_price():
    assert fmt_price(91300) == "91,300"

def test_fmt_billion_large():
    assert "억" in fmt_billion(400_000_000_000)

def test_fmt_billion_small():
    assert "억" in fmt_billion(1_200_000_000)


def test_fmt_volume_large_in_10k_units():
    assert fmt_volume(5_000_000) == "500.0만주"
    assert fmt_volume(12_345_678) == "1,234.6만주"


def test_fmt_volume_small_in_shares():
    assert fmt_volume(9_999) == "9,999주"
    assert fmt_volume(0) == "0주"


def test_fmt_rank_basic():
    assert fmt_rank(1) == "1위"
    assert fmt_rank(11) == "11위"


def test_fmt_rank_handles_nan_and_none():
    assert fmt_rank(None) == "—"
    assert fmt_rank(float("nan")) == "—"

def test_fmt_date_weekday():
    d = date(2026, 5, 6)  # 수요일
    result = fmt_date(d)
    assert "2026-05-06" in result
    assert "수" in result

def test_fmt_layer_stats_normal():
    stats = {"n": 4, "p": 1.0, "avg_gap": 7.8, "std_gap": 3.1}
    result = fmt_layer_stats(stats, "Layer 2")
    assert "n=4" in result
    assert "P=100%" in result
    assert "+7.80%" in result

def test_fmt_layer_stats_zero_n():
    result = fmt_layer_stats({"n": 0}, "Layer 3")
    assert "사례 없음" in result

def test_fmt_sizing_table():
    rows = [{"name": "제룡전기", "p_gap": 1.0, "avg_gap": 8.9,
             "kelly": 0.20, "sharpe": 0.421, "equal": 0.333}]
    result = fmt_sizing_table(rows)
    assert "제룡전기" in result
    assert "100%" in result

def test_fmt_sizing_table_kelly_none():
    rows = [{"name": "X", "p_gap": 0.7, "avg_gap": 3.0,
             "kelly": None, "sharpe": 0.5, "equal": 1.0}]
    result = fmt_sizing_table(rows)
    assert "제외" in result


# ── H3: wide-char 정렬 ───────────────────────────────────────────────────────

def test_display_width_ascii():
    assert _display_width("hello") == 5
    assert _display_width("ABC123") == 6


def test_display_width_korean():
    assert _display_width("제룡전기") == 8     # 4글자 × 2셀
    assert _display_width("SK하이닉스") == 10  # 2 + 4×2


def test_pad_korean_left():
    """한글 종목명 왼쪽 정렬: 시각 너비 기준으로 패딩."""
    out = _pad("제룡전기", 12, "left")
    assert _display_width(out) == 12
    assert out.startswith("제룡전기")


def test_pad_korean_right():
    out = _pad("100%", 7, "right")
    assert _display_width(out) == 7
    assert out.endswith("100%")


def test_truncate_to_width_korean():
    assert _truncate_to_width("제룡전기홀딩스", 8) == "제룡전기"
    assert _truncate_to_width("제룡전기홀딩스", 10) == "제룡전기홀"


def test_fmt_sizing_table_aligned_with_korean_names():
    """한글/영문 혼합 종목명에서도 각 행이 동일한 시각 너비를 가져야 한다."""
    rows = [
        {"name": "제룡전기", "p_gap": 1.0, "avg_gap": 8.9,
         "kelly": 0.20, "sharpe": 0.42, "equal": 0.33},
        {"name": "SK하이닉스", "p_gap": 0.7, "avg_gap": 3.0,
         "kelly": 0.10, "sharpe": 0.30, "equal": 0.33},
        {"name": "AAPL", "p_gap": 0.6, "avg_gap": 2.0,
         "kelly": 0.05, "sharpe": 0.20, "equal": 0.33},
    ]
    result = fmt_sizing_table(rows)
    lines = result.split("\n")
    # 헤더 + 구분선 + 3행 = 5줄
    assert len(lines) == 5
    # 모든 데이터 행의 시각 너비가 동일
    data_widths = [_display_width(line) for line in lines[2:]]
    assert len(set(data_widths)) == 1, f"행 너비 불일치: {data_widths}"


# ── decision report ──────────────────────────────────────────────────────────

def _make_candidate(**kwargs) -> dict:
    base = {
        "code": "075180", "name": "제룡전기", "rank": 1,
        "price": 91300, "prev_close": 70230,
        "daily_return": 30.0, "intraday_high": 91300,
        "intraday_high_pct": 30.0, "trading_value": 400_000_000_000,
        # 2026-05-22: 거래량 → 회전율+시총 표시 변경 (사용자 정정)
        "market_cap": 14_500,  # 1.4조 (제룡전기 ~1.4조 추정)
        "turnover": 18.3,      # 회전율 18.3%
        "turnover_rank": 2,
        "is_limit_up": True, "priority": "limit_up",
        "themes": ["전기/전선", "원자력"],
        "layers": {
            "layer1": {"n": 7, "p": 0.71, "avg_gap": 4.2, "std_gap": 5.8,
                       "avg_gap_when_up": 6.0, "avg_gap_when_dn": 2.0, "avg_close_return": 5.0},
            "layer2": {"n": 4, "p": 1.0, "avg_gap": 7.8, "std_gap": 3.1,
                       "avg_gap_when_up": 7.8, "avg_gap_when_dn": float("nan"), "avg_close_return": 10.0},
            "layer3": {"n": 3, "p": 1.0, "avg_gap": 8.9, "std_gap": 2.4,
                       "avg_gap_when_up": 8.9, "avg_gap_when_dn": float("nan"), "avg_close_return": 12.0},
            "layer4": {"n": 0, "note": "v1: 분봉 데이터 적재 후 구현"},
        },
        "sizing_layer": "layer3",
        "sizing_stats": {"n": 3, "p": 1.0, "avg_gap": 8.9, "std_gap": 2.4,
                         "avg_gap_when_up": 8.9, "avg_gap_when_dn": float("nan")},
        "sizing": {"kelly": 0.20, "sharpe": 0.421, "equal": 1.0},
    }
    base.update(kwargs)
    return base


def test_decision_report_contains_header():
    report = build_decision_report(
        leading_themes=[{"theme": "전기/전선", "count": 4, "codes": ["075180", "000120"]}],
        candidates=[_make_candidate()],
        snapshot_dt=_DT,
    )
    assert "🎯 [결정-14:50]" in report
    assert "2026-05-06" in report


def test_decision_report_contains_candidate():
    report = build_decision_report([], [_make_candidate()], _DT)
    assert "제룡전기" in report
    assert "075180" in report
    assert "Layer 3" in report
    assert "사이징 기준" in report


def test_decision_report_no_candidates():
    report = build_decision_report([], [], _DT)
    assert "후보 없음" in report


def test_decision_report_shows_turnover_and_market_cap():
    """회전율(거래대금/시총) + 시총 라인 — 단타 자금 유입 강도 표시 (2026-05-22 사용자 정정).

    거래량(주) 은 ETF/저가주 편향이 있어 종배 비교축으로 부적합 (CLAUDE.md 도메인 용어).
    회전율 + 시총으로 변경.
    """
    c = _make_candidate(turnover=18.3, market_cap=14_500, turnover_rank=2)
    report = build_decision_report([], [c], _DT)
    assert "회전율:" in report
    assert "18.30%" in report      # fmt_pct(sign=False)
    assert "1.4조" in report        # fmt_market_cap (14500억 → 1.4조)
    assert "회전율 2위" in report   # fmt_rank


def test_fmt_market_cap_units():
    from src.report.formatting import fmt_market_cap
    assert fmt_market_cap(5_000_000) == "500.0조"  # 삼전
    assert fmt_market_cap(14_500) == "1.4조"       # 1.45조
    assert fmt_market_cap(5_000) == "5,000억"      # 5천억
    assert fmt_market_cap(0) == "N/A"
    assert fmt_market_cap(-1) == "N/A"


def test_decision_report_layer_labels_explain_matching():
    """Layer 1/2/3 라벨이 매칭 조건을 설명해야 함 (2026-05-19 사용자 요청)."""
    report = build_decision_report([], [_make_candidate()], _DT)
    assert "ret≥20% 모든 사례" in report  # Layer 1
    assert "상한가 ret≥29.5%" in report   # Layer 2
    assert "종가위치 ±5% 일치" in report  # Layer 3 (2026-05-22 ±2%→±5%)


def test_decision_report_layer4_explains_what_it_is():
    """Layer 4 가 단순 'v1 미구현' 이 아니라 무엇인지 설명해야 함."""
    report = build_decision_report([], [_make_candidate()], _DT)
    assert "고점도달 시각" in report  # Layer 4 의 의미
    assert "분봉" in report


def test_decision_report_leading_themes():
    report = build_decision_report(
        [{"theme": "전기/전선", "count": 4, "codes": ["A", "B", "C", "D"]}],
        [], _DT,
    )
    assert "전기/전선" in report
    assert "..." in report  # 3개 초과 시 ... 표시


def test_split_messages_short():
    report = "short message"
    assert split_messages(report) == [report]


def test_split_messages_long():
    long_report = "header\n" + "\n▣ ".join([f"종목{i} " * 300 for i in range(5)])
    msgs = split_messages(long_report)
    assert len(msgs) > 1
    assert all(len(m) <= 4096 for m in msgs)


def test_split_messages_keeps_stock_block_atomic():
    # 종목 블록 마커가 메시지 중간에 끊기지 않아야 함 — 각 메시지는 헤더로
    # 시작하거나 "\n▣ " 로 시작하는 블록만 포함
    long_report = "header\n" + "\n▣ ".join([f"종목{i}\n내용 라인\n" * 100 for i in range(5)])
    msgs = split_messages(long_report)
    for m in msgs[1:]:  # 첫 메시지는 header 로 시작, 나머지는 종목 블록부터
        assert m.startswith("\n▣ "), f"종목 블록 시작이 아닌 메시지: {m[:50]!r}"


def test_split_messages_separates_sizing_block():
    # 사이징 블록이 별도 atomic 으로 분리되어 종목 블록 + 사이징이 한
    # 메시지에 합쳐져 max_len 을 초과하지 않도록 보장
    big_stock = "\n▣ 종목A\n" + ("긴 라인\n" * 800)  # ~3500자
    sizing = "\n═══\n[사이징 제안]\n═══\n" + ("표 라인\n" * 100)  # ~700자
    report = "header" + big_stock + sizing
    msgs = split_messages(report)
    # 종목 블록 + 사이징이 합쳐서 4096 초과면 별도 메시지로 분리되어야
    assert len(msgs) >= 2
    sizing_msg = [m for m in msgs if "[사이징 제안]" in m]
    assert len(sizing_msg) == 1
    # 사이징이 들어간 메시지에는 종목 블록 마커가 함께 있지 않아야 (분리 확인)
    assert "▣ 종목A" not in sizing_msg[0]


def test_split_messages_warns_on_oversized_atomic(caplog):
    # 단독 블록 자체가 4096 초과면 경고 로그 + 그대로 발송
    oversized = "\n▣ 종목X\n" + ("a" * 5000)
    report = "header" + oversized
    msgs = split_messages(report)
    # 거부될 수 있어도 일단 메시지 리스트엔 포함
    assert any("▣ 종목X" in m for m in msgs)


# ── event alert ──────────────────────────────────────────────────────────────

def test_limit_up_alert_contains_key_info():
    alert = build_limit_up_alert(
        code="075180", name="제룡전기", price=91300, prev_close=70230,
        daily_return=30.0, trading_value=400_000_000_000, rank=12,
        themes=["전기/전선", "원자력"],
        layer2_stats={"n": 4, "p": 1.0, "avg_gap": 7.8, "std_gap": 3.1},
        detected_at=_DT,
    )
    assert "🚨 [상한가]" in alert
    assert "제룡전기" in alert
    assert "075180" in alert
    assert "전기/전선" in alert
    assert "n=4" in alert
    assert len(alert) < 400  # 짧아야 함


def test_limit_up_alert_no_history():
    alert = build_limit_up_alert(
        code="075180", name="제룡전기", price=91300, prev_close=70230,
        daily_return=30.0, trading_value=400_000_000_000, rank=1,
        themes=[], layer2_stats={"n": 0},
        detected_at=_DT,
    )
    assert "사례 부족" in alert


# ── periodic report ──────────────────────────────────────────────────────────

def _make_snapshot():
    return pd.DataFrame([
        {"rank": 1, "code": "075180", "name": "제룡전기", "daily_return": 30.0,
         "trading_value": 400_000_000_000, "is_limit_up": True,
         "price": 91300, "prev_close": 70230, "intraday_high": 91300, "volume": 1},
        {"rank": 2, "code": "005930", "name": "삼성전자", "daily_return": 1.2,
         "trading_value": 1_600_000_000_000, "is_limit_up": False,
         "price": 80000, "prev_close": 79000, "intraday_high": 81000, "volume": 1},
    ])


def test_periodic_report_contains_header():
    report = build_periodic_report(
        snapshot_df=_make_snapshot(),
        leading_themes=[{"theme": "전기/전선", "count": 3, "codes": ["075180"]}],
        prev_leading_themes=[],
        new_limit_up=[],
        snapshot_dt=_DT_1100,
    )
    assert "📊 [추적-11:00]" in report
    assert "제룡전기" in report
    assert "🔴" in report  # 상한가 표시


def test_periodic_report_new_theme_marked():
    report = build_periodic_report(
        snapshot_df=_make_snapshot(),
        leading_themes=[{"theme": "전기/전선", "count": 3, "codes": ["075180"]}],
        prev_leading_themes=[],
        new_limit_up=[],
        snapshot_dt=_DT_1100,
    )
    assert "🆕" in report


def test_periodic_report_new_limit_up():
    report = build_periodic_report(
        snapshot_df=_make_snapshot(),
        leading_themes=[],
        prev_leading_themes=[],
        new_limit_up=[{"name": "제룡전기", "code": "075180", "daily_return": 30.0}],
        snapshot_dt=_DT_1100,
    )
    assert "신규 상한가" in report
    assert "제룡전기" in report


def _leader(code: str, name: str, themes: list[str], rank: int, ret: float,
            criterion: str = "volume", is_limit_up: bool = False) -> dict:
    return {
        "code": code, "name": name, "themes": themes, "rank": rank,
        "price": 1000, "daily_return": ret, "is_limit_up": is_limit_up,
        "criterion": criterion,
    }


# build_early_morning_alert 관련 테스트는 폐기 (M5.5/M6 dashboard 로 대체).
# 09:00~10:30 변화 감지/알림은 src/dashboard/state.py / worker.py 의
# 상태 머신 + step_tracker 로 검증됨 (test_dashboard_state.py).


def test_has_significant_change_same_themes():
    t = [{"theme": "T1", "count": 3, "codes": []}]
    assert has_significant_change(t, t, []) is False


def test_has_significant_change_new_theme():
    prev = [{"theme": "T1", "count": 3, "codes": []}]
    curr = [{"theme": "T1", "count": 3, "codes": []}, {"theme": "T2", "count": 3, "codes": []}]
    assert has_significant_change(curr, prev, []) is True


def test_has_significant_change_new_limit_up():
    t = [{"theme": "T1", "count": 3, "codes": []}]
    assert has_significant_change(t, t, [{"code": "A"}]) is True


# ── morning report ───────────────────────────────────────────────────────────

def test_morning_report_contains_header():
    report = build_morning_report(
        market_stats={
            "kospi_current": 2600.0, "kospi_prev_close": 2580.0,
            "kospi_ma200": 2500.0, "kospi_60d_return": 5.2,
            "vkospi": 18.5, "bear_ratio_20d": 30.0,
        },
        holdings=[],
        report_dt=datetime(2026, 5, 6, 9, 30, tzinfo=KST),
    )
    assert "📊 [모닝]" in report
    assert "200일 이평" in report
    assert "위 ✅" in report  # KOSPI > MA200


def test_morning_report_holdings():
    report = build_morning_report(
        market_stats={},
        holdings=[{"name": "제룡전기", "code": "075180",
                   "buy_price": 91300, "open_price": 96000}],
        report_dt=datetime(2026, 5, 6, 9, 30, tzinfo=KST),
    )
    assert "제룡전기" in report
    assert "익절" in report


def test_morning_report_empty_market_stats():
    report = build_morning_report({}, [], datetime(2026, 5, 6, 9, 30, tzinfo=KST))
    assert "N/A" in report


def test_morning_report_uses_change_rate_when_prev_close_missing():
    """compute_market_stats 는 kospi_prev_close 를 안 채우고 kospi_change_rate 만
    채움 (2026-05-19 발견). 모닝 레포트가 change_rate 를 직접 사용해야 KOSPI 줄이
    'N/A' 가 아니라 정상 표시."""
    report = build_morning_report(
        market_stats={
            "kospi_current": 2600.0, "kospi_change_rate": 0.78,
            "kospi_60d_return": 5.2,
        },
        holdings=[],
        report_dt=datetime(2026, 5, 6, 9, 30, tzinfo=KST),
    )
    assert "2,600.00" in report
    # change_rate fallback 으로 +0.78% 표시 — N/A 아님
    assert "KOSPI 시초:    N/A" not in report


# ── afterhours report ────────────────────────────────────────────────────────

def test_afterhours_report_contains_header():
    report = build_afterhours_report(
        candidates=[_make_candidate()],
        afterhours_quotes=[
            {"code": "075180", "name": "제룡전기", "price": 95000,
             "prev_close": 91300, "change_pct": 4.1}
        ],
        data_status={
            "ohlcv_updated": True, "ohlcv_count": 2400,
            "snapshots_collected": 4, "errors": [],
        },
        report_dt=datetime(2026, 5, 6, 16, 0, tzinfo=KST),
    )
    assert "📝 [사후]" in report
    assert "제룡전기" in report
    assert "갭상 예고" in report
    assert "✅ 일봉 OHLCV" in report
    assert "4/4" in report


def test_afterhours_report_errors_shown():
    report = build_afterhours_report(
        candidates=[], afterhours_quotes=[],
        data_status={"ohlcv_updated": False, "ohlcv_count": 0,
                     "snapshots_collected": 2, "errors": ["API 타임아웃"]},
        report_dt=datetime(2026, 5, 6, 16, 0, tzinfo=KST),
    )
    assert "API 타임아웃" in report
    assert "❌" in report


# ── market regime line (top of decision report) ──────────────────────────────

def test_decision_report_shows_market_regime():
    report = build_decision_report(
        leading_themes=[],
        candidates=[],
        snapshot_dt=datetime(2026, 5, 6, 14, 50, tzinfo=KST),
        market_stats={
            "kospi_current": 2680.45,
            "kospi_change_rate": 0.83,
            "kospi_above_ma200": True,
            "kospi_60d_return": 3.42,
        },
    )
    assert "[시장 국면]" in report
    assert "KOSPI 2680.45" in report
    assert "200ma 위 ✅" in report
    assert "+3.42%" in report


def test_decision_report_warns_on_weak_market():
    report = build_decision_report(
        leading_themes=[],
        candidates=[],
        snapshot_dt=datetime(2026, 5, 6, 14, 50, tzinfo=KST),
        market_stats={
            "kospi_current": 2400.0,
            "kospi_change_rate": -1.2,
            "kospi_above_ma200": False,
            "kospi_60d_return": -8.5,
        },
    )
    assert "200ma 아래 ⚠" in report
    assert "강세장 가정 무너짐" in report


def test_decision_report_omits_market_section_when_empty():
    report = build_decision_report(
        leading_themes=[],
        candidates=[],
        snapshot_dt=datetime(2026, 5, 6, 14, 50, tzinfo=KST),
        market_stats=None,
    )
    assert "[시장 국면]" not in report


# ── intraday signals (14:50 호가/체결/투자자) ────────────────────────────────

def _candidate_with_signals() -> dict:
    base = _make_candidate()
    base["intraday_signals"] = {
        "asking_price": {
            "bid_total_volume": 3_200_000,
            "ask_total_volume": 450_000,
            "bid_ask_ratio": 7.1,
        },
        "ccnl_strength": {"ccnl_strength": 142.0},
        # 2026-05-22: 외인/기관/프로그램 표시 수량 일관 통일 + N일 평균 비교.
        "investor_flow": {
            "foreign_net_buy": 1_800_000,       # +180만주
            "institution_net_buy": 4_200_000,   # +420만주
            "program_net_buy": 600_000,         # +60만주
        },
        "investor_nday_avg": {
            "n_days": 5,
            "foreign_net_buy_avg": 1_200_000,   # 평균 +120만주
            "institution_net_buy_avg": -800_000, # 평균 -80만주 (오늘 +420만주 = 매수 전환)
            "program_net_buy_avg": 400_000,     # 평균 +40만주
        },
    }
    return base


def test_decision_report_shows_intraday_signals():
    report = build_decision_report(
        leading_themes=[],
        candidates=[_candidate_with_signals()],
        snapshot_dt=datetime(2026, 5, 6, 14, 50, tzinfo=KST),
    )
    assert "[14:50 시그널]" in report
    assert "표시만 — 사이징 미반영" in report
    assert "체결강도 142" in report
    # 2026-05-22: 수급 라인 N일 평균 비교 형식
    assert "수급 (5일 자체 누적 평균 대비)" in report
    assert "외국인:" in report
    assert "+180만주" in report           # 오늘 외인
    assert "+420만주" in report           # 오늘 기관
    assert "+60만주" in report            # 오늘 프로그램
    assert "+50% vs 평균 +120만주" in report  # 외인 같은 방향 50% 강화
    assert "🔥 매수 전환" in report           # 기관 부호 전환 (평균 매도 → 오늘 매수)
    assert "🟢 매수 우세" in report


def test_decision_report_first_day_no_nday_avg():
    """첫날 (자체 누적 없음) — 오늘 값만 표시 + '자체 누적 시작' 안내."""
    c = _make_candidate()
    c["intraday_signals"] = {
        "asking_price": {"bid_total_volume": 100, "ask_total_volume": 100, "bid_ask_ratio": 1.0},
        "ccnl_strength": {"ccnl_strength": 100.0},
        "investor_flow": {
            "foreign_net_buy": 500_000, "institution_net_buy": 0, "program_net_buy": -100_000,
        },
        # investor_nday_avg 누락 (첫날)
    }
    report = build_decision_report(
        leading_themes=[],
        candidates=[c],
        snapshot_dt=datetime(2026, 5, 6, 14, 50, tzinfo=KST),
    )
    assert "외국인 +50만주" in report
    assert "프로그램 -10만주" in report
    assert "자체 누적 시작" in report


def test_decision_report_flags_sell_side_dominance():
    c = _make_candidate()
    c["intraday_signals"] = {
        "asking_price": {
            "bid_total_volume": 100_000,
            "ask_total_volume": 500_000,
            "bid_ask_ratio": 0.2,
        },
        "ccnl_strength": {"ccnl_strength": 65.0},
    }
    report = build_decision_report(
        leading_themes=[],
        candidates=[c],
        snapshot_dt=datetime(2026, 5, 6, 14, 50, tzinfo=KST),
    )
    assert "🔴 매도 우세" in report  # 호가, 체결 둘 다


def test_decision_report_no_signal_section_when_signals_absent():
    """signals 없으면 [14:50 시그널] 섹션 자체가 표시되지 않음."""
    report = build_decision_report(
        leading_themes=[],
        candidates=[_make_candidate()],
        snapshot_dt=datetime(2026, 5, 6, 14, 50, tzinfo=KST),
    )
    assert "[14:50 시그널]" not in report


# ── decision candidates persistence ──────────────────────────────────────────

def test_decision_candidates_round_trip(tmp_path):
    """save → load 시 후보가 동일하게 복원되고 NaN은 None으로 직렬화된다."""
    dt = datetime(2026, 5, 6, 14, 50, tzinfo=KST)
    candidates = [
        {
            "code": "075180",
            "name": "제룡전기",
            "price": 91500,
            "daily_return": 29.97,
            "priority": "limit_up",
            "themes": ["전력기기", "원전"],
            "layers": {"layer1": {"n": 7, "p": 0.71, "avg_gap": 4.2}},
            "sizing_stats": {"n": 3, "p": 1.0, "avg_gap": 8.9},
            "sizing": {"kelly": 0.2, "sharpe": 0.42, "equal": 0.333},
            "intraday_high_pct": float("nan"),  # NaN → null
        }
    ]
    path = save_decision_candidates(candidates, tmp_path, dt)
    assert path.exists()
    assert path.name == "2026-05-06.json"

    loaded = load_decision_candidates(tmp_path, dt.date())
    assert len(loaded) == 1
    c = loaded[0]
    assert c["code"] == "075180"
    assert c["name"] == "제룡전기"
    assert c["daily_return"] == 29.97
    assert c["themes"] == ["전력기기", "원전"]
    assert c["sizing"]["kelly"] == 0.2
    assert c["intraday_high_pct"] is None  # NaN → None


def test_load_decision_candidates_missing_file_returns_empty(tmp_path):
    """파일이 없으면 빈 리스트를 반환 (사후 레포트가 graceful skip)."""
    result = load_decision_candidates(tmp_path, date(2026, 5, 6))
    assert result == []
