"""단저단고 STRONG 푸시 알림 — `_maybe_push_mr_strong_alert` 단위 테스트.

조건:
  - monitored.mr_grade == "STRONG" + (mr_sigB or mr_sigS) → push
  - 같은 kind 연속 → push X
  - kind 전환 → push
  - STRONG 벗어남 → mr_alert_kind None reset
  - /on /off 와 무관 (paused 일 때도 push 됨 — 호출자가 보장)
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

from src.dashboard.state import MonitoredStock
from src.dashboard.worker import _maybe_push_mr_strong_alert


def _make_stock(grade="STRONG", sigB=False, sigS=False, alert_kind=None) -> MonitoredStock:
    m = MonitoredStock(
        code="091340", name="대한광통신",
        added_at=datetime(2026, 5, 29, 9, 30),
        is_auto=True,
    )
    m.sector_role = "leader"
    m.surface_sector_name = "AI데이터센터"
    m.mr_grade = grade
    m.mr_sigB = sigB
    m.mr_sigS = sigS
    m.mr_score = 2.3
    m.mr_reason = "STOCH=30 Z=-1.08 atr0.4%"
    m.mr_alert_kind = alert_kind
    return m


def test_push_on_strong_sigB_fresh():
    """첫 STRONG + sigB → push + mr_alert_kind='단저'."""
    m = _make_stock(sigB=True)
    with patch("src.dashboard.worker.send_message_single") as send:
        send.return_value = {"ok": True}
        _maybe_push_mr_strong_alert(m, datetime(2026, 5, 29, 9, 32, 10), "t", "c")
    assert send.call_count == 1
    args, kwargs = send.call_args
    text = args[2]
    assert "🚨 단저단고 STRONG" in text
    assert "⭐ 주도주" in text
    assert "🟢 단저" in text
    assert "대한광통신" in text
    assert m.mr_alert_kind == "단저"


def test_no_push_when_same_kind_consecutive():
    """이전에 단저 push 했으면 다시 sigB STRONG 와도 push X."""
    m = _make_stock(sigB=True, alert_kind="단저")
    with patch("src.dashboard.worker.send_message_single") as send:
        _maybe_push_mr_strong_alert(m, datetime(2026, 5, 29, 9, 32, 10), "t", "c")
    assert send.call_count == 0
    assert m.mr_alert_kind == "단저"


def test_push_on_kind_transition():
    """단저 → 단고 전환 시 재 push."""
    m = _make_stock(sigS=True, alert_kind="단저")
    with patch("src.dashboard.worker.send_message_single") as send:
        send.return_value = {"ok": True}
        _maybe_push_mr_strong_alert(m, datetime(2026, 5, 29, 9, 35, 0), "t", "c")
    assert send.call_count == 1
    text = send.call_args[0][2]
    assert "🔴 단고" in text
    assert m.mr_alert_kind == "단고"


def test_strong_grade_without_trigger_no_push():
    """STRONG 영역이지만 sigB/sigS 모두 False → push X (사용자: '단저/단고 strong' 만)."""
    m = _make_stock(sigB=False, sigS=False)
    with patch("src.dashboard.worker.send_message_single") as send:
        _maybe_push_mr_strong_alert(m, datetime(2026, 5, 29, 9, 30, 0), "t", "c")
    assert send.call_count == 0
    assert m.mr_alert_kind is None


def test_non_strong_grade_resets_alert_kind():
    """STRONG 영역 벗어나면 mr_alert_kind 가 None 으로 reset (재진입 시 다시 push 위해)."""
    m = _make_stock(grade="WATCH", sigB=True, alert_kind="단저")
    with patch("src.dashboard.worker.send_message_single") as send:
        _maybe_push_mr_strong_alert(m, datetime(2026, 5, 29, 9, 40, 0), "t", "c")
    assert send.call_count == 0  # WATCH 면 push X
    assert m.mr_alert_kind is None  # reset


def test_strong_reentry_after_reset_pushes_again():
    """STRONG → WATCH (reset) → STRONG 재진입 → 다시 push."""
    m = _make_stock(sigB=True)
    m.mr_alert_kind = "단저"  # 옛 push 흔적

    # WATCH 로 떨어짐
    m.mr_grade = "WATCH"
    with patch("src.dashboard.worker.send_message_single") as send:
        _maybe_push_mr_strong_alert(m, datetime(2026, 5, 29, 9, 40, 0), "t", "c")
        assert send.call_count == 0
        assert m.mr_alert_kind is None

    # STRONG 재진입
    m.mr_grade = "STRONG"
    m.mr_sigB = True
    with patch("src.dashboard.worker.send_message_single") as send:
        send.return_value = {"ok": True}
        _maybe_push_mr_strong_alert(m, datetime(2026, 5, 29, 9, 45, 0), "t", "c")
        assert send.call_count == 1
        assert m.mr_alert_kind == "단저"


def test_candidate_role_label_in_text():
    """sector_role='candidate' 인 경우 메시지에 🌟 주도주 후보 표시."""
    m = _make_stock(sigB=True)
    m.sector_role = "candidate"
    with patch("src.dashboard.worker.send_message_single") as send:
        send.return_value = {"ok": True}
        _maybe_push_mr_strong_alert(m, datetime(2026, 5, 29, 9, 32, 10), "t", "c")
    text = send.call_args[0][2]
    assert "🌟 주도주 후보" in text


def test_manual_role_label_in_text():
    """is_manual=True + sector_role=None → 🔵 수동 라벨."""
    m = _make_stock(sigB=True)
    m.is_auto = False
    m.is_manual = True
    m.sector_role = None
    with patch("src.dashboard.worker.send_message_single") as send:
        send.return_value = {"ok": True}
        _maybe_push_mr_strong_alert(m, datetime(2026, 5, 29, 9, 32, 10), "t", "c")
    text = send.call_args[0][2]
    assert "🔵 수동" in text
