"""src.dashboard.state 단위 테스트 (round 35 multi-flag)."""
from __future__ import annotations

from datetime import datetime, timedelta

from src.dashboard.state import (
    LeaderState,
    MonitoredStock,
    MonitoringSession,
    Source,
    in_monitoring_window,
)
from src.scalping.score.thresholds import (
    GRACE_PERIOD_SECONDS,
    MONITORING_MAX_CODES,
    TRANSITION_EXIT_PERSIST_SECONDS,
)


# ── in_monitoring_window ─────────────────────────────────────────────────────


def test_in_window_business_day_within():
    assert in_monitoring_window(datetime(2026, 5, 11, 9, 30)) is True


def test_in_window_business_day_outside():
    assert in_monitoring_window(datetime(2026, 5, 11, 8, 59)) is False
    assert in_monitoring_window(datetime(2026, 5, 11, 10, 31)) is False


def test_in_window_weekend():
    assert in_monitoring_window(datetime(2026, 5, 9, 9, 30)) is False


# ── add_manual / 토글 (multi-flag) ───────────────────────────────────────────


def test_add_manual_basic():
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    changed, msg = s.add_manual("005930", now)
    assert changed is True
    assert "005930" in s.monitored
    assert s.monitored["005930"].is_manual is True
    assert s.monitored["005930"].is_auto is False


def test_add_manual_toggle_removes_flag():
    """다시 토글 시 is_manual 만 끔. flag 다 없으면 종목 entry 는 prune 대기."""
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    s.add_manual("005930", now)
    changed, msg = s.add_manual("005930", now)
    assert changed is True
    assert s.monitored["005930"].is_manual is False
    assert "해제" in msg


def test_add_manual_toggle_preserves_auto_flag():
    """자동 풀에 있는 종목에 manual 켰다 끄면 auto flag 는 살아남는다."""
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    s.update_auto_leaders([{"code": "075180", "name": "제룡전기", "themes": ["T"]}], now)
    assert s.monitored["075180"].is_auto is True
    # manual 켜기
    s.add_manual("075180", now)
    assert s.monitored["075180"].is_manual is True
    assert s.monitored["075180"].is_auto is True  # auto 도 살아있음
    # manual 끄기
    s.add_manual("075180", now)
    assert s.monitored["075180"].is_manual is False
    assert s.monitored["075180"].is_auto is True
    assert "075180" in s.monitored  # auto 가 있어 종목 entry 유지


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


def test_remove_manual_all_clears_flag_only():
    """/clear — 모든 manual flag 만 clear. 자동 flag 는 살아있어 종목 유지."""
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    s.add_manual("005930", now)  # manual only
    s.update_auto_leaders([{"code": "075180", "name": "제룡전기", "themes": ["T"]}], now)
    s.add_manual("075180", now)  # auto + manual
    n, msg = s.remove_manual_all()
    assert n == 2  # 005930 + 075180 둘 다 manual flag 해제
    # 005930 — manual 끄면 flag 없음 (prune 대상 — 본 함수는 prune 안 함)
    assert s.monitored["005930"].is_manual is False
    # 075180 — manual 끄지만 auto 살아있음
    assert s.monitored["075180"].is_manual is False
    assert s.monitored["075180"].is_auto is True


# ── update_rising_candidates ────────────────────────────────────────────────


def test_rising_basic_add():
    s = MonitoringSession()
    now = datetime(2026, 5, 13, 9, 30)
    changes = s.update_rising_candidates(
        [{"code": "012200", "name": "계양전기", "themes": []}], now,
    )
    assert "012200" in s.monitored
    assert s.monitored["012200"].is_rising is True
    assert any("012200" in c for c in changes)


def test_rising_persists_while_in_pool():
    s = MonitoringSession()
    t0 = datetime(2026, 5, 13, 9, 30)
    s.update_rising_candidates([{"code": "012200", "name": "계양", "themes": []}], t0)
    t1 = t0 + timedelta(minutes=5)
    s.update_rising_candidates([{"code": "012200", "name": "계양", "themes": []}], t1)
    assert s.monitored["012200"].is_rising is True


def test_rising_flag_off_when_dropped_from_pool():
    """풀에서 빠지면 is_rising flag 만 off — 다른 flag 없으면 prune 대상."""
    s = MonitoringSession()
    t0 = datetime(2026, 5, 13, 9, 30)
    s.update_rising_candidates([{"code": "012200", "name": "계양", "themes": []}], t0)
    s.update_rising_candidates([], t0 + timedelta(seconds=5))
    assert s.monitored["012200"].is_rising is False
    # 다른 flag 도 없으므로 prune 대상 — prune 호출 시 제거
    s.prune_empty(set())
    assert "012200" not in s.monitored


