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
_EDIT_MESSAGE_PATH = "/bot{token}/editMessageText"
_DELETE_MESSAGE_PATH = "/bot{token}/deleteMessage"
_GET_UPDATES_PATH = "/bot{token}/getUpdates"
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


def send_message_single(
    token: str,
    chat_id: str,
    text: str,
    parse_mode: str | None = "Markdown",
    disable_notification: bool = False,
) -> dict[str, Any] | None:
    """단일 메시지 발송 (분할 없음, message_id 반환용).

    M6 모니터링에서 종목당 메시지 1개를 만들어 두고 이후 editMessageText 로
    갱신하기 위해 message_id 가 필요. 분할 발송하면 첫 메시지의 id 만
    추적 가능 — 본 함수는 4096자 잘라서 단일 발송.

    Returns:
        Telegram API 응답 dict. result.message_id 에 메시지 ID.
        실패 시 None.
    """
    if not token or not chat_id:
        logger.warning("텔레그램 토큰 또는 chat_id 미설정 — 발송 스킵")
        return None

    text = text[:_MAX_LEN]
    try:
        with httpx.Client() as client:
            url = f"{_API_BASE}{_SEND_MESSAGE_PATH.format(token=token)}"
            payload: dict[str, Any] = {
                "chat_id": chat_id,
                "text": text,
                "disable_notification": disable_notification,
            }
            if parse_mode:
                payload["parse_mode"] = parse_mode
            resp = client.post(url, json=payload, timeout=_TIMEOUT)
            if resp.status_code == 400:
                logger.error(f"텔레그램 400: {resp.text[:200]}")
                return None
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.error(f"텔레그램 단일 발송 실패: {e}")
        return None


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=8),
    retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
    reraise=True,
)
def edit_message(
    token: str,
    chat_id: str,
    message_id: int,
    text: str,
    parse_mode: str | None = "Markdown",
) -> dict[str, Any] | None:
    """기존 메시지 텍스트 갱신 (M6 모니터링 — 푸시 알림 없음).

    같은 chat_id 에 같은 message_id 로 보내면 Telegram 이 메시지를 in-place
    갱신. 사용자에게 알림 푸시는 발생하지 않음 (rate limit 분당 30/chat).

    동일 텍스트로 edit 시 Telegram 이 400 'message is not modified' 반환 →
    None 반환.

    Returns:
        Telegram API 응답 dict 또는 None.
    """
    if not token or not chat_id:
        return None
    text = text[:_MAX_LEN]
    try:
        with httpx.Client() as client:
            url = f"{_API_BASE}{_EDIT_MESSAGE_PATH.format(token=token)}"
            payload: dict[str, Any] = {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
            }
            if parse_mode:
                payload["parse_mode"] = parse_mode
            resp = client.post(url, json=payload, timeout=_TIMEOUT)
            if resp.status_code == 400:
                # "message is not modified" 또는 메시지가 삭제됨 — 정상 무시
                body = resp.text
                if "not modified" in body:
                    return None
                logger.warning(f"editMessageText 400: {body[:200]}")
                return None
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.error(f"텔레그램 메시지 편집 실패 (message_id={message_id}): {e}")
        return None


def delete_message(token: str, chat_id: str, message_id: int) -> bool:
    """메시지 삭제 (모니터링 종목 해제 시).

    Returns:
        삭제 성공 여부. 이미 삭제된 메시지는 False (Telegram 400).
    """
    if not token or not chat_id:
        return False
    try:
        with httpx.Client() as client:
            url = f"{_API_BASE}{_DELETE_MESSAGE_PATH.format(token=token)}"
            payload = {"chat_id": chat_id, "message_id": message_id}
            resp = client.post(url, json=payload, timeout=_TIMEOUT)
            return resp.status_code == 200
    except Exception as e:
        logger.error(f"텔레그램 메시지 삭제 실패 (message_id={message_id}): {e}")
        return False


def get_updates(
    token: str,
    offset: int | None = None,
    timeout: int = 30,
) -> list[dict[str, Any]]:
    """getUpdates long polling — 사용자 명령 수신.

    Args:
        token: 봇 토큰.
        offset: 마지막 처리한 update_id + 1. None 이면 전체.
        timeout: long polling 대기 초 (서버측). 30초 권장.

    Returns:
        업데이트 dict 리스트. 각 항목에 update_id, message {chat.id, text, ...}.
        실패 시 빈 리스트.
    """
    if not token:
        return []
    try:
        with httpx.Client() as client:
            url = f"{_API_BASE}{_GET_UPDATES_PATH.format(token=token)}"
            params: dict[str, Any] = {"timeout": timeout}
            if offset is not None:
                params["offset"] = offset
            resp = client.get(url, params=params, timeout=timeout + 5)
            resp.raise_for_status()
            data = resp.json()
            return list(data.get("result") or [])
    except Exception as e:
        logger.warning(f"getUpdates 실패: {e}")
        return []


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
