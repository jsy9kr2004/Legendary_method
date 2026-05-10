"""src.dashboard.state 단위 테스트."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.dashboard.state import (
    Alert,
    LeaderState,
    MonitoringSession,
    Source,
    in_monitoring_window,
)
from src.jongbae.config_thresholds import (
    GRACE_PERIOD_SECONDS,
    MONITORING_MAX_CODES,
    TRANSITION_EXIT_PERSIST_SECONDS,
)


# ── in_monitoring_window ─────────────────────────────────────────────────────


def test_in_window_business_day_within():
    # 2026-05-11 월요일 09:30
    assert in_monitoring_window(datetime(2026, 5, 11, 9, 30)) is True


def test_in_window_business_day_outside():
    assert in_monitoring_window(datetime(2026, 5, 11, 8, 59)) is False
    assert in_monitoring_window(datetime(2026, 5, 11, 10, 31)) is False


def test_in_window_weekend():
    # 2026-05-09 토요일
    assert in_monitoring_window(datetime(2026, 5, 9, 9, 30)) is False


# ── add_manual / 토글 ────────────────────────────────────────────────────────


def test_add_manual_basic():
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    changed, msg = s.add_manual("005930", now)
    assert changed is True
    assert "005930" in s.monitored
    assert s.monitored["005930"].source == Source.MANUAL


def test_add_manual_toggle_removes():
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    s.add_manual("005930", now)
    changed, msg = s.add_manual("005930", now)
    assert changed is True
    assert "005930" not in s.monitored
    assert "해제" in msg


def test_add_manual_invalid_code():
    s = MonitoringSession()
    changed, msg = s.add_manual("12345", datetime.now())
    assert changed is False
    assert "잘못된" in msg


def test_add_manual_max_codes():
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    for i in range(MONITORING_MAX_CODES):
        s.add_manual(f"00000{i}"[-6:], now)
    changed, msg = s.add_manual("999999", now)
    assert changed is False
    assert "최대" in msg


def test_remove_manual_all_keeps_auto():
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    s.add_manual("005930", now)
    s.update_auto_leaders([{"code": "075180", "name": "제룡전기", "themes": ["전기/전선"]}], now)
    n, msg = s.remove_manual_all()
    assert n == 1
    assert "005930" not in s.monitored
    assert "075180" in s.monitored  # 자동은 유지


def test_toggle_pause():
    s = MonitoringSession()
    paused, _ = s.toggle_pause()
    assert paused is True
    assert s.paused is True
    paused, _ = s.toggle_pause()
    assert paused is False


def test_list_monitored_empty():
    s = MonitoringSession()
    assert "없음" in s.list_monitored()


def test_list_monitored_format():
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    s.add_manual("005930", now)
    s.update_auto_leaders([{"code": "075180", "name": "제룡전기", "themes": ["전기/전선"]}], now)
    listing = s.list_monitored()
    assert "005930" in listing
    assert "075180" in listing
    assert "제룡전기" in listing
    assert "자동" in listing
    assert "수동" in listing


# ── update_auto_leaders ──────────────────────────────────────────────────────


def test_update_auto_leaders_adds():
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    changes = s.update_auto_leaders([
        {"code": "075180", "name": "제룡전기", "themes": ["전기/전선"]},
    ], now)
    assert "075180" in s.monitored
    assert s.monitored["075180"].source == Source.AUTO
    assert s.monitored["075180"].themes == ["전기/전선"]
    assert any("제룡전기" in c for c in changes)


def test_update_auto_leaders_removes_dropped():
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    s.update_auto_leaders([{"code": "075180", "name": "제룡전기", "themes": ["전기/전선"]}], now)
    s.update_auto_leaders([{"code": "001440", "name": "대한전선", "themes": ["전기/전선"]}], now)
    assert "075180" not in s.monitored
    assert "001440" in s.monitored


def test_update_auto_leaders_keeps_manual():
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    s.add_manual("005930", now)
    s.update_auto_leaders([{"code": "075180", "name": "제룡전기", "themes": ["전기/전선"]}], now)
    # 수동 005930 은 자동 갱신과 무관하게 유지
    assert "005930" in s.monitored
    assert s.monitored["005930"].source == Source.MANUAL


# ── step_tracker 상태 머신 ───────────────────────────────────────────────────


def _stock(code: str, name: str, turnover: float) -> dict:
    return {"code": code, "name": name, "turnover": turnover}


def test_tracker_normal_initial():
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    a1 = _stock("A", "주도A", 18.0)
    alert = s.step_tracker("전기/전선", a1, candidate=None,
                           candidate_passed_transition_check=False, now=now)
    assert alert is None
    assert s.trackers["전기/전선"].state == LeaderState.NORMAL


def test_tracker_normal_to_transition():
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    a1 = _stock("A", "주도A", 18.0)
    s.step_tracker("전기/전선", a1, candidate=None,
                   candidate_passed_transition_check=False, now=now)
    a2 = _stock("B", "후보B", 12.0)
    alert = s.step_tracker("전기/전선", a1, candidate=a2,
                           candidate_passed_transition_check=True, now=now)
    assert alert is not None
    assert alert.kind == "transition"
    assert s.trackers["전기/전선"].state == LeaderState.TRANSITION


def test_tracker_transition_to_grace_on_overtake():
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    a1 = _stock("A", "주도A", 18.0)
    a2 = _stock("B", "후보B", 12.0)
    s.step_tracker("전기/전선", a1, None, False, now)
    s.step_tracker("전기/전선", a1, a2, True, now)
    # a2 회전율이 a1 추월
    a2_high = _stock("B", "후보B", 22.0)
    later = now + timedelta(minutes=2)
    alert = s.step_tracker("전기/전선", a1, a2_high, True, later)
    assert alert is not None
    assert alert.kind == "replacement"
    assert s.trackers["전기/전선"].state == LeaderState.GRACE


def test_tracker_grace_revert_on_a1_recovery():
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    a1 = _stock("A", "주도A", 18.0)
    a2 = _stock("B", "후보B", 12.0)
    s.step_tracker("전기/전선", a1, None, False, now)
    s.step_tracker("전기/전선", a1, a2, True, now)
    s.step_tracker("전기/전선", a1, _stock("B", "후보B", 22.0), True, now + timedelta(minutes=2))
    assert s.trackers["전기/전선"].state == LeaderState.GRACE
    # GRACE 중 a1 회복: a2 turnover < a1 turnover
    s.step_tracker("전기/전선", a1, _stock("B", "후보B", 10.0), True, now + timedelta(minutes=3))
    assert s.trackers["전기/전선"].state == LeaderState.NORMAL


def test_tracker_grace_period_completes():
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    a1 = _stock("A", "주도A", 18.0)
    a2 = _stock("B", "후보B", 22.0)
    s.step_tracker("전기/전선", a1, None, False, now)
    s.step_tracker("전기/전선", a1, _stock("B", "후보B", 12.0), True, now)
    s.step_tracker("전기/전선", a1, a2, True, now)  # 즉시 GRACE
    assert s.trackers["전기/전선"].state == LeaderState.GRACE
    later = now + timedelta(seconds=GRACE_PERIOD_SECONDS + 1)
    s.step_tracker("전기/전선", a1, a2, True, later)
    # GRACE 종료 → NORMAL, incumbent 가 a2 로 교체
    assert s.trackers["전기/전선"].state == LeaderState.NORMAL
    assert s.trackers["전기/전선"].incumbent_code == "B"


def test_tracker_transition_candidate_disappears():
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    a1 = _stock("A", "주도A", 18.0)
    a2 = _stock("B", "후보B", 12.0)
    s.step_tracker("전기/전선", a1, None, False, now)
    s.step_tracker("전기/전선", a1, a2, True, now)
    # 다음 스텝에서 candidate 사라짐
    s.step_tracker("전기/전선", a1, None, False, now + timedelta(minutes=1))
    assert s.trackers["전기/전선"].state == LeaderState.NORMAL


def test_tracker_transition_weak_persistence_drops_candidate():
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    a1 = _stock("A", "주도A", 18.0)
    s.step_tracker("전기/전선", a1, None, False, now)
    s.step_tracker("전기/전선", a1, _stock("B", "B", 12.0), True, now)
    # a2 회전율이 a1 × 0.4 = 7.2 미만으로 떨어짐
    weak_b = _stock("B", "B", 5.0)
    s.step_tracker("전기/전선", a1, weak_b, True, now + timedelta(seconds=10))
    # 아직 3분 미만
    assert s.trackers["전기/전선"].state == LeaderState.TRANSITION
    # 3분 지속
    s.step_tracker(
        "전기/전선", a1, weak_b, True,
        now + timedelta(seconds=10 + TRANSITION_EXIT_PERSIST_SECONDS + 1),
    )
    assert s.trackers["전기/전선"].state == LeaderState.NORMAL
