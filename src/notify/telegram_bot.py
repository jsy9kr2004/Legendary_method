"""Telegram 양방향 봇 — 사용자 명령 수신 및 응답 (M6).

명령어:
    /on, /start                  모니터링 ON (멱등). 24h 허용.
    /off, /pause                 모니터링 OFF (멱등).
    /list                        현재 모니터링 종목 출력
    /clear                       수동 추가분만 해제
    NNNNNN                       6자리 숫자 → 감시 모드 토글 추가/해제
    /buy NNNNNN [PRICE] [MIN]    보유 모드 진입 (R15).
                                 PRICE 생략 시 모니터링 최근 시세를 매수가로 사용.
                                 MIN 은 시간손절 N분(기본 10).
    /sell NNNNNN                 보유 모드 해제 (감시 모드 복귀)
    /status NNNNNN               해당 종목 풀 카드 강제 재발송 트리거
    그 외                        무시

설계:
    `parse_command()` 는 pure — 메시지 텍스트 → 명령 + 인자.
    실행은 `apply_command()` 가 MonitoringSession 에 위임.
    long polling worker 는 `src.dashboard.worker` 에서 24h 상시 호출.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from src.dashboard.state import MonitoringSession
from src.jongbae.config_thresholds import TIME_STOP_MINUTES_DEFAULT
from src.jongbae.exit_triggers import (
    Holding,
    load_holdings,
    save_holdings,
)


CommandKind = Literal[
    "on", "off", "list", "clear", "toggle_code",
    "buy", "sell", "status",
    "unknown", "ignore",
]


@dataclass
class Command:
    kind: CommandKind
    code: str | None = None         # toggle_code / buy / sell / status 의 종목코드
    price: float | None = None      # buy 의 매수가
    time_stop_minutes: int | None = None  # buy 의 시간손절 오버라이드


def _strip_mention(t: str) -> str:
    """`/cmd@botname args` → `/cmd args`."""
    if "@" not in t or not t.startswith("/"):
        return t
    head, sep, tail = t.partition(" ")
    if "@" in head:
        head = head.split("@", 1)[0]
    return f"{head} {tail}".strip() if sep else head


def _is_valid_code(s: str) -> bool:
    return s.isdigit() and len(s) == 6


def parse_command(text: str) -> Command:
    """텔레그램 메시지 텍스트 → Command. 화이트스페이스 trim, 대소문자 무시 일부.

    봇 그룹 모드 prefix(@bot_name) 도 제거.
    """
    if not text:
        return Command(kind="ignore")

    t = _strip_mention(text.strip())
    if not t:
        return Command(kind="ignore")

    parts = t.split()
    head = parts[0]
    lower = head.lower()

    if lower in ("/on", "/start"):
        return Command(kind="on")
    if lower in ("/off", "/pause"):
        return Command(kind="off")
    if lower == "/list":
        return Command(kind="list")
    if lower == "/clear":
        return Command(kind="clear")

    if lower == "/buy":
        # /buy CODE [PRICE] [MIN] — PRICE 생략 시 worker 가 추적 중인 최근 시세 사용.
        if len(parts) < 2:
            return Command(kind="unknown")
        code = parts[1]
        if not _is_valid_code(code):
            return Command(kind="unknown")
        price: float | None = None
        if len(parts) >= 3:
            try:
                price = float(parts[2].replace(",", ""))
            except ValueError:
                return Command(kind="unknown")
            if price <= 0:
                return Command(kind="unknown")
        min_override: int | None = None
        if len(parts) >= 4:
            try:
                m = int(parts[3])
                if m > 0:
                    min_override = m
            except ValueError:
                pass
        return Command(kind="buy", code=code, price=price, time_stop_minutes=min_override)

    if lower == "/sell":
        if len(parts) < 2 or not _is_valid_code(parts[1]):
            return Command(kind="unknown")
        return Command(kind="sell", code=parts[1])

    if lower == "/status":
        if len(parts) < 2 or not _is_valid_code(parts[1]):
            return Command(kind="unknown")
        return Command(kind="status", code=parts[1])

    # 6자리 숫자 (감시 모드 토글)
    if _is_valid_code(head) and len(parts) == 1:
        return Command(kind="toggle_code", code=head)

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

    if cmd.kind == "on":
        _, msg = session.set_on()
        return msg

    if cmd.kind == "off":
        _, msg = session.set_off()
        return msg

    if cmd.kind == "list":
        return session.list_monitored()

    if cmd.kind == "clear":
        _, msg = session.remove_manual_all()
        return msg

    if cmd.kind == "toggle_code":
        # 24h 허용 (round 18) — /on 24h 정책과 일관성.
        if cmd.code is None:
            return ""
        _, msg = session.add_manual(cmd.code, now)
        return msg

    if cmd.kind == "buy":
        # 24h 허용 (round 18) — 사용자가 NXT/장중/임의 시점 매수했음을 봇에 알림.
        # PRICE 생략 시 _apply_buy 가 session.last_prices 에서 자동 보충 (round 20).
        if cmd.code is None:
            return ""
        return _apply_buy(cmd.code, cmd.price, cmd.time_stop_minutes, session, now)

    if cmd.kind == "sell":
        if cmd.code is None:
            return ""
        return _apply_sell(cmd.code, session)

    if cmd.kind == "status":
        if cmd.code is None:
            return ""
        if cmd.code not in session.monitored:
            return f"⚠ {cmd.code} — 모니터링 중이 아님"
        m = session.monitored[cmd.code]
        # 풀 카드 재발송은 worker 가 다음 tick 에 message_id 초기화로 처리
        m.message_id = None
        return f"🔄 {cmd.code} {m.name} — 다음 갱신에서 카드 재발송"

    if cmd.kind == "unknown":
        return ""  # 모르는 명령 무시 (스팸 방지)

    return ""


# ── /buy /sell 영속화 ───────────────────────────────────────────────────────


def _apply_buy(
    code: str,
    price: float | None,
    time_stop_minutes: int | None,
    session: MonitoringSession,
    now: datetime,
) -> str:
    """보유 모드 진입.

    감시 모드에 없는 종목이면 자동으로 수동 추가까지 함께 진행.
    price 가 None 이면 `session.last_prices` (worker tick 이 매 사이클 채움) 에서
    최근 시세를 매수가로 사용 (round 20 — UX 단순화). 모니터링 안 하던 종목이거나
    아직 첫 tick 전이라 시세가 없으면 명시 입력 요구.
    """
    if code not in session.monitored:
        ok, add_msg = session.add_manual(code, now)
        if not ok:
            return add_msg  # 슬롯 가득 등

    if price is None:
        price = session.last_prices.get(code)
        if price is None or price <= 0:
            # M7 wiring (PWA): 카드 페이로드의 current price 도 fallback.
            # 데모 환경 / 워밍업 중 / 종목이 모니터링 풀에 갓 들어와 last_prices
            # 가 비어 있어도 보유 등록 가능.
            payload = session.last_payloads.get(code)
            if payload:
                cur = (payload.get("price") or {}).get("current")
                if cur and cur > 0:
                    price = float(cur)
        if price is None or price <= 0:
            return (
                f"⚠ {code} — 최근 시세 미확보. "
                f"`/buy {code} PRICE` 로 매수가를 명시해 주세요."
            )

    holdings = load_holdings()
    minutes = time_stop_minutes or TIME_STOP_MINUTES_DEFAULT
    holding = Holding(
        code=code,
        entry_price=price,
        entry_time=now,
        time_stop_minutes=minutes,
    )
    holdings[code] = holding
    save_holdings(holdings)

    name = session.monitored[code].name if code in session.monitored else code
    sl = holding.stop_loss_price
    tp1 = holding.take_profit_1_price
    tp2 = holding.take_profit_2_price
    # 장 시간 외 안내 — 등록은 진행하되 R15 트리거가 다음 정규장부터 의미를 가짐.
    off_hours = not _is_regular_session(now)
    off_hours_note = (
        "\n⏸ 장 시간 외 — 다음 정규장(평일 09:00~15:30) 부터 시그널 평가 시작"
        if off_hours else ""
    )
    return (
        f"🟡 {code} {name} — 보유 모드 진입\n"
        f"매수가 {int(price):,}  진입 {now.strftime('%H:%M:%S')}\n"
        f"손절선 {int(sl):,} (-1.5%)\n"
        f"익절 1차 {int(tp1):,} (+2.0%) / 2차 {int(tp2):,} (+3.5%)\n"
        f"시간 손절 {minutes}분 후 +0.5% 미달 시 알림"
        f"{off_hours_note}"
    )


def _is_regular_session(now: datetime) -> bool:
    """KRX 정규장 (평일 09:00~15:30) 여부.

    M7 PWA / 텔레그램 /buy 명령에서 장 시간 외 안내용. NXT 프리장은 v0 미지원
    (CLAUDE.md). 휴장일 정밀 캘린더(`calendar_kr.is_business_day`) 사용.
    """
    from src.calendar_kr import is_business_day

    if not is_business_day(now.date()):
        return False
    t = now.time()
    return (t.hour, t.minute) >= (9, 0) and (t.hour, t.minute) <= (15, 30)


def _apply_sell(code: str, session: MonitoringSession) -> str:
    """보유 모드 해제 → 감시 모드 복귀.

    감시 모드 자체 토글은 X (사용자가 6자리 코드 다시 입력해서 해제).
    """
    holdings = load_holdings()
    if code not in holdings:
        return f"⚠ {code} — 보유 모드 아님"
    holdings.pop(code)
    save_holdings(holdings)
    name = session.monitored[code].name if code in session.monitored else code
    return f"⚪ {code} {name} — 감시 모드 복귀 (보유 해제)"
