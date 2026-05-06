"""텔레그램 봇 발송 모듈.

Bot API: https://api.telegram.org/bot{token}/sendMessage
parse_mode: "Markdown" (MarkdownV2는 이스케이프 규칙 복잡해서 사용 안 함)

4096자 제한 자동 분할:
    sendMessage 실패 없이 여러 메시지로 나눠 발송.
    split_messages(text) 로 분할 후 순서대로 전송.

재시도:
    tenacity 3회, 1~8초 지수 백오프.
    네트워크/5xx 에러만 재시도. 400 Bad Request 는 즉시 fail-loud.

에러 알림:
    send_error_alert()는 parse_mode 없이 plain text로 발송
    (마크다운 이스케이프 오류로 에러 알림까지 실패하는 상황 방지).
"""
from __future__ import annotations

import time
from typing import Any

import httpx
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

_API_BASE = "https://api.telegram.org"
_SEND_MESSAGE_PATH = "/bot{token}/sendMessage"
_TIMEOUT = 15.0
_MAX_LEN = 4096


def _split_text(text: str, max_len: int = _MAX_LEN) -> list[str]:
    """텍스트를 max_len 이하로 분할.

    줄 경계(\n)를 기준으로 자른다. 단일 줄이 max_len 초과이면 강제 분할.
    """
    if len(text) <= max_len:
        return [text]

    parts: list[str] = []
    current = ""
    for line in text.split("\n"):
        candidate = current + ("\n" if current else "") + line
        if len(candidate) > max_len:
            if current:
                parts.append(current)
            # 단일 줄이 max_len 초과이면 강제 분할
            while len(line) > max_len:
                parts.append(line[:max_len])
                line = line[max_len:]
            current = line
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=8),
    retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
    reraise=True,
)
def _send_one(
    client: httpx.Client,
    token: str,
    chat_id: str,
    text: str,
    parse_mode: str | None = "Markdown",
) -> dict[str, Any]:
    """단일 메시지 전송 (내부용)."""
    url = f"{_API_BASE}{_SEND_MESSAGE_PATH.format(token=token)}"
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode

    resp = client.post(url, json=payload, timeout=_TIMEOUT)

    # 400은 마크다운 문법 오류일 가능성 높음 → 재시도 안 함
    if resp.status_code == 400:
        logger.error(f"텔레그램 400 Bad Request: {resp.text[:200]}")
        return {"ok": False, "error": resp.text}

    resp.raise_for_status()
    return resp.json()


def send_message(
    token: str,
    chat_id: str,
    text: str,
    parse_mode: str | None = "Markdown",
    interval_sec: float = 0.3,
) -> list[dict[str, Any]]:
    """텔레그램 메시지 발송. 4096자 초과 시 자동 분할.

    Args:
        token: 봇 토큰
        chat_id: 채팅 ID (개인 또는 그룹)
        text: 발송할 마크다운 텍스트
        parse_mode: "Markdown" | None
        interval_sec: 분할 메시지 간 발송 간격 (텔레그램 rate limit 대응)

    Returns:
        각 메시지별 API 응답 리스트.
    """
    if not token or not chat_id:
        logger.warning("텔레그램 토큰 또는 chat_id 미설정 — 발송 스킵")
        return []

    parts = _split_text(text)
    results = []
    with httpx.Client() as client:
        for i, part in enumerate(parts):
            try:
                result = _send_one(client, token, chat_id, part, parse_mode)
                results.append(result)
                logger.info(f"텔레그램 발송 완료 ({i+1}/{len(parts)}): {len(part)}자")
            except Exception as e:
                logger.error(f"텔레그램 발송 실패 ({i+1}/{len(parts)}): {e}")
                results.append({"ok": False, "error": str(e)})
            if i < len(parts) - 1:
                time.sleep(interval_sec)

    return results


def send_error_alert(
    token: str,
    chat_id: str,
    error_msg: str,
    context: str = "",
) -> dict[str, Any]:
    """시스템 장애 에러 알림 발송.

    parse_mode=None (plain text). 마크다운 파싱 오류로 에러 알림까지
    실패하는 상황을 방지.

    Returns:
        단일 API 응답 dict. 발송 실패해도 예외 raise 안 함 (이중 실패 방지).
    """
    ctx_str = f"\n컨텍스트: {context}" if context else ""
    text = f"⚠️ [에러] 시스템 장애 감지{ctx_str}\n\n{error_msg}"
    text = text[:_MAX_LEN]  # 에러 메시지는 분할 없이 잘라냄

    if not token or not chat_id:
        logger.warning("텔레그램 미설정 — 에러 알림 발송 스킵")
        return {}

    try:
        with httpx.Client() as client:
            return _send_one(client, token, chat_id, text, parse_mode=None)
    except Exception as e:
        logger.error(f"에러 알림 발송도 실패: {e}")
        return {"ok": False, "error": str(e)}
