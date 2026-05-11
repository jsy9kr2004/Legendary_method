"""텔레그램 명령 수신 polling 테스트 CLI.

사용:
    python -m src.dashboard.test_poll               # 기본 90초
    python -m src.dashboard.test_poll --seconds 60
    python -m src.dashboard.test_poll --drain-only  # 누적된 update 만 한번에 처리

명령 처리는 command_poll_loop 와 동일한 경로 (parse_command → apply_command).
응답은 텔레그램 봇이 chat 으로 전송. 결과는 stdout 에도 출력.
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from datetime import datetime

from loguru import logger

from src.config import load_settings, now_kst
from src.dashboard.state import MonitoringSession
from src.notify.telegram import get_updates, send_message_single
from src.notify.telegram_bot import apply_command, parse_command


def _process_update(
    upd: dict,
    session: MonitoringSession,
    token: str,
    chat_id: str,
) -> None:
    msg = upd.get("message") or {}
    if str(msg.get("chat", {}).get("id")) != str(chat_id):
        return
    text = msg.get("text", "")
    cmd = parse_command(text)
    now = now_kst()
    response = apply_command(cmd, session, now)
    print(
        f"  [{datetime.now():%H:%M:%S}] 수신: {text!r:30s}"
        f"  →  cmd.kind={cmd.kind:12s}  code={cmd.code}"
    )
    if response:
        print(f"     응답 전송 ({len(response)}자): {response[:120]}")
        resp = send_message_single(token, chat_id, response, parse_mode=None)
        ok = resp and resp.get("ok")
        print(f"     send_message_single → ok={ok}")
    else:
        print(f"     응답 없음 (무시)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=90, help="폴링 지속 시간(초)")
    parser.add_argument(
        "--drain-only",
        action="store_true",
        help="누적된 update 만 1회 fetch 하고 종료",
    )
    args = parser.parse_args(argv)

    s = load_settings()
    if not s.telegram_bot_token or not s.telegram_chat_id:
        print("ERROR: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 미설정", file=sys.stderr)
        return 1

    print(f"chat_id={s.telegram_chat_id}")
    print(f"현재 시각 (KST): {now_kst():%Y-%m-%d %H:%M:%S}")

    session = MonitoringSession()

    if args.drain_only:
        updates = get_updates(s.telegram_bot_token, offset=None, timeout=1)
        print(f"누적된 update: {len(updates)}개\n")
        for upd in updates:
            _process_update(upd, session, s.telegram_bot_token, s.telegram_chat_id)
        return 0

    print(f"▶ 폴링 {args.seconds}초 동안 진행. 봇으로 /list, /pause, 091340 등 보내세요.")
    print("─" * 60)

    offset: int | None = None
    started = time.monotonic()
    cycle = 0
    n_processed = 0
    while time.monotonic() - started < args.seconds:
        remaining = int(args.seconds - (time.monotonic() - started))
        cycle += 1
        # 짧은 timeout (장폴링 5초) — 종료 응답성 확보
        updates = get_updates(s.telegram_bot_token, offset=offset, timeout=5)
        if updates:
            for upd in updates:
                offset = max(offset or 0, upd["update_id"]) + 1
                _process_update(upd, session, s.telegram_bot_token, s.telegram_chat_id)
                n_processed += 1
        else:
            # 빈 사이클 — 너무 자주 찍지 않도록
            if cycle % 4 == 1:
                print(f"  [polling] 사이클#{cycle}  남은 {remaining}s  ({datetime.now():%H:%M:%S})")

    print("─" * 60)
    print(f"종료. 총 처리한 update: {n_processed}개  현재 session.monitored: {len(session.monitored)}")
    print(f"session.paused = {session.paused}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
