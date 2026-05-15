"""실시간 모니터링 상태 머신 (M6).

핵심 책임:
    - 모니터링 대상 종목 set 관리 (자동/수동/부상 후보 출처)
    - 주도주 교체 상태 머신: NORMAL → TRANSITION → GRACE → NORMAL (카드 헤더에 통합 표시)
    - 5분 GRACE 유예기 카운트다운 (실제 교체 후 a1, a2 함께 표시)
    - 사용자 명령 처리 (/pause, /list, /clear, 6자리 코드 토글)
    - 장 시간 외 입력 안내

I/O 분리:
    본 모듈은 pure — 시각(now)을 인자로 받고 상태만 갱신.
    실제 텔레그램 발송/분봉 fetch 는 worker (`src/dashboard/worker.py`).
    정정 round 19 이후: 카드 외 별도 푸시는 모두 폐기. step_tracker 등은 상태만
    갱신하며 alert 객체를 만들지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
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
)


class Source(str, Enum):
    AUTO = "auto"      # 주도주 (주도섹터 결정 + 회전율 1위)
    MANUAL = "manual"  # 사용자 6자리 코드 입력
    # 거래대금 급증 후보. 시간 만료 없음 — 후보 풀에서 빠지면 즉시 카드 제거
    # (정정 round 19). 매매 결정 시 사용자가 /add 또는 6자리 토글로 MANUAL 승격.
    RISING = "rising"


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
    # round 21: R14 매수 점수 + 등급. worker tick 이 Stage 4 통과 종목에 한해 채움.
    # 카드 헤더 표시에 사용. RISING 종목은 등록 시점에 score 가 있어야 카드 surface 됨.
    buy_score: float | None = None
    buy_grade: str | None = None   # "STRONG" / "WATCH" / "NEUTRAL" / "AVOID"
    buy_reasons: list[str] = field(default_factory=list)


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
    # worker tick 이 매 사이클 채우는 code → 최근 현재가. `/buy CODE` 가 PRICE
    # 인자 없이 들어와도 여기서 자동 보충 (round 20). 다른 thread (telegram_bot.
    # _apply_buy) 가 읽으므로 단순 dict 로 두고 GIL 에 의존 (atomic dict 읽기/쓰기).
    last_prices: dict[str, float] = field(default_factory=dict)
    # round 22: 종목별 체결강도(VP) 시계열. worker tick 에서 VP push, 카드/트리거에서
    # ma_1/ma_5/ma_20 조회. memory-only, 데몬 재시작 시 워밍업 다시 시작 (5분 내 정상화).
    vp_series: dict[str, Any] = field(default_factory=dict)  # code -> VPSeries
    # round 32 (P1-1 wiring): 종목별 상한가 도달 시각. scheduler 의 상한가 감지
    # 시점에 저장, worker funnel 에서 GraderSnapshot.limit_up_hit_time 으로 전달.
    # R14c 가산점(9:30 이전 +1, 10:30 이전 +0.5). 데몬 재시작 시 비어있음 — 당일
    # 재감지 시 채워짐.
    limit_up_hit_times: dict[str, time] = field(default_factory=dict)
    # M7 PWA 대시보드용 페이로드. worker tick 이 매 사이클 갱신, FastAPI WebSocket
    # endpoint 가 polling 후 broadcast. 텔레그램 텍스트(message_ids) 와 별도 채널.
    # 빠진 monitored 종목은 tick 끝에 정리. 데몬 재시작 시 비어있음.
    last_payloads: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_payload_ts: datetime | None = None

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
                # 부상 후보 → 수동 승격.
                existing.source = Source.MANUAL
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
        max_count: int = 5,
    ) -> list[str]:
        """거래대금 급증 후보 풀로 RISING 종목 set 을 동기화.

        정책 (정정 round 19):
            - 시간 만료(TTL) 폐지. 풀에서 안 보이는 RISING 은 즉시 제거.
            - AUTO/MANUAL 이미 있으면 RISING 으로 덮어쓰지 않음(중복 방지).
            - 풀 안에서 회전율 상위 max_count 까지만 RISING 카드 유지.

        Args:
            candidates: identify_rising_candidates 결과 (회전율 내림차순).
            now: 현재 시각.
            max_count: 동시 RISING 종목 한도.

        Returns:
            첫 진입 종목 변경 메시지 리스트 (로그용).
        """
        changes: list[str] = []
        pool_codes = [c["code"] for c in candidates]
        pool_codes_set = set(pool_codes)

        # 1) 풀에 없는 기존 RISING 종목 제거. AUTO/MANUAL 은 건드리지 않음.
        for code in list(self.monitored.keys()):
            m = self.monitored[code]
            if m.source != Source.RISING:
                continue
            if code not in pool_codes_set:
                self.monitored.pop(code)
                changes.append(f"💤 {m.name} ({code}) 부상 후보 풀 이탈 — 카드 제거")

        # 2) 풀 상위에서 신규 RISING 추가 + 기존 RISING 점수 갱신.
        for cand in candidates:
            code = cand["code"]
            # round 21: candidates 에 buy_score/buy_grade/buy_reasons 가 있으면 반영.
            buy_score = cand.get("buy_score")
            buy_grade = cand.get("buy_grade")
            buy_reasons = cand.get("buy_reasons") or []
            if code in self.monitored:
                m = self.monitored[code]
                if m.source == Source.RISING:
                    new_themes = cand.get("themes", [])
                    if new_themes:
                        m.themes = list(set(m.themes + new_themes))
                    if buy_score is not None:
                        m.buy_score = buy_score
                        m.buy_grade = buy_grade
                        m.buy_reasons = list(buy_reasons)
                continue
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
                buy_score=buy_score,
                buy_grade=buy_grade,
                buy_reasons=list(buy_reasons),
            )
            score_str = f" [{buy_grade} {buy_score:+.1f}]" if buy_score is not None else ""
            changes.append(f"⚡ {cand.get('name', code)} ({code}) 부상 후보 신규{score_str}")
        return changes

    def promote_rising_to_manual(self, code: str, now: datetime) -> bool:
        """RISING → MANUAL 승격 (사용자 매매 결정 시).

        Returns:
            True 면 승격 성공. 종목이 없거나 RISING 이 아니면 False.
        """
        m = self.monitored.get(code)
        if m is None or m.source != Source.RISING:
            return False
        m.source = Source.MANUAL
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
    ) -> None:
        """섹터별 상태 머신 한 스텝 진행.

        정정 round 19: alert 객체 반환 폐지 — 모든 상태 전이는 카드 헤더에
        통합 표시. 본 함수는 tracker 상태만 갱신한다.

        Args:
            sector: 주도섹터명.
            incumbent: 현재 주도주 a1 (code, name, turnover).
            candidate: 부상 후보 a2 (code, name, turnover) 또는 None.
                후보 판정은 호출자가 미리 (회전율비 + 가속배율) 체크.
            candidate_passed_transition_check: 후보가 TRANSITION 진입 조건 통과 여부.
            now: 현재 시각.
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
            return

        # 주도주가 통째로 바뀐 경우 (a1 자체가 사라짐 등) — 트래커 리셋
        if tracker.incumbent_code != incumbent["code"] and tracker.state == LeaderState.NORMAL:
            tracker.incumbent_code = incumbent["code"]
            tracker.incumbent_turnover = float(incumbent.get("turnover", 0.0))
            tracker.state_entered_at = now
            return

        tracker.incumbent_turnover = float(incumbent.get("turnover", 0.0))

        # 상태별 처리
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
                # 후보가 사라짐 → NORMAL 복귀
                tracker.state = LeaderState.NORMAL
                tracker.candidate_code = None
                tracker.candidate_turnover = 0.0
                tracker.transition_weak_since = None
                return

            tracker.candidate_turnover = float(candidate.get("turnover", 0.0))

            # a2 회전율이 a1 추월 → GRACE 진입
            if tracker.candidate_turnover > tracker.incumbent_turnover:
                tracker.state = LeaderState.GRACE
                tracker.state_entered_at = now
                tracker.transition_weak_since = None
                return

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
            else:
                tracker.transition_weak_since = None
            return

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
                return

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
            return
