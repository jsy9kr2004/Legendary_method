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
    fmt_layer_stats,
    fmt_sizing_table,
    fmt_sizing_table,
    fmt_date,
)
from src.report.decision import build_decision_report, split_messages
from src.report.event import build_limit_up_alert
from src.report.periodic import (
    build_early_morning_alert,
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


def test_early_morning_alert_no_change_returns_none():
    same_themes = [{"theme": "전기/전선", "count": 3, "codes": ["A"]}]
    same_stocks = [_leader("A", "X", ["전기/전선"], 1, 25.0)]
    result = build_early_morning_alert(
        snapshot_df=_make_snapshot(),
        leading_themes=same_themes,
        prev_leading_themes=same_themes,
        leading_stocks=same_stocks,
        prev_leading_stocks=same_stocks,
        snapshot_dt=_DT_0910,
    )
    assert result is None


def test_early_morning_alert_new_theme_triggers():
    result = build_early_morning_alert(
        snapshot_df=_make_snapshot(),
        leading_themes=[{"theme": "전기/전선", "count": 3, "codes": ["A"]}],
        prev_leading_themes=[],
        leading_stocks=[],
        prev_leading_stocks=[],
        snapshot_dt=_DT_0910,
    )
    assert result is not None
    assert "⚡ [장초반-09:10]" in result
    assert "전기/전선" in result


def test_early_morning_alert_new_leading_stock_triggers():
    """주도주 신규 진입 시 알림 발송."""
    same_themes = [{"theme": "전기/전선", "count": 3, "codes": ["075180"]}]
    new_leader = [_leader("075180", "제룡전기", ["전기/전선"], 1, 28.0, "both")]
    result = build_early_morning_alert(
        snapshot_df=_make_snapshot(),
        leading_themes=same_themes,
        prev_leading_themes=same_themes,
        leading_stocks=new_leader,
        prev_leading_stocks=[],
        snapshot_dt=_DT_0910,
    )
    assert result is not None
    assert "주도주 진입" in result
    assert "제룡전기" in result
    assert "거래대금+상승률" in result  # criterion=both 라벨


def test_early_morning_alert_dropped_leader_triggers():
    same_themes = [{"theme": "전기/전선", "count": 3, "codes": ["075180"]}]
    leader = [_leader("075180", "제룡전기", ["전기/전선"], 1, 28.0)]
    result = build_early_morning_alert(
        snapshot_df=_make_snapshot(),
        leading_themes=same_themes,
        prev_leading_themes=same_themes,
        leading_stocks=[],
        prev_leading_stocks=leader,
        snapshot_dt=_DT_0910,
    )
    assert result is not None
    assert "주도주 이탈" in result


def test_early_morning_alert_criterion_change_triggers():
    """기준이 volume → both 등으로 격상되면 알림."""
    same_themes = [{"theme": "전기/전선", "count": 3, "codes": ["075180"]}]
    prev = [_leader("075180", "제룡전기", ["전기/전선"], 1, 15.0, "volume")]
    curr = [_leader("075180", "제룡전기", ["전기/전선"], 1, 25.0, "both")]
    result = build_early_morning_alert(
        snapshot_df=_make_snapshot(),
        leading_themes=same_themes,
        prev_leading_themes=same_themes,
        leading_stocks=curr,
        prev_leading_stocks=prev,
        snapshot_dt=_DT_0910,
    )
    assert result is not None
    assert "기준 변동" in result


def test_early_morning_alert_multi_theme_stock():
    """한 종목이 여러 주도섹터에 걸치면 themes 가 합쳐져 표시."""
    themes = [
        {"theme": "전기/전선", "count": 3, "codes": ["075180"]},
        {"theme": "원자력", "count": 3, "codes": ["075180"]},
    ]
    new_leader = [_leader("075180", "제룡전기", ["전기/전선", "원자력"], 1, 28.0, "both")]
    result = build_early_morning_alert(
        snapshot_df=_make_snapshot(),
        leading_themes=themes,
        prev_leading_themes=themes,
        leading_stocks=new_leader,
        prev_leading_stocks=[],
        snapshot_dt=_DT_0910,
    )
    assert "전기/전선" in result and "원자력" in result


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
