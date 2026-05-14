"""실시간 모니터링 상태 머신 (M6).

핵심 책임:
    - 모니터링 대상 종목 set 관리 (자동/수동 출처)
    - 주도주 교체 상태 머신: NORMAL → TRANSITION → GRACE → NORMAL
    - 5분 GRACE 유예기 카운트다운 (실제 교체 후 a1, a2 함께 표시)
    - 사용자 명령 처리 (/pause, /list, /clear, 6자리 코드 토글)
    - 장 시간 외 입력 안내

I/O 분리:
    본 모듈은 pure — 시각(now)을 인자로 받고 변경된 알림 리스트를 반환.
    실제 텔레그램 발송/분봉 fetch 는 worker (`src/dashboard/worker.py`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from enum import Enum
from typing import Any

from loguru import logger

from src.calendar_kr import is_business_day
from src.jongbae.config_thresholds import (
    GRACE_PERIOD_SECONDS,
    MONITORING_END_HOUR,
    MONITORING_END_MINUTE,
    MONITORING_MAX_CODES,
    MONITORING_START_HOUR,
    MONITORING_START_MINUTE,
    TRANSITION_EXIT_PERSIST_SECONDS,
    TRANSITION_EXIT_TURNOVER_RATIO,
    TRANSITION_TURNOVER_RATIO,
)


class Source(str, Enum):
    AUTO = "auto"      # 주도주 (주도섹터 결정 + 회전율 1위)
    MANUAL = "manual"  # 사용자 6자리 코드 입력
    RISING = "rising"  # 거래대금 급증 후보 — 첫 알림 + 2분 유지, 매매 결정 시 사용자가 MANUAL 로 승격


class LeaderState(str, Enum):
    NORMAL = "normal"
    TRANSITION = "transition"
    GRACE = "grace"


@dataclass
class MonitoredStock:
    code: str
    name: str
    source: Source
    added_at: datetime
    message_id: int | None = None    # Telegram message id (편집용)
    themes: list[str] = field(default_factory=list)
    # RISING 한정: 자동 만료 시각 (첫 알림 + 2분 default). None 이면 만료 없음(AUTO/MANUAL).
    # 만료되면 prune_expired() 가 제거 + 텔레그램 메시지 delete.
    expires_at: datetime | None = None


@dataclass
class LeaderTracker:
    """주도섹터 1개에 대한 a1/a2 + 상태 머신.

    NORMAL: a1 만 모니터링.
    TRANSITION: a1 + a2 함께. 후보 a2 부상 중.
    GRACE: a1 + a2 함께 5분간. a2 가 a1 회전율 추월 후 유예기.
    """
    sector: str
    incumbent_code: str           # a1
    incumbent_turnover: float = 0.0
    candidate_code: str | None = None  # a2
    candidate_turnover: float = 0.0
    state: LeaderState = LeaderState.NORMAL
    state_entered_at: datetime | None = None
    transition_weak_since: datetime | None = None  # 후보 약화 지속 시작


@dataclass
class Alert:
    """상태 전이 / 임계 이벤트 시 사용자에게 보낼 메시지.

    worker 가 텔레그램으로 발송 (편집 X, 새 메시지 + 푸시 ON).
    """
    kind: str    # "transition" / "replacement" / "exit" / "strong_rise" / "sector_change"
    text: str
    code: str | None = None


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
    # 시간창(평일 09:00~10:30) 가드를 우회. /on 명령으로 강제 ON, /off 로 해제.
    # 평일 자동 09:00~10:30 외에 사용자가 임의 시각(8시/11시 등)에 켜고 싶을 때.
    force_on: bool = False
    monitored: dict[str, MonitoredStock] = field(default_factory=dict)
    trackers: dict[str, LeaderTracker] = field(default_factory=dict)  # sector -> tracker
    # /off 시 카드 메시지 정리를 tick job 이 1회 수행하기 위한 플래그.
    off_cleanup_pending: bool = False
    # 알림 디바운스: (code, kind) → 마지막 푸시했을 때의 accel 값.
    # 같은 종목·같은 종류 알림이 5초마다 반복되지 않도록 edge-trigger 가드.
    # 의미있게 더 악화되거나(0.1배 이상 차이) 한 번 정상권(accel ≥ 1.0) 복귀했다
    # 다시 트리거되면 재푸시. 자세한 룰은 worker.py 알림 분기 참조.
    last_alert_accel: dict[tuple[str, str], float] = field(default_factory=dict)
    # 호가 잔량 색상 추적: code → 직전 색상('green'/'yellow'/'red'). 색상 전환 시 alert.
    last_asking_color: dict[str, str] = field(default_factory=dict)
    # 모니터링 메시지 재배치 플래그.
    # 텔레그램은 메시지 순서 변경 불가 — 새 alert 가 발송되면 모니터링 메시지가
    # 위로 밀려난다. 이 flag 가 True 면 다음 tick 에서 모든 모니터링 메시지를
    # delete + 새로 send (silent) 해서 화면 최하단으로 재배치한다.
    # set 트리거: ⭐ 자동 모니터링 추가/제거, ⚡/⚠ 알림 발송, 사용자 명령 응답.
    reposition_pending: bool = False

    # ── 종목 추가/제거 ────────────────────────────────────────────────────────

    def add_manual(self, code: str, now: datetime) -> tuple[bool, str]:
        """사용자가 6자리 코드 입력 — 토글.

        Returns:
            (changed, response_message)
        """
        code = code.strip()
        if len(code) != 6 or not code.isdigit():
            return False, f"잘못된 종목코드: {code} (6자리 숫자 필요)"

        if code in self.monitored:
            existing = self.monitored[code]
            if existing.source == Source.RISING:
                # 부상 후보 → 수동 승격 (만료 해제).
                existing.source = Source.MANUAL
                existing.expires_at = None
                existing.added_at = now
                return True, f"🔵 {code} {existing.name} — 부상 후보 → 수동 승격"
            if existing.source == Source.AUTO:
                # 자동 주도주 → 수동 승격 (보유 잠금).
                # 사용자가 매수 후 같은 코드 입력 시 AUTO 가 +29% 도달해도 monitored
                # 에서 빠지지 않게 MANUAL 로 잠금. 다시 입력하면 해제(아래 MANUAL 분기).
                existing.source = Source.MANUAL
                existing.added_at = now
                return True, f"🔵 {code} {existing.name} — 자동 → 수동 승격 (보유 잠금)"
            # MANUAL 이면 토글 해제
            self.monitored.pop(code)
            return True, f"{code} {existing.name} — 모니터링 해제"

        # MONITORING_MAX_CODES 는 core(AUTO+MANUAL) 한도 — RISING 카운트 제외.
        core_count = sum(
            1 for m in self.monitored.values() if m.source != Source.RISING
        )
        if core_count >= MONITORING_MAX_CODES:
            return False, f"⚠ 모니터링 종목 최대 {MONITORING_MAX_CODES}개 — 추가 거부 (해제 후 재시도)"

        self.monitored[code] = MonitoredStock(
            code=code, name=code, source=Source.MANUAL, added_at=now,
        )
        return True, f"🔵 {code} — 수동 모니터링 추가됨"

    def remove_manual_all(self) -> tuple[int, str]:
        """/clear — 수동 추가분만 해제. 자동(주도주)는 유지."""
        manual = [c for c, m in self.monitored.items() if m.source == Source.MANUAL]
        for c in manual:
            self.monitored.pop(c)
        return len(manual), f"🧹 수동 추가분 {len(manual)}개 해제. 자동 주도주는 유지."

    def list_monitored(self) -> str:
        """/list — 현재 모니터링 종목."""
        if not self.monitored:
            return "📋 모니터링 중인 종목 없음."
        lines = [f"📋 [현재 모니터링 — {len(self.monitored)}개]"]
        auto = [m for m in self.monitored.values() if m.source == Source.AUTO]
        manual = [m for m in self.monitored.values() if m.source == Source.MANUAL]
        if auto:
            lines.append("⭐ 자동")
            for m in auto:
                themes = " / ".join(m.themes) if m.themes else "?"
                lines.append(f"  • {m.code} {m.name} ({themes})")
        if manual:
            lines.append("🔵 수동")
            for m in manual:
                lines.append(f"  • {m.code} {m.name}")
        return "\n".join(lines)

    def set_on(self) -> tuple[bool, str]:
        """/on /start — 모니터링 ON (멱등). 이미 ON 이면 안내만."""
        if not self.paused:
            return False, "▶ 이미 모니터링 ON 상태"
        self.paused = False
        return True, "▶ 모니터링 ON — 카드 갱신 시작"

    def set_off(self) -> tuple[bool, str]:
        """/off — 모니터링 OFF (멱등). 이미 OFF 이면 안내만.

        카드 메시지 정리는 tick job 이 다음 사이클에 1회 수행 (off_cleanup_pending).
        """
        if self.paused:
            return False, "⏸ 이미 모니터링 OFF 상태"
        self.paused = True
        self.off_cleanup_pending = True
        return True, "⏸ 모니터링 OFF — /on 으로 재개 (다음 평일 09:00 자동 ON)"

    # ── 자동 주도주 갱신 ──────────────────────────────────────────────────────

    def update_auto_leaders(
        self,
        leaders: list[dict[str, Any]],
        now: datetime,
    ) -> list[str]:
        """자동 주도주 (회전율 1위) 리스트로 monitored 갱신.

        leaders: identify_early_morning_leaders 결과.
        반환: 변경 사항 한 줄 요약 리스트.
        """
        changes: list[str] = []

        new_codes = {l["code"] for l in leaders}
        # 자동분 제거
        for code in list(self.monitored.keys()):
            if code in new_codes:
                continue
            entry = self.monitored[code]
            if entry.source == Source.AUTO:
                self.monitored.pop(code)
                changes.append(f"⭐→💤 {code} {entry.name} 자동 모니터링 종료")

        # 자동분 추가/갱신. MONITORING_MAX_CODES 는 core(AUTO+MANUAL) 한도 —
        # RISING(부상 후보)은 별도 카운트하므로 슬롯 계산에서 제외.
        for ld in leaders:
            code = ld["code"]
            if code in self.monitored:
                # 이미 있으면 (수동이든 자동이든) 테마 합치기, source는 수동 우선
                m = self.monitored[code]
                m.themes = list(set(m.themes + ld.get("themes", [])))
                continue
            core_count = sum(
                1 for m in self.monitored.values() if m.source != Source.RISING
            )
            if core_count >= MONITORING_MAX_CODES:
                logger.warning(
                    f"모니터링 슬롯 가득 ({MONITORING_MAX_CODES}) — 자동 주도주 {code} 추가 보류"
                )
                continue
            self.monitored[code] = MonitoredStock(
                code=code,
                name=ld.get("name", code),
                source=Source.AUTO,
                added_at=now,
                themes=list(ld.get("themes", [])),
            )
            changes.append(f"⭐ 자동 모니터링 추가: {ld.get('name', code)} ({code})")

        return changes

    # ── 부상 후보 (RISING) ────────────────────────────────────────────────────

    def update_rising_candidates(
        self,
        candidates: list[dict[str, Any]],
        now: datetime,
        ttl_minutes: int = 2,
        max_count: int = 5,
    ) -> list[str]:
        """거래대금 급증 후보를 RISING source 로 등록/갱신.

        - 신규: TTL 분 후 만료 (default 2분). 신규 진입 변경 사항 리스트로 반환.
        - 기존 RISING: candidates 안에 다시 들어있으면 expires_at 연장 (rolling).
            없으면 그대로 두고 prune_expired 시점에 정리.
        - AUTO/MANUAL 이미 있으면 RISING 추가 skip (중복 방지).

        Args:
            candidates: identify_rising_candidates 결과.
            now: 현재 시각.
            ttl_minutes: 신규 진입 시 유지 분.
            max_count: 동시 RISING 종목 한도.

        Returns:
            첫 진입 종목 변경 메시지 리스트 (alert 발송용).
        """
        from datetime import timedelta

        changes: list[str] = []
        seen_in_pool: set[str] = set()
        ttl = timedelta(minutes=ttl_minutes)

        for cand in candidates:
            code = cand["code"]
            seen_in_pool.add(code)
            if code in self.monitored:
                m = self.monitored[code]
                if m.source == Source.RISING:
                    # 풀에 계속 있으면 TTL 연장 (rolling window)
                    m.expires_at = now + ttl
                    # 테마 라벨 갱신 (있다면)
                    new_themes = cand.get("themes", [])
                    if new_themes:
                        m.themes = list(set(m.themes + new_themes))
                # AUTO/MANUAL 이면 RISING 으로 덮어쓰지 않음
                continue
            # 신규 RISING. core 슬롯은 침범하지 않으나 RISING 한도는 별도.
            rising_count = sum(
                1 for m in self.monitored.values() if m.source == Source.RISING
            )
            if rising_count >= max_count:
                continue
            self.monitored[code] = MonitoredStock(
                code=code,
                name=cand.get("name", code),
                source=Source.RISING,
                added_at=now,
                themes=list(cand.get("themes", [])),
                expires_at=now + ttl,
            )
            changes.append(f"⚡ {cand.get('name', code)} ({code})")
        return changes

    def prune_expired(self, now: datetime) -> list[str]:
        """RISING 중 expires_at 지난 종목 제거. 제거된 code 리스트 반환.

        호출자가 텔레그램 메시지도 함께 delete 한다.
        """
        expired: list[str] = []
        for code in list(self.monitored.keys()):
            m = self.monitored[code]
            if m.source != Source.RISING:
                continue
            if m.expires_at is not None and now >= m.expires_at:
                self.monitored.pop(code)
                expired.append(code)
        return expired

    def promote_rising_to_manual(self, code: str, now: datetime) -> bool:
        """RISING → MANUAL 승격 (사용자 매매 결정 시).

        Returns:
            True 면 승격 성공. 종목이 없거나 RISING 이 아니면 False.
        """
        m = self.monitored.get(code)
        if m is None or m.source != Source.RISING:
            return False
        m.source = Source.MANUAL
        m.expires_at = None
        m.added_at = now
        return True

    # ── LeaderTracker 상태 머신 ──────────────────────────────────────────────

    def step_tracker(
        self,
        sector: str,
        incumbent: dict[str, Any],
        candidate: dict[str, Any] | None,
        candidate_passed_transition_check: bool,
        now: datetime,
    ) -> Alert | None:
        """섹터별 상태 머신 한 스텝 진행.

        Args:
            sector: 주도섹터명.
            incumbent: 현재 주도주 a1 (code, name, turnover).
            candidate: 부상 후보 a2 (code, name, turnover) 또는 None.
                후보 판정은 호출자가 미리 (회전율비 + 가속배율) 체크.
            candidate_passed_transition_check: 후보가 TRANSITION 진입 조건 통과 여부.
            now: 현재 시각.

        Returns:
            상태 전이 시 Alert. 변화 없으면 None.
        """
        tracker = self.trackers.get(sector)
        if tracker is None:
            tracker = LeaderTracker(
                sector=sector,
                incumbent_code=incumbent["code"],
                incumbent_turnover=float(incumbent.get("turnover", 0.0)),
                state_entered_at=now,
            )
            self.trackers[sector] = tracker
            return None

        # 주도주가 통째로 바뀐 경우 (a1 자체가 사라짐 등) — 트래커 리셋
        if tracker.incumbent_code != incumbent["code"] and tracker.state == LeaderState.NORMAL:
            tracker.incumbent_code = incumbent["code"]
            tracker.incumbent_turnover = float(incumbent.get("turnover", 0.0))
            tracker.state_entered_at = now
            return None

        tracker.incumbent_turnover = float(incumbent.get("turnover", 0.0))

        # 상태별 처리
        if tracker.state == LeaderState.NORMAL:
            if candidate and candidate_passed_transition_check:
                tracker.state = LeaderState.TRANSITION
                tracker.candidate_code = candidate["code"]
                tracker.candidate_turnover = float(candidate.get("turnover", 0.0))
                tracker.state_entered_at = now
                tracker.transition_weak_since = None
                return Alert(
                    kind="transition",
                    code=candidate["code"],
                    text=(
                        f"🔥 [부상 후보 감지] {now.strftime('%H:%M:%S')}\n"
                        f"섹터: {sector}\n"
                        f"현재 주도주: {incumbent.get('name', incumbent['code'])} "
                        f"(회전율 {tracker.incumbent_turnover:.1f}%)\n"
                        f"부상 후보: {candidate.get('name', candidate['code'])} "
                        f"(회전율 {tracker.candidate_turnover:.1f}%)"
                    ),
                )
            return None

        if tracker.state == LeaderState.TRANSITION:
            if candidate is None or candidate["code"] != tracker.candidate_code:
                # 후보가 사라짐 → NORMAL 복귀
                tracker.state = LeaderState.NORMAL
                tracker.candidate_code = None
                tracker.candidate_turnover = 0.0
                tracker.transition_weak_since = None
                return None

            tracker.candidate_turnover = float(candidate.get("turnover", 0.0))

            # a2 회전율이 a1 추월 → GRACE 진입
            if tracker.candidate_turnover > tracker.incumbent_turnover:
                tracker.state = LeaderState.GRACE
                tracker.state_entered_at = now
                tracker.transition_weak_since = None
                return Alert(
                    kind="replacement",
                    code=candidate["code"],
                    text=(
                        f"🔄 [주도주 교체 완료] {now.strftime('%H:%M:%S')}\n"
                        f"{sector} — {incumbent.get('name', incumbent['code'])} "
                        f"→ {candidate.get('name', candidate['code'])} (회전율 역전)\n"
                        f"GRACE {GRACE_PERIOD_SECONDS // 60}분 함께 표시"
                    ),
                )

            # a2 약화 (a2 회전율 < a1 × 0.4) 가 3분 지속 → 후보 탈락
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
                    return None
            else:
                tracker.transition_weak_since = None
            return None

        if tracker.state == LeaderState.GRACE:
            assert tracker.state_entered_at is not None
            elapsed = (now - tracker.state_entered_at).total_seconds()

            # GRACE 중 a1 이 다시 a2 추월하면 카운트다운 무효, NORMAL 복귀
            if (
                candidate is None
                or candidate["code"] != tracker.candidate_code
                or float(candidate.get("turnover", 0.0)) < tracker.incumbent_turnover
            ):
                # a1 이 우세 회복 → 교체 무효
                tracker.state = LeaderState.NORMAL
                tracker.candidate_code = None
                tracker.candidate_turnover = 0.0
                tracker.state_entered_at = now
                return None

            tracker.candidate_turnover = float(candidate.get("turnover", 0.0))

            if elapsed >= GRACE_PERIOD_SECONDS:
                # GRACE 종료 — incumbent 를 a2 로 교체
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
                return None
            return None

        return None