def test_rising_drop_does_not_affect_other_flags():
    """RISING 풀 동기화가 manual/auto flag 를 건드리지 않는다."""
    s = MonitoringSession()
    t0 = datetime(2026, 5, 13, 9, 30)
    s.add_manual("005930", t0)
    s.update_auto_leaders([{"code": "075180", "name": "제룡전기", "themes": ["T"]}], t0)
    s.update_rising_candidates([{"code": "012200", "name": "계양", "themes": []}], t0)
    s.update_rising_candidates([], t0 + timedelta(seconds=5))
    s.prune_empty(set())
    assert "005930" in s.monitored
    assert "075180" in s.monitored
    assert "012200" not in s.monitored


def test_manual_pin_survives_auto_pool_exit():
    """사용자가 자동 풀 종목에 manual 핀 박으면 풀 이탈 후에도 카드 유지 (사용자 요구 핵심)."""
    s = MonitoringSession()
    t0 = datetime(2026, 5, 13, 9, 30)
    s.update_auto_leaders([{"code": "075180", "name": "제룡전기", "themes": ["T"]}], t0)
    assert s.monitored["075180"].is_auto is True
    # 사용자 [→ 수동]
    s.add_manual("075180", t0 + timedelta(minutes=1))
    assert s.monitored["075180"].is_manual is True
    # 자동 풀에서 빠짐 (+29% 도달 시뮬)
    s.update_auto_leaders([], t0 + timedelta(minutes=2))
    assert s.monitored["075180"].is_auto is False
    assert s.monitored["075180"].is_manual is True
    s.prune_empty(set())
    assert "075180" in s.monitored  # manual 핀이라 prune 안 됨


def test_clear_manual_flag_helper():
    """청산 시 호출되는 헬퍼 — is_manual 만 off, 다른 flag 영향 X."""
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    s.add_manual("075180", now)
    s.update_auto_leaders([{"code": "075180", "name": "제룡", "themes": []}], now)
    assert s.clear_manual_flag("075180") is True
    assert s.monitored["075180"].is_manual is False
    assert s.monitored["075180"].is_auto is True
    # 두번째 호출 — 이미 꺼져있음
    assert s.clear_manual_flag("075180") is False


def test_ensure_held_stock():
    """보유 surface — monitored 에 없으면 entry 추가, flag 는 모두 false."""
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    m = s.ensure_held_stock("091340", "대한광통신", now)
    assert m.code == "091340"
    assert m.name == "대한광통신"
    assert m.has_any_flag() is False
    # 이미 있으면 name 만 갱신 (더 나은 이름이 들어오면)
    s.monitored["091340"].name = "091340"  # name=code 인 상태
    s.ensure_held_stock("091340", "대한광통신", now)
    assert s.monitored["091340"].name == "대한광통신"


def test_prune_empty():
    """flag 다 false 이고 보유도 아닌 종목 제거."""
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    # 케이스 1: flag 없음 + 보유 X → 제거
    s.ensure_held_stock("000001", "X", now)
    # 케이스 2: manual flag — 유지
    s.add_manual("000002", now)
    # 케이스 3: flag 없지만 holdings 에 있음 → 유지
    s.ensure_held_stock("000003", "Y", now)
    removed = s.prune_empty({"000003"})
    assert "000001" not in s.monitored
    assert "000002" in s.monitored
    assert "000003" in s.monitored
    assert any("000001" in r for r in removed)


def test_set_on_off_idempotent():
    s = MonitoringSession()
    assert s.paused is False
    changed, msg = s.set_on()
    assert changed is False
    assert "이미" in msg
    changed, msg = s.set_off()
    assert changed is True
    assert s.paused is True
    changed, _ = s.set_off()
    assert changed is False
    changed, _ = s.set_on()
    assert changed is True


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
    assert s.monitored["075180"].is_auto is True
    assert s.monitored["075180"].themes == ["전기/전선"]
    assert any("제룡전기" in c for c in changes)


def test_update_auto_leaders_drops_flag_only():
    """자동 풀에서 빠지면 is_auto flag 만 off — 종목 entry 는 prune 단계에서 결정."""
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    s.update_auto_leaders([{"code": "075180", "name": "제룡전기", "themes": ["T"]}], now)
    s.update_auto_leaders([{"code": "001440", "name": "대한전선", "themes": ["T"]}], now)
    # 075180 는 entry 남아있지만 is_auto false
    assert s.monitored["075180"].is_auto is False
    assert s.monitored["001440"].is_auto is True
    # prune 시 075180 사라짐
    s.prune_empty(set())
    assert "075180" not in s.monitored
    assert "001440" in s.monitored


