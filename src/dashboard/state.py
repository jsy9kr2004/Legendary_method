"""실시간 모니터링 상태 머신 (M6).

핵심 책임:
    - 모니터링 대상 종목 set 관리 — multi-flag 모델 (round 35)
    - 주도주 교체 상태 머신: NORMAL → TRANSITION → GRACE → NORMAL (카드 헤더에 통합)
    - 5분 GRACE 유예기 카운트다운
    - 사용자 명령 처리 (/pause, /list, /clear, 6자리 코드 토글)

데이터 모델 (round 35):
    `MonitoredStock` 은 한 종목에 대한 카드. 4 상태 flag 가 동시에 켜질 수 있다:
        - is_auto: 시스템 — 주도섹터 회전율 1위. 매 tick 갱신
        - is_rising: 시스템 — Buy.Score score 통과 부상 후보. 매 tick 갱신
        - is_manual: 사용자 핀 — 자동/후보 풀에서 빠져도 카드 유지
        - HOLD: holdings.json 에서 derived (state 에 저장 X — worker 가 매 tick 결정)

    모든 flag 가 false 이고 보유도 아니면 monitored 에서 제거 (worker 가 prune).

    이전 (single source: Source enum) 의 "AUTO 가 +29% 도달 시 manual 잠금" 같은
    승격 동작은 더 이상 자동으로 일어나지 않는다. 사용자가 명시적으로 [→ 수동]
    버튼 (또는 6자리 코드 토글) 으로 is_manual 을 켜야 자동 풀에서 빠져도 유지.

I/O 분리:
    본 모듈은 pure — 시각(now)을 인자로 받고 상태만 갱신.
    실제 텔레그램 발송/분봉 fetch 는 worker (`src/dashboard/worker.py`).
    holdings.json 접근도 worker — state.py 는 holdings 모름.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from enum import Enum
from typing import Any

from loguru import logger

from src.calendar_kr import is_business_day
from src.scalping.score.thresholds import (
    GRACE_PERIOD_SECONDS,
    MONITORING_END_HOUR,
    MONITORING_END_MINUTE,
    MONITORING_MAX_CODES,
    MONITORING_START_HOUR,
    MONITORING_START_MINUTE,
    TRANSITION_EXIT_PERSIST_SECONDS,
    TRANSITION_EXIT_TURNOVER_RATIO,
)


class Source(str, Enum):
    """Backward-compat enum. 카드 좌측 보더 색상 결정 시 derive 용으로만 사용.

    multi-flag 모델 (round 35) 이후엔 MonitoredStock 에 저장하지 않는다.
    `MonitoredStock.primary_source(is_held)` 로 우선순위 derive.
    """
    AUTO = "auto"
    MANUAL = "manual"
    RISING = "rising"
    HOLD = "hold"


class LeaderState(str, Enum):
    NORMAL = "normal"
    TRANSITION = "transition"
    GRACE = "grace"


@dataclass
class MonitoredStock:
    """한 종목의 모니터링 카드. 4 상태 flag 동시 ON 가능 (round 35).

    - is_auto / is_rising: 시스템이 매 tick 갱신.
    - is_manual: 사용자 명시 핀.
    - HOLD: holdings.json 기반 derived — 본 dataclass 에는 없음.
      worker 가 build_payload 시 외부 인자로 전달.
    """
    code: str
    name: str
    added_at: datetime
    is_auto: bool = False
    is_rising: bool = False
    is_manual: bool = False
    message_id: int | None = None
    themes: list[str] = field(default_factory=list)
    buy_score: float | None = None
    buy_grade: str | None = None
    buy_reasons: list[str] = field(default_factory=list)
    # 매매법 분류 (P1-4, docs §11.1) — 현재 로깅 전용, 카드 표시는 검증 후.
    setup_label: str | None = None        # breakout / pullback / chase / none
    setup_score_breakout: float | None = None
    setup_score_pullback: float | None = None
    setup_chase_warning: bool = False
    # 단저단고 시그널 (docs/scalping-redesign-2026-05-27.md, 2026-05-27).
    # 매 tick 봉 단위 분석으로 마지막 봉의 sigB/sigS 갱신. 카드 dry-run 표시.
    mr_sigB: bool = False
    mr_sigS: bool = False
    mr_reason: str | None = None  # 발화 사유 (예: "단저 STOCH 28 / RSI 35")
    # 시장 폭(breadth) — 국면 게이지 (P2-7). tick 마다 동일값 (시장 레벨).
    market_breadth_up_frac: float | None = None
    market_n_up5: int | None = None

    def has_any_flag(self) -> bool:
        """auto/rising/manual 중 하나라도 켜져 있는지. HOLD 는 별도."""
        return self.is_auto or self.is_rising or self.is_manual

    def primary_source(self, is_held: bool = False) -> Source:
        """카드 좌측 보더 색상용 우선순위 — HOLD > MANUAL > AUTO > RISING."""
        if is_held:
            return Source.HOLD
        if self.is_manual:
            return Source.MANUAL
        if self.is_auto:
            return Source.AUTO
        if self.is_rising:
            return Source.RISING
        # 어떤 flag 도 없으면 보유로 surface 된 케이스 — 호출자가 is_held=True 줘야
        return Source.HOLD


@dataclass
class LeaderTracker:
    """주도섹터 1개에 대한 a1/a2 + 상태 머신."""
    sector: str
    incumbent_code: str
    incumbent_turnover: float = 0.0
    candidate_code: str | None = None
    candidate_turnover: float = 0.0
    state: LeaderState = LeaderState.NORMAL
    state_entered_at: datetime | None = None
    transition_weak_since: datetime | None = None


def in_monitoring_window(now: datetime) -> bool:
    """평일 09:00~10:30 인지. 휴장일은 항상 False."""
    if not is_business_day(now.date()):
        return False
    start = time(MONITORING_START_HOUR, MONITORING_START_MINUTE)
    end = time(MONITORING_END_HOUR, MONITORING_END_MINUTE)
    return start <= now.time() <= end


@dataclass
class MonitoringSession:
    """대시보드 한 세션의 전체 상태."""
    paused: bool = False
    force_on: bool = False
    monitored: dict[str, MonitoredStock] = field(default_factory=dict)
    trackers: dict[str, LeaderTracker] = field(default_factory=dict)
    off_cleanup_pending: bool = False
    last_prices: dict[str, float] = field(default_factory=dict)
    vp_series: dict[str, Any] = field(default_factory=dict)
    limit_up_hit_times: dict[str, time] = field(default_factory=dict)
    last_payloads: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_payload_ts: datetime | None = None
    # round 36 후속: 외인/기관/프로그램 누적값이 마지막으로 바뀐 시점 추적.
    # 윈도우 고정(1m/5m) 대신 KIS 갱신 주기에 자동 적응 — 응답값이 이전 호출과
    # 다르면 그 시점에서 Δ 기록, 같으면 snapshot 갱신 X (카드 Δ 라인의 elapsed
    # 가 늘어남). 종배 14:50 결정 레포트는 이 추적 안 함 (스냅샷 1회).
    last_investor_snapshots: dict[str, tuple[datetime, dict[str, Any]]] = field(default_factory=dict)
    last_investor_deltas: dict[str, dict[str, Any]] = field(default_factory=dict)

    # ── 종목 추가/제거 (사용자 토글) ──────────────────────────────────────────

    def add_manual(self, code: str, now: datetime) -> tuple[bool, str]:
        """6자리 코드 토글 — is_manual flag 만 켜고/끄기.

        (round 35) 자동/후보 → 수동 "승격" 개념 폐기. flag 가 공존이라 자동/후보
        풀에 있는 종목에 manual 을 켜면 둘 다 표시. 끄면 manual 만 빠지고 자동/
        후보 flag 가 살아있으면 카드 유지.
        """
        code = code.strip()
        if len(code) != 6 or not code.isdigit():
            return False, f"잘못된 종목코드: {code} (6자리 숫자 필요)"

        if code in self.monitored:
            m = self.monitored[code]
            if m.is_manual:
                m.is_manual = False
                if m.has_any_flag():
                    return True, f"× {code} {m.name} — 수동 해제 (자동/후보 flag 유지)"
                # flag 다 없음 — worker 가 holdings 확인 후 prune 결정
                return True, f"× {code} {m.name} — 수동 해제"
            else:
                m.is_manual = True
                m.added_at = now
                return True, f"🔵 {code} {m.name} — 수동 핀 (자동/후보 풀 이탈해도 유지)"

        # 신규 종목 — manual 슬롯 한도 체크
        manual_count = sum(1 for m in self.monitored.values() if m.is_manual)
        if manual_count >= MONITORING_MAX_CODES:
            return False, f"⚠ 수동 모니터링 최대 {MONITORING_MAX_CODES}개"

        self.monitored[code] = MonitoredStock(
            code=code, name=code, added_at=now, is_manual=True,
        )
        return True, f"🔵 {code} — 수동 모니터링 추가"

    def clear_manual_flag(self, code: str) -> bool:
        """특정 종목의 is_manual 만 끄기. 다른 flag 영향 X. 청산 시 호출.

        Returns:
            True 면 flag 가 켜져있다가 꺼졌음. False 면 변화 없음.
        """
        m = self.monitored.get(code)
        if m is None or not m.is_manual:
            return False
        m.is_manual = False
        return True

    def remove_manual_all(self) -> tuple[int, str]:
        """/clear — 모든 종목의 is_manual flag clear.

        다른 flag (자동/후보) 또는 보유 여부는 worker prune 이 판단해서 카드 유지.
        """
        cleared = 0
        for m in self.monitored.values():
            if m.is_manual:
                m.is_manual = False
                cleared += 1
        return cleared, f"🧹 수동 핀 {cleared}개 해제. 자동/후보/보유는 유지."

    def list_monitored(self) -> str:
        """/list — 현재 모니터링 종목. flag 조합으로 표시."""
        if not self.monitored:
            return "📋 모니터링 중인 종목 없음."
        lines = [f"📋 [현재 모니터링 — {len(self.monitored)}개]"]
        for m in self.monitored.values():
            flags = []
            if m.is_manual:
                flags.append("🔵수동")
            if m.is_auto:
                flags.append("⭐자동")
            if m.is_rising:
                flags.append("⚡후보")
            label = " / ".join(flags) if flags else "(no flag)"
            themes = " / ".join(m.themes) if m.themes else "—"
            lines.append(f"  • {m.code} {m.name} [{label}] ({themes})")
        return "\n".join(lines)

    def set_on(self) -> tuple[bool, str]:
        """/on /start — 모니터링 ON (멱등)."""
        if not self.paused:
            return False, "▶ 이미 모니터링 ON 상태"
        self.paused = False
        return True, "▶ 모니터링 ON — 카드 갱신 시작"

    def set_off(self) -> tuple[bool, str]:
        """/off — 모니터링 OFF (멱등)."""
        if self.paused:
            return False, "⏸ 이미 모니터링 OFF 상태"
        self.paused = True
        self.off_cleanup_pending = True
        return True, "⏸ 모니터링 OFF — /on 으로 재개 (다음 평일 09:00 자동 ON)"

    # ── 자동 주도주 (시스템) ──────────────────────────────────────────────────

    def update_auto_leaders(
        self,
        leaders: list[dict[str, Any]],
        now: datetime,
    ) -> list[str]:
        """주도주 풀로 is_auto flag 동기화.

        (round 35) 이전엔 source=AUTO 종목을 통째로 갈아엎었으나, 이제는 flag
        만 set/clear. 사용자가 [→ 수동] 으로 is_manual 켜둔 종목이 자동 풀에서
        빠져도 카드 유지.
        """
        changes: list[str] = []
        new_codes = {l["code"] for l in leaders}

        # 기존 is_auto 중 풀에서 빠진 것 — flag 만 off
        for code, m in self.monitored.items():
            if m.is_auto and code not in new_codes:
                m.is_auto = False
                changes.append(f"⭐→ {code} {m.name} 자동 풀 이탈")

        # 새 leaders — is_auto flag set + 신규 종목이면 entry 추가
        for ld in leaders:
            code = ld["code"]
            new_themes = list(ld.get("themes", []))
            if code in self.monitored:
                m = self.monitored[code]
                if not m.is_auto:
                    changes.append(f"⭐ {ld.get('name', code)} ({code}) 자동 진입")
                m.is_auto = True
                if new_themes:
                    m.themes = list(set(m.themes + new_themes))
            else:
                self.monitored[code] = MonitoredStock(
                    code=code, name=ld.get("name", code),
                    added_at=now, is_auto=True, themes=new_themes,
                )
                changes.append(f"⭐ 자동 추가: {ld.get('name', code)} ({code})")

        return changes

    # ── 부상 후보 (시스템) ────────────────────────────────────────────────────

    def update_rising_candidates(
        self,
        candidates: list[dict[str, Any]],
        now: datetime,
        max_count: int = 5,
    ) -> list[str]:
        """부상 후보 풀로 is_rising flag 동기화.

        (round 35) 풀 상위 max_count 만 is_rising. 풀에서 빠진 종목은 flag off.
        다른 flag (manual/auto/hold) 가 있으면 카드 유지.
        """
        changes: list[str] = []
        pool_codes_set = {c["code"] for c in candidates}

        # 풀 이탈 — is_rising flag off
        for code, m in self.monitored.items():
            if m.is_rising and code not in pool_codes_set:
                m.is_rising = False
                changes.append(f"💤 {m.name} ({code}) 후보 풀 이탈")

        # 풀 상위 max_count 까지 is_rising 켜기
        added = 0
        for cand in candidates:
            if added >= max_count and cand["code"] not in self.monitored:
                continue
            code = cand["code"]
            buy_score = cand.get("buy_score")
            buy_grade = cand.get("buy_grade")
            buy_reasons = cand.get("buy_reasons") or []
            new_themes = list(cand.get("themes", []))
            if code in self.monitored:
                m = self.monitored[code]
                if not m.is_rising:
                    score_str = f" [{buy_grade} {buy_score:+.1f}]" if buy_score is not None else ""
                    changes.append(f"⚡ {m.name} ({code}) 후보 진입{score_str}")
                m.is_rising = True
                if new_themes:
                    m.themes = list(set(m.themes + new_themes))
                if buy_score is not None:
                    m.buy_score = buy_score
                    m.buy_grade = buy_grade
                    m.buy_reasons = list(buy_reasons)
            else:
                self.monitored[code] = MonitoredStock(
                    code=code, name=cand.get("name", code),
                    added_at=now, is_rising=True, themes=new_themes,
                    buy_score=buy_score, buy_grade=buy_grade,
                    buy_reasons=list(buy_reasons),
                )
                score_str = f" [{buy_grade} {buy_score:+.1f}]" if buy_score is not None else ""
                changes.append(f"⚡ {cand.get('name', code)} ({code}) 후보 신규{score_str}")
                added += 1
        return changes

    # ── HOLD surface (worker 가 holdings.json 기반 호출) ──────────────────────

    def ensure_held_stock(
        self,
        code: str,
        name: str,
        now: datetime,
    ) -> MonitoredStock:
        """보유 종목이 monitored 에 없으면 entry 만 추가. flag 는 모두 false.

        보유 종목 카드 유지는 worker prune 의 holding 인자로 결정 (`prune_empty`).
        is_hold 같은 명시 flag 는 두지 않음 — derive 가 source of truth (holdings.json).
        """
        if code in self.monitored:
            m = self.monitored[code]
            if name and m.name == m.code:
                m.name = name  # 더 나은 이름이 들어오면 갱신
            return m
        m = MonitoredStock(
            code=code, name=name or code, added_at=now,
        )
        self.monitored[code] = m
        return m

    def prune_empty(self, holding_codes: set[str]) -> list[str]:
        """flag 가 모두 false 이고 보유도 아닌 종목 제거.

        Args:
            holding_codes: holdings.json 의 종목 코드 set.

        Returns:
            제거된 (code, name) 의 한 줄 메시지 list.
        """
        removed: list[str] = []
        for code in list(self.monitored.keys()):
            m = self.monitored[code]
            if m.has_any_flag():
                continue
            if code in holding_codes:
                continue
            self.monitored.pop(code)
            removed.append(f"💤 {m.name} ({code}) 카드 제거 (flag 없음 + 보유 아님)")
        return removed

    # ── 외인/기관/프로그램 수급 Δ 추적 (round 36 후속) ────────────────────────

    _INVESTOR_DELTA_KEYS = (
        "foreign_net_buy_value",
        "institution_net_buy_value",
        "program_net_buy",
    )

    def update_investor_delta(
        self,
        code: str,
        investor: dict[str, Any] | None,
        now: datetime,
    ) -> dict[str, Any] | None:
        """누적값이 마지막으로 바뀐 시점 추적 + Δ + elapsed_sec 반환.

        윈도우 고정(1m/5m) 대신 응답값이 이전과 다른 순간을 잡음 — KIS 갱신
        주기에 자동 적응. KIS 가 5분마다 바꾸든 1분마다 바꾸든 카드 Δ 라인의
        elapsed 가 그대로 갱신 주기를 노출한다.

        - investor=None 또는 첫 호출 (snapshot 없음): None 반환
        - 이전 snapshot 과 동일: snapshot 갱신 X, 마지막 Δ + 늘어난 elapsed 반환
        - 이전과 다름: 새 Δ 기록 + snapshot 갱신
        """
        if investor is not None:
            prev = self.last_investor_snapshots.get(code)
            if prev is None:
                # 첫 호출 — 비교 대상 없으니 snapshot 만 박고 Δ 없음.
                self.last_investor_snapshots[code] = (now, dict(investor))
            else:
                _, prev_val = prev
                if any(investor.get(k) != prev_val.get(k) for k in self._INVESTOR_DELTA_KEYS):
                    self.last_investor_deltas[code] = {
                        "foreign_value": (investor.get("foreign_net_buy_value") or 0)
                            - (prev_val.get("foreign_net_buy_value") or 0),
                        "institution_value": (investor.get("institution_net_buy_value") or 0)
                            - (prev_val.get("institution_net_buy_value") or 0),
                        "program_qty": (investor.get("program_net_buy") or 0)
                            - (prev_val.get("program_net_buy") or 0),
                        "changed_at": now,
                    }
                    self.last_investor_snapshots[code] = (now, dict(investor))

        last_delta = self.last_investor_deltas.get(code)
        if last_delta is None:
            return None
        return {
            "foreign_value": last_delta["foreign_value"],
            "institution_value": last_delta["institution_value"],
            "program_qty": last_delta["program_qty"],
            "elapsed_sec": int((now - last_delta["changed_at"]).total_seconds()),
        }

    # ── LeaderTracker 상태 머신 ──────────────────────────────────────────────

    def step_tracker(
        self,
        sector: str,
        incumbent: dict[str, Any],
        candidate: dict[str, Any] | None,
        candidate_passed_transition_check: bool,
        now: datetime,
    ) -> None:
        """섹터별 상태 머신 한 스텝."""
        tracker = self.trackers.get(sector)
        if tracker is None:
            tracker = LeaderTracker(
                sector=sector,
                incumbent_code=incumbent["code"],
                incumbent_turnover=float(incumbent.get("turnover", 0.0)),
                state_entered_at=now,
            )
            self.trackers[sector] = tracker
            return

        if tracker.incumbent_code != incumbent["code"] and tracker.state == LeaderState.NORMAL:
            tracker.incumbent_code = incumbent["code"]
            tracker.incumbent_turnover = float(incumbent.get("turnover", 0.0))
            tracker.state_entered_at = now
            return

        tracker.incumbent_turnover = float(incumbent.get("turnover", 0.0))

        if tracker.state == LeaderState.NORMAL:
            if candidate and candidate_passed_transition_check:
                tracker.state = LeaderState.TRANSITION
                tracker.candidate_code = candidate["code"]
                tracker.candidate_turnover = float(candidate.get("turnover", 0.0))
                tracker.state_entered_at = now
                tracker.transition_weak_since = None
            return

        if tracker.state == LeaderState.TRANSITION:
            if candidate is None or candidate["code"] != tracker.candidate_code:
                tracker.state = LeaderState.NORMAL
                tracker.candidate_code = None
                tracker.candidate_turnover = 0.0
                tracker.transition_weak_since = None
                return

            tracker.candidate_turnover = float(candidate.get("turnover", 0.0))

            if tracker.candidate_turnover > tracker.incumbent_turnover:
                tracker.state = LeaderState.GRACE
                tracker.state_entered_at = now
                tracker.transition_weak_since = None
                return

            if (
                tracker.incumbent_turnover > 0
                and tracker.candidate_turnover
                < tracker.incumbent_turnover * TRANSITION_EXIT_TURNOVER_RATIO
            ):
                if tracker.transition_weak_since is None:
                    tracker.transition_weak_since = now
                elif (now - tracker.transition_weak_since).total_seconds() >= TRANSITION_EXIT_PERSIST_SECONDS:
                    tracker.state = LeaderState.NORMAL
                    tracker.candidate_code = None
                    tracker.candidate_turnover = 0.0
                    tracker.transition_weak_since = None
            else:
                tracker.transition_weak_since = None
            return

        if tracker.state == LeaderState.GRACE:
            assert tracker.state_entered_at is not None
            elapsed = (now - tracker.state_entered_at).total_seconds()

            if (
                candidate is None
                or candidate["code"] != tracker.candidate_code
                or float(candidate.get("turnover", 0.0)) < tracker.incumbent_turnover
            ):
                tracker.state = LeaderState.NORMAL
                tracker.candidate_code = None
                tracker.candidate_turnover = 0.0
                tracker.state_entered_at = now
                return

            tracker.candidate_turnover = float(candidate.get("turnover", 0.0))

            if elapsed >= GRACE_PERIOD_SECONDS:
                old_name = incumbent.get("name", tracker.incumbent_code)
                tracker.incumbent_code = candidate["code"]
                tracker.incumbent_turnover = tracker.candidate_turnover
                tracker.candidate_code = None
                tracker.candidate_turnover = 0.0
                tracker.state = LeaderState.NORMAL
                tracker.state_entered_at = now
                logger.info(
                    f"[{sector}] GRACE 종료 — {old_name} → {candidate.get('name')}"
                )
            return
