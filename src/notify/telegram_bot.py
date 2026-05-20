"""Telegram 양방향 봇 — 사용자 명령 수신 및 응답 (M6).

명령어:
    /on, /start                  모니터링 ON (멱등). 24h 허용.
    /off, /pause                 모니터링 OFF (멱등).
    /list                        현재 모니터링 종목 출력
    /clear                       수동 추가분만 해제
    NNNNNN                       6자리 숫자 → 감시 모드 토글 추가/해제
    /buy NNNNNN [PRICE] [MIN]    보유 모드 진입 (Exit.Triggers).
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
from src.data.tick_log import TradeEvent, append_trade_event
from src.scalping.score.thresholds import TIME_STOP_MINUTES_DEFAULT
from src.scalping.exit.triggers import (
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
    """보유 모드 진입 (round 35 — multi-flag 모델).

    monitored 에 없는 종목이면 entry 만 surface (is_manual 등 flag X — 보유는
    holdings 기반 derived). price 가 None 이면 last_prices → last_payloads.current
    순으로 fallback. 둘 다 없어도 entry_price=0 으로 등록 (사용자 정책: "그냥 보유
    처리"). Exit.Triggers 트리거는 entry_price <= 0 일 때 평가 skip 으로 안전 처리됨.
    사용자가 나중에 `/buy CODE PRICE` 로 가격 갱신 가능.
    """
    # 시세 fallback
    resolved_price: float | None = price
    autofilled = False
    if resolved_price is None:
        v = session.last_prices.get(code)
        if v is not None and v > 0:
            resolved_price = float(v)
            autofilled = True
        else:
            payload = session.last_payloads.get(code)
            if payload:
                cur = (payload.get("price") or {}).get("current")
                if cur and cur > 0:
                    resolved_price = float(cur)
                    autofilled = True

    # 사용자 정책: 시세 미확보여도 등록 진행. entry_price=0 → Exit.Triggers 트리거 skip.
    final_price = float(resolved_price) if (resolved_price and resolved_price > 0) else 0.0

    # monitored 에 surface — flag 는 안 켜고 entry 만. 보유 카드는 worker prune 이
    # holdings_codes 기반으로 유지 결정.
    name = code
    if code in session.monitored:
        name = session.monitored[code].name
    else:
        session.ensure_held_stock(code, name=code, now=now)

    holdings = load_holdings()
    minutes = time_stop_minutes or TIME_STOP_MINUTES_DEFAULT
    holding = Holding(
        code=code,
        entry_price=final_price,
        entry_time=now,
        time_stop_minutes=minutes,
    )
    holdings[code] = holding
    save_holdings(holdings)

    # Phase 1: 매수 이벤트 jsonl 기록 — 사후 분석 시 tick_logs 와 timestamp join.
    append_trade_event(
        TradeEvent(
            ts=now.isoformat(),
            code=code,
            name=name,
            action="buy",
            price=int(final_price) if final_price > 0 else None,
            source="command",
        ),
        now,
    )

    off_hours = not _is_regular_session(now)
    off_hours_note = (
        "\n⏸ 장 시간 외 — 다음 정규장(평일 09:00~15:30) 부터 시그널 평가 시작"
        if off_hours else ""
    )

    if final_price <= 0:
        return (
            f"🟡 {code} {name} — 보유 모드 진입 (매수가 미입력)\n"
            f"진입 {now.strftime('%H:%M:%S')}\n"
            f"※ Exit.Triggers 손절/익절 트리거는 평가 X — `/buy {code} PRICE` 로 매수가 갱신 권장"
            f"{off_hours_note}"
        )

    sl = holding.stop_loss_price
    tp1 = holding.take_profit_1_price
    tp2 = holding.take_profit_2_price
    autofill_note = " (시세 자동 보충)" if autofilled else ""
    return (
        f"🟡 {code} {name} — 보유 모드 진입\n"
        f"매수가 {int(final_price):,}{autofill_note}  진입 {now.strftime('%H:%M:%S')}\n"
        f"손절선 {int(sl):,} (-1.5%)\n"
        f"익절 1차 {int(tp1):,} (+2.0%) / 2차 {int(tp2):,} (+3.5%)\n"
        f"시간 손절 {minutes}분 후 +0.5% 미달 시 알림"
        f"{off_hours_note}"
    )


def _is_regular_session(now: datetime) -> bool:
    """KRX 정규장 (평일 09:00~15:30) 여부."""
    from src.calendar_kr import is_business_day

    if not is_business_day(now.date()):
        return False
    t = now.time()
    return (t.hour, t.minute) >= (9, 0) and (t.hour, t.minute) <= (15, 30)


def _apply_sell(code: str, session: MonitoringSession) -> str:
    """청산 — holdings 제거 + 수동 핀(is_manual)도 함께 clear (round 35).

    사용자 정책: 청산 후 수동 pin 유지 X. 자동/후보 풀에 있으면 다음 tick 에 그
    flag 로 카드 유지, 없으면 worker prune 으로 카드 사라짐.
    """
    holdings = load_holdings()
    if code not in holdings:
        return f"⚠ {code} — 보유 모드 아님"

    # Phase 1: pop 전에 Exit.Triggers 트리거 발화 사유 추출 — 사후 "어떤 트리거 발화 후
    # 사용자가 매도했는지" 분석용.
    holding_obj = holdings[code]
    triggers_fired_str: str | None = None
    try:
        fired = list(getattr(holding_obj, "triggers_fired", []) or [])
        if fired:
            triggers_fired_str = ",".join(fired)
    except (AttributeError, TypeError):
        pass

    holdings.pop(code)
    save_holdings(holdings)
    name = session.monitored[code].name if code in session.monitored else code

    # Phase 1: 매도 이벤트 jsonl 기록.
    from datetime import datetime as _dt
    now = _dt.now()
    sell_price = session.last_prices.get(code)
    append_trade_event(
        TradeEvent(
            ts=now.isoformat(),
            code=code,
            name=name,
            action="sell",
            price=int(sell_price) if sell_price else None,
            source="command",
            trigger_fired=triggers_fired_str,
        ),
        now,
    )

    cleared_manual = session.clear_manual_flag(code)
    pin_note = " + 수동 핀 해제" if cleared_manual else ""
    return f"⚪ {code} {name} — 청산 처리{pin_note}"
