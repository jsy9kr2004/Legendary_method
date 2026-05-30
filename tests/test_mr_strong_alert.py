"""푸시 알림 (v4, 2026-05-30) — `_maybe_push_mr_strong_alert` 단위 테스트.

조건 (단고 폐기):
  - exit_signal (보유 trailing -1% 도달) → "청산" push (우선)
  - mr_sigB AND mr_grade_buy == "STRONG" (강망치 매수신호) → "STRONG단저" push
  - 같은 kind 연속 → push X / kind 전환 → push / 발화 영역 벗어남 → alert_kind reset
  - /on /off 와 무관 (paused 일 때도 push — 호출자가 보장)
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

from src.dashboard.state import MonitoredStock
from src.dashboard.worker import _maybe_push_mr_strong_alert


def _make_stock(
    grade_buy="STRONG", sigB=False, exit_signal=False,
    alert_kind=None, holding_peak=None,
) -> MonitoredStock:
    """v4 — 강망치 STRONG 단저 + 청산(trailing). 단고 폐기."""
    m = MonitoredStock(
        code="091340", name="대한광통신",
        added_at=datetime(2026, 5, 29, 9, 30),
        is_auto=True,
    )
    m.sector_role = "leader"
    m.surface_sector_name = "AI데이터센터"
    m.mr_grade = grade_buy
    m.mr_grade_buy = grade_buy
    m.mr_score_buy = 1.59 if grade_buy == "STRONG" else 0.0
    m.mr_score = m.mr_score_buy
    m.mr_sigB = sigB
    m.exit_signal = exit_signal
    m.holding_peak = holding_peak
    m.mr_reason = "강망치 단저 swing-low 진폭 1.59% (STRONG)"
    m.mr_alert_kind = alert_kind
    return m


def test_push_on_strong_sigB_fresh():
    """첫 STRONG 강망치 + sigB → push + mr_alert_kind='STRONG단저'."""
    m = _make_stock(sigB=True)
    with patch("src.dashboard.worker.send_message_single") as send:
        send.return_value = {"ok": True}
        _maybe_push_mr_strong_alert(m, datetime(2026, 5, 29, 9, 32, 10), "t", "c")
    assert send.call_count == 1
    text = send.call_args[0][2]
    assert "🚨 STRONG 단저 매수신호" in text
    assert "⭐ 주도주" in text
    assert "🟢 강망치" in text
    assert "대한광통신" in text
    assert m.mr_alert_kind == "STRONG단저"


def test_no_push_when_same_kind_consecutive():
    """이전에 STRONG단저 push 했으면 다시 sigB STRONG 와도 push X."""
    m = _make_stock(sigB=True, alert_kind="STRONG단저")
    with patch("src.dashboard.worker.send_message_single") as send:
        _maybe_push_mr_strong_alert(m, datetime(2026, 5, 29, 9, 32, 10), "t", "c")
    assert send.call_count == 0
    assert m.mr_alert_kind == "STRONG단저"


def test_push_on_exit_signal():
    """보유 종목 청산 시그널(trailing 도달) → "청산" push (is_held=True)."""
    m = _make_stock(grade_buy="NEUTRAL", sigB=False, exit_signal=True, holding_peak=92000)
    with patch("src.dashboard.worker.send_message_single") as send:
        send.return_value = {"ok": True}
        _maybe_push_mr_strong_alert(m, datetime(2026, 5, 29, 9, 35, 0), "t", "c", is_held=True)
    assert send.call_count == 1
    text = send.call_args[0][2]
    assert "🚨 청산 시그널" in text
    assert "🔴 trailing" in text
    assert "92,000" in text
    assert m.mr_alert_kind == "청산"


def test_exit_signal_no_push_when_not_held():
    """감시 모드(미보유) 청산 시그널은 push X — 카드에만 표시 (노이즈 방지)."""
    m = _make_stock(grade_buy="NEUTRAL", sigB=False, exit_signal=True, holding_peak=92000)
    with patch("src.dashboard.worker.send_message_single") as send:
        _maybe_push_mr_strong_alert(m, datetime(2026, 5, 29, 9, 35, 0), "t", "c", is_held=False)
    assert send.call_count == 0


def test_exit_signal_takes_priority_over_strong_buy():
    """청산 시그널이 STRONG 단저보다 우선 (보유 중 trailing 도달이 더 급함)."""
    m = _make_stock(grade_buy="STRONG", sigB=True, exit_signal=True, holding_peak=92000)
    with patch("src.dashboard.worker.send_message_single") as send:
        send.return_value = {"ok": True}
        _maybe_push_mr_strong_alert(m, datetime(2026, 5, 29, 9, 35, 0), "t", "c", is_held=True)
    assert "🚨 청산 시그널" in send.call_args[0][2]
    assert m.mr_alert_kind == "청산"


def test_no_trigger_no_push():
    """sigB/exit 모두 False → push X + alert_kind reset."""
    m = _make_stock(sigB=False, exit_signal=False)
    with patch("src.dashboard.worker.send_message_single") as send:
        _maybe_push_mr_strong_alert(m, datetime(2026, 5, 29, 9, 30, 0), "t", "c")
    assert send.call_count == 0
    assert m.mr_alert_kind is None


def test_non_strong_grade_resets_alert_kind():
    """grade_buy < STRONG → push X + alert_kind reset (재진입 시 다시 push)."""
    m = _make_stock(grade_buy="WATCH", sigB=True, alert_kind="STRONG단저")
    with patch("src.dashboard.worker.send_message_single") as send:
        _maybe_push_mr_strong_alert(m, datetime(2026, 5, 29, 9, 40, 0), "t", "c")
    assert send.call_count == 0
    assert m.mr_alert_kind is None


def test_strong_reentry_after_reset_pushes_again():
    """STRONG → WATCH (reset) → STRONG 재진입 → 다시 push."""
    m = _make_stock(grade_buy="STRONG", sigB=True)
    m.mr_alert_kind = "STRONG단저"
    m.mr_grade_buy = "WATCH"
    with patch("src.dashboard.worker.send_message_single") as send:
        _maybe_push_mr_strong_alert(m, datetime(2026, 5, 29, 9, 40, 0), "t", "c")
        assert send.call_count == 0
        assert m.mr_alert_kind is None
    m.mr_grade_buy = "STRONG"
    m.mr_sigB = True
    with patch("src.dashboard.worker.send_message_single") as send:
        send.return_value = {"ok": True}
        _maybe_push_mr_strong_alert(m, datetime(2026, 5, 29, 9, 45, 0), "t", "c")
        assert send.call_count == 1
        assert m.mr_alert_kind == "STRONG단저"


def test_buy_to_exit_transition():
    """STRONG단저 push 후 청산 시그널 발화 → 재 push (kind 전환)."""
    m = _make_stock(grade_buy="NEUTRAL", exit_signal=True, holding_peak=92000, alert_kind="STRONG단저")
    with patch("src.dashboard.worker.send_message_single") as send:
        send.return_value = {"ok": True}
        _maybe_push_mr_strong_alert(m, datetime(2026, 5, 29, 9, 50, 0), "t", "c", is_held=True)
    assert send.call_count == 1
    assert "🚨 청산 시그널" in send.call_args[0][2]
    assert m.mr_alert_kind == "청산"


def test_candidate_role_label_in_text():
    """sector_role='candidate' → 🌟 주도주 후보 표시."""
    m = _make_stock(sigB=True)
    m.sector_role = "candidate"
    with patch("src.dashboard.worker.send_message_single") as send:
        send.return_value = {"ok": True}
        _maybe_push_mr_strong_alert(m, datetime(2026, 5, 29, 9, 32, 10), "t", "c")
    assert "🌟 주도주 후보" in send.call_args[0][2]


def test_manual_role_label_in_text():
    """is_manual=True + sector_role=None → 🔵 수동 라벨."""
    m = _make_stock(sigB=True)
    m.is_auto = False
    m.is_manual = True
    m.sector_role = None
    with patch("src.dashboard.worker.send_message_single") as send:
        send.return_value = {"ok": True}
        _maybe_push_mr_strong_alert(m, datetime(2026, 5, 29, 9, 32, 10), "t", "c")
    assert "🔵 수동" in send.call_args[0][2]


def test_sigB_no_push_when_grade_buy_watch():
    """단저 시그널 있어도 grade_buy=WATCH 면 push X (강망치는 STRONG 만)."""
    m = _make_stock(grade_buy="WATCH", sigB=True)
    with patch("src.dashboard.worker.send_message_single") as send:
        _maybe_push_mr_strong_alert(m, datetime(2026, 5, 29, 9, 32, 10), "t", "c")
    assert send.call_count == 0