def test_update_auto_leaders_keeps_manual_pin():
    """자동 갱신이 수동 핀을 건드리지 않는다."""
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    s.add_manual("005930", now)
    s.update_auto_leaders([{"code": "075180", "name": "제룡전기", "themes": ["T"]}], now)
    assert s.monitored["005930"].is_manual is True
    assert s.monitored["005930"].is_auto is False


# ── step_tracker 상태 머신 ───────────────────────────────────────────────────


def _ticker(code: str, name: str, turnover: float) -> dict:
    return {"code": code, "name": name, "turnover": turnover}


def test_tracker_normal_initial():
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    a1 = _ticker("A", "주도A", 18.0)
    s.step_tracker("전기/전선", a1, candidate=None,
                   candidate_passed_transition_check=False, now=now)
    assert s.trackers["전기/전선"].state == LeaderState.NORMAL


def test_tracker_normal_to_transition():
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    a1 = _ticker("A", "주도A", 18.0)
    s.step_tracker("전기/전선", a1, candidate=None,
                   candidate_passed_transition_check=False, now=now)
    a2 = _ticker("B", "후보B", 12.0)
    s.step_tracker("전기/전선", a1, candidate=a2,
                   candidate_passed_transition_check=True, now=now)
    tracker = s.trackers["전기/전선"]
    assert tracker.state == LeaderState.TRANSITION
    assert tracker.candidate_code == "B"


def test_tracker_transition_to_grace_on_overtake():
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    a1 = _ticker("A", "주도A", 18.0)
    a2 = _ticker("B", "후보B", 12.0)
    s.step_tracker("전기/전선", a1, None, False, now)
    s.step_tracker("전기/전선", a1, a2, True, now)
    a2_high = _ticker("B", "후보B", 22.0)
    later = now + timedelta(minutes=2)
    s.step_tracker("전기/전선", a1, a2_high, True, later)
    assert s.trackers["전기/전선"].state == LeaderState.GRACE


def test_tracker_grace_revert_on_a1_recovery():
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    a1 = _ticker("A", "주도A", 18.0)
    a2 = _ticker("B", "후보B", 12.0)
    s.step_tracker("전기/전선", a1, None, False, now)
    s.step_tracker("전기/전선", a1, a2, True, now)
    s.step_tracker("전기/전선", a1, _ticker("B", "후보B", 22.0), True, now + timedelta(minutes=2))
    assert s.trackers["전기/전선"].state == LeaderState.GRACE
    s.step_tracker("전기/전선", a1, _ticker("B", "후보B", 10.0), True, now + timedelta(minutes=3))
    assert s.trackers["전기/전선"].state == LeaderState.NORMAL


def test_tracker_grace_period_completes():
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    a1 = _ticker("A", "주도A", 18.0)
    a2 = _ticker("B", "후보B", 22.0)
    s.step_tracker("전기/전선", a1, None, False, now)
    s.step_tracker("전기/전선", a1, _ticker("B", "후보B", 12.0), True, now)
    s.step_tracker("전기/전선", a1, a2, True, now)
    assert s.trackers["전기/전선"].state == LeaderState.GRACE
    later = now + timedelta(seconds=GRACE_PERIOD_SECONDS + 1)
    s.step_tracker("전기/전선", a1, a2, True, later)
    assert s.trackers["전기/전선"].state == LeaderState.NORMAL
    assert s.trackers["전기/전선"].incumbent_code == "B"


def test_tracker_transition_candidate_disappears():
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    a1 = _ticker("A", "주도A", 18.0)
    a2 = _ticker("B", "후보B", 12.0)
    s.step_tracker("전기/전선", a1, None, False, now)
    s.step_tracker("전기/전선", a1, a2, True, now)
    s.step_tracker("전기/전선", a1, None, False, now + timedelta(minutes=1))
    assert s.trackers["전기/전선"].state == LeaderState.NORMAL


def test_tracker_transition_weak_persistence_drops_candidate():
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    a1 = _ticker("A", "주도A", 18.0)
    s.step_tracker("전기/전선", a1, None, False, now)
    s.step_tracker("전기/전선", a1, _ticker("B", "B", 12.0), True, now)
    weak_b = _ticker("B", "B", 5.0)
    s.step_tracker("전기/전선", a1, weak_b, True, now + timedelta(seconds=10))
    assert s.trackers["전기/전선"].state == LeaderState.TRANSITION
    s.step_tracker(
        "전기/전선", a1, weak_b, True,
        now + timedelta(seconds=10 + TRANSITION_EXIT_PERSIST_SECONDS + 1),
    )
    assert s.trackers["전기/전선"].state == LeaderState.NORMAL


