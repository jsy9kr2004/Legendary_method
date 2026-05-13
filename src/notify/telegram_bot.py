"""Telegram 양방향 봇 — 사용자 명령 수신 및 응답 (M6).

명령어:
    /pause   자동/수동 모니터링 전체 ON/OFF 토글
    /on      모니터링 강제 ON (시간창 09:00~10:30 무시, 임의 시각에 켜기)
    /off     모니터링 강제 OFF (시간창 안이라도 끄기)
    /list    현재 모니터링 종목 출력
    /clear   수동 추가분만 해제
    NNNNNN   6자리 숫자 → 토글 추가/해제 (force_on 이면 시간창 외에서도 가능)
    그 외    "장 시간 외" 안내 또는 무시

설계:
    `parse_command()` 는 pure — 메시지 텍스트 → 명령 + 인자.
    실행은 `apply_command()` 가 MonitoringSession 에 위임.
    long polling worker 는 `src.dashboard.worker` 에서 호출.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from src.dashboard.state import MonitoringSession, in_monitoring_window


CommandKind = Literal["pause", "on", "off", "list", "clear", "toggle_code", "unknown", "ignore"]


@dataclass
class Command:
    kind: CommandKind
    code: str | None = None  # toggle_code 의 경우 6자리 코드


def parse_command(text: str) -> Command:
    """텔레그램 메시지 텍스트 → Command. 화이트스페이스 trim, 대소문자 무시 일부.

    봇 그룹 모드 prefix(@bot_name) 도 제거.
    """
    if not text:
        return Command(kind="ignore")

    t = text.strip()
    # 멘션 prefix 제거
    if "@" in t and t.startswith("/"):
        head, _, _ = t.partition("@")
        t = head + t.split(" ", 1)[1] if " " in t else head

    lower = t.lower()
    if lower in ("/pause", "/start"):
        # /start 도 pause 토글로 (봇 첫 시작 시)
        return Command(kind="pause")
    if lower == "/on":
        return Command(kind="on")
    if lower == "/off":
        return Command(kind="off")
    if lower == "/list":
        return Command(kind="list")
    if lower == "/clear":
        return Command(kind="clear")

    # 6자리 숫자
    if t.isdigit() and len(t) == 6:
        return Command(kind="toggle_code", code=t)

    return Command(kind="unknown")


def apply_command(
    cmd: Command,
    session: MonitoringSession,
    now: datetime,
) -> str:
    """Command 실행 → 사용자에게 보낼 응답 텍스트.

    장 시간 외 입력은 안내 메시지만 반환 (상태 변경 X).
    """
    if cmd.kind == "ignore":
        return ""

    # /pause 와 /list 는 시간과 무관하게 처리
    if cmd.kind == "pause":
        _, msg = session.toggle_pause()
        return msg

    if cmd.kind == "on":
        # 시간창 우회하고 강제 ON. master/theme 미로딩이면 다음 tick 에서 lazy 로딩됨.
        session.force_on = True
        session.paused = False
        return "▶ 모니터링 강제 ON (시간창 무시). 다음 tick부터 동작."

    if cmd.kind == "off":
        # 시간창 내부라도 강제 OFF. /pause 와 동일 효과지만 명시적.
        session.paused = True
        session.force_on = False
        return "⏸ 모니터링 OFF. /on 으로 재시작 또는 다음 평일 09:00 자동 ON."

    if cmd.kind == "list":
        return session.list_monitored()

    if cmd.kind == "clear":
        _, msg = session.remove_manual_all()
        return msg

    if cmd.kind == "toggle_code":
        # force_on 이면 시간창 외부에서도 /add 허용 (모니터링 자체가 켜져 있으므로).
        if not session.force_on and not in_monitoring_window(now):
            return f"장 시간 외입니다. (모니터링 운영: 평일 09:00~10:30, 또는 /on 으로 강제 켜기)"
        if cmd.code is None:
            return ""
        _, msg = session.add_manual(cmd.code, now)
        return msg

    if cmd.kind == "unknown":
        return ""  # 모르는 명령은 무시 (스팸 방지)

    return ""
