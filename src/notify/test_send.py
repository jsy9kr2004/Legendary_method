"""텔레그램 연결 테스트 CLI.

사용:
    python -m src.notify.test_send
    python -m src.notify.test_send "커스텀 메시지"

.env에서 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 자동 로드.
"""
from __future__ import annotations

import sys
from datetime import datetime

from src.config import load_settings
from src.notify.telegram import send_message


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    text = argv[0] if argv else f"✅ 연결 테스트 ({datetime.now():%Y-%m-%d %H:%M:%S})"

    s = load_settings()
    if not s.telegram_bot_token:
        print("ERROR: .env의 TELEGRAM_BOT_TOKEN이 비어 있습니다.", file=sys.stderr)
        return 1
    if not s.telegram_chat_id:
        print("ERROR: .env의 TELEGRAM_CHAT_ID가 비어 있습니다.", file=sys.stderr)
        return 1

    print(f"발송 → chat_id={s.telegram_chat_id}, 길이={len(text)}자")
    results = send_message(s.telegram_bot_token, s.telegram_chat_id, text)
    for r in results:
        print(r)
    return 0 if all(r.get("ok") for r in results) else 2


if __name__ == "__main__":
    sys.exit(main())