# ── update_investor_delta — round 36 후속 ────────────────────────────────────

def _investor(foreign_v=0, inst_v=0, program=0):
    """fetch_investor_flow 결과 흉내 — 최소 키만."""
    return {
        "foreign_net_buy_value": foreign_v,
        "institution_net_buy_value": inst_v,
        "program_net_buy": program,
        "foreign_net_buy": foreign_v // 100,
        "institution_net_buy": inst_v // 100,
        "individual_net_buy": 0,
    }


def test_investor_delta_none_when_input_none():
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    assert s.update_investor_delta("075180", None, now) is None


def test_investor_delta_none_on_first_call():
    """첫 호출은 비교 대상 없음 → snapshot 박히고 Δ 없음."""
    s = MonitoringSession()
    now = datetime(2026, 5, 11, 9, 30)
    result = s.update_investor_delta("075180", _investor(foreign_v=1_000_000_000), now)
    assert result is None
    assert "075180" in s.last_investor_snapshots


def test_investor_delta_none_when_value_unchanged():
    """두 번째 호출 값 그대로면 Δ 갱신 X, 이전 Δ 도 없으니 None."""
    s = MonitoringSession()
    t0 = datetime(2026, 5, 11, 9, 30)
    t1 = datetime(2026, 5, 11, 9, 30, 15)
    inv = _investor(foreign_v=1_000_000_000, inst_v=500_000_000, program=10_000)
    s.update_investor_delta("075180", inv, t0)
    result = s.update_investor_delta("075180", inv, t1)
    assert result is None


def test_investor_delta_recorded_on_change():
    """값 바뀌면 Δ 기록 + elapsed=0 (changed_at=now)."""
    s = MonitoringSession()
    t0 = datetime(2026, 5, 11, 9, 30)
    t1 = datetime(2026, 5, 11, 9, 35)
    s.update_investor_delta(
        "075180",
        _investor(foreign_v=1_000_000_000, inst_v=500_000_000, program=10_000),
        t0,
    )
    result = s.update_investor_delta(
        "075180",
        _investor(foreign_v=1_300_000_000, inst_v=400_000_000, program=12_500),
        t1,
    )
    assert result is not None
    assert result["foreign_value"] == 300_000_000
    assert result["institution_value"] == -100_000_000
    assert result["program_qty"] == 2_500
    assert result["elapsed_sec"] == 0


def test_investor_delta_elapsed_grows_when_value_persists():
    """Δ 박힌 후 같은 값이 계속 와도 elapsed 가 늘어남 (KIS 5분 유지 시나리오)."""
    s = MonitoringSession()
    t0 = datetime(2026, 5, 11, 9, 30)
    t1 = datetime(2026, 5, 11, 9, 31)
    t2 = datetime(2026, 5, 11, 9, 33, 47)
    inv1 = _investor(foreign_v=1_000_000_000)
    inv2 = _investor(foreign_v=1_500_000_000)
    s.update_investor_delta("075180", inv1, t0)
    r1 = s.update_investor_delta("075180", inv2, t1)
    assert r1["foreign_value"] == 500_000_000
    assert r1["elapsed_sec"] == 0
    r2 = s.update_investor_delta("075180", inv2, t2)
    assert r2["foreign_value"] == 500_000_000
    assert r2["elapsed_sec"] == int((t2 - t1).total_seconds())


def test_investor_delta_new_change_resets_elapsed():
    """또 다른 변화가 오면 Δ 갱신 + elapsed=0 reset."""
    s = MonitoringSession()
    t0 = datetime(2026, 5, 11, 9, 30)
    t1 = datetime(2026, 5, 11, 9, 35)
    t2 = datetime(2026, 5, 11, 9, 40)
    s.update_investor_delta("075180", _investor(foreign_v=1_000_000_000), t0)
    s.update_investor_delta("075180", _investor(foreign_v=1_500_000_000), t1)
    r = s.update_investor_delta("075180", _investor(foreign_v=2_000_000_000), t2)
    assert r["foreign_value"] == 500_000_000  # t1→t2 변화
    assert r["elapsed_sec"] == 0


def test_investor_delta_independent_per_code():
    """종목별로 snapshot/delta 가 격리됨."""
    s = MonitoringSession()
    t0 = datetime(2026, 5, 11, 9, 30)
    t1 = datetime(2026, 5, 11, 9, 35)
    s.update_investor_delta("075180", _investor(foreign_v=1_000_000_000), t0)
    s.update_investor_delta("091340", _investor(foreign_v=500_000_000), t0)
    r1 = s.update_investor_delta("075180", _investor(foreign_v=1_200_000_000), t1)
    r2 = s.update_investor_delta("091340", _investor(foreign_v=500_000_000), t1)
    assert r1["foreign_value"] == 200_000_000
    assert r2 is None  # 091340 은 변화 없음


# ── 단저단고 surface 룰 + mr_history (2026-05-29) ────────────────────────────


def _make_stock(code: str = "100001") -> MonitoredStock:
    return MonitoredStock(code=code, name=f"stock_{code}", added_at=datetime(2026, 5, 29, 9, 30))


def test_push_mr_event_appends_new_kind():
    m = _make_stock()
    t0 = datetime(2026, 5, 29, 9, 30)
    m.push_mr_event(t0, "STRONG단저", 1.59, "강망치 진폭 1.59%")
    assert len(m.mr_history) == 1
    assert m.mr_history[0].kind == "STRONG단저"
    assert m.mr_history[0].score == 1.59
    # 폐기된 kind 는 무시
    m.push_mr_event(t0, "단고", 1.0, "x")
    assert len(m.mr_history) == 1


def test_push_mr_event_same_kind_updates_in_place():
    """연속 동일 kind 발화는 prepend X, score/reason 갱신만."""
    m = _make_stock()
    t0 = datetime(2026, 5, 29, 9, 30, 0)
    t1 = datetime(2026, 5, 29, 9, 30, 3)
    m.push_mr_event(t0, "STRONG단저", 1.0, "진폭 1.0%")
    m.push_mr_event(t1, "STRONG단저", 1.5, "진폭 1.5%")
    assert len(m.mr_history) == 1
    assert m.mr_history[0].score == 1.5
    assert m.mr_history[0].ts == t1


def test_push_mr_event_kind_change_prepends():
    m = _make_stock()
    t0 = datetime(2026, 5, 29, 9, 30, 0)
    t1 = datetime(2026, 5, 29, 9, 31, 0)
    m.push_mr_event(t0, "STRONG단저", 1.5, "진폭 1.5%")
    m.push_mr_event(t1, "청산", 1.0, "trailing -1.0%")
    assert len(m.mr_history) == 2
    assert m.mr_history[0].kind == "청산"      # 최신
    assert m.mr_history[1].kind == "STRONG단저"


def test_push_mr_event_fifo_max_three():
    m = _make_stock()
    for i in range(5):
        kind = "STRONG단저" if i % 2 == 0 else "청산"
        m.push_mr_event(
            datetime(2026, 5, 29, 9, 30 + i),
            kind, float(i), f"score{i}",
        )
    assert len(m.mr_history) == 3
    # 가장 최신 (i=4, kind="STRONG단저", score=4.0) 이 [0]
    assert m.mr_history[0].score == 4.0
    assert m.mr_history[2].score == 2.0


def test_update_auto_leaders_sets_sector_role():
    """leaders + candidates 합쳐서 받으면 sector_role / surface_sector_name 설정."""
    s = MonitoringSession()
    t0 = datetime(2026, 5, 29, 9, 30)
    entries = [
        {"code": "100001", "name": "leader_stock", "themes": ["섹터A"],
         "sector_role": "leader", "surface_sector_name": "섹터A"},
        {"code": "100002", "name": "cand_stock", "themes": ["섹터A"],
         "sector_role": "candidate", "surface_sector_name": "섹터A"},
    ]
    s.update_auto_leaders(entries, t0)
    assert s.monitored["100001"].is_auto
    assert s.monitored["100001"].sector_role == "leader"
    assert s.monitored["100001"].surface_sector_name == "섹터A"
    assert s.monitored["100002"].sector_role == "candidate"


def test_update_auto_leaders_drops_role_when_out_of_pool():
    """다음 tick 에서 풀 이탈 시 is_auto False + sector_role/surface_sector_name None."""
    s = MonitoringSession()
    t0 = datetime(2026, 5, 29, 9, 30)
    s.update_auto_leaders(
        [{"code": "100001", "name": "x", "themes": ["섹터A"],
          "sector_role": "leader", "surface_sector_name": "섹터A"}],
        t0,
    )
    s.update_auto_leaders([], t0 + timedelta(seconds=3))
    m = s.monitored["100001"]
    assert not m.is_auto
    assert m.sector_role is None
    assert m.surface_sector_name is None


def test_mr_alert_kind_default_none():
    """신규 종목의 mr_alert_kind 는 None (아직 push X)."""
    m = _make_stock()
    assert m.mr_alert_kind is None
