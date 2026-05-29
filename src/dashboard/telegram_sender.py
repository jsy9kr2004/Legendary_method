"""텔레그램 카드 송출 전용 백그라운드 쓰레드 (2026-05-29).

배경 — tick 블로킹 제거:
    기존 `dashboard_tick()` 는 monitored 종목마다 `edit_message()` 를 **동기**
    호출했다. 그 호출은 매번 새 `httpx.Client()` 를 만들어 TCP+TLS 핸드셰이크를
    반복했고, 텔레그램 429(rate limit) 시 tenacity 가 tick 쓰레드 안에서 지수
    백오프(최대 8초 × 3회) 로 잠들었다. 종목 6~10개가 직렬로 이걸 겪으면 한 tick
    이 수십 초로 늘어나 — 카드(텔레그램)뿐 아니라 같은 tick 안에서 갱신되는 PWA
    `last_payloads` 와 STRONG 푸시까지 전부 30초씩 밀렸다.

해결 — 송출을 tick 에서 분리:
    tick 은 카드 텍스트를 이 sender 에 **enqueue 만 하고 즉시 리턴**한다. 실제
    네트워크 송출은 전용 데몬 쓰레드가 처리한다.

설계 원칙:
    1) **영속 연결** — `httpx.Client` 1개 재사용 (핸드셰이크 1회).
    2) **latest-wins** — 같은 종목에 대해 sender 가 따라잡기 전 여러 갱신이 쌓이면
       가장 최신 텍스트 1개만 보낸다. 부하 자동 shedding.
    3) **dedup** — 직전에 실제로 보낸 텍스트와 동일하면 edit skip (텔레그램
       'message is not modified' 400 + 불필요 호출 회피).
    4) **429 격리** — rate limit 시 `Retry-After` 만큼 **sender 쓰레드만** sleep.
       tick 은 절대 영향 X.
    5) **푸시 정책 유지** — 카드는 editMessageText (푸시 X), 신규 종목만
       sendMessage, STRONG 알림만 별도 send (푸시 O). `docs/dashboard-pwa.md` /
       CLAUDE.md 알림 채널 정책 그대로.

message_id 소유권:
    sender 가 `message_ids`(code→message_id) dict 를 단독으로 read/write 한다.
    send→id 기록 / edit→id 조회 / delete→pop 모두 sender 쓰레드에서만 일어나
    경쟁 없음. /off·종료 시 카드 정리도 `clear_all()` 로 sender 쓰레드에 위임.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any

import httpx
from loguru import logger

_API_BASE = "https://api.telegram.org"
_TIMEOUT = 15.0
_MAX_LEN = 4096
# 429 Retry-After 헤더 없을 때 기본 대기 (초). 텔레그램 chat 당 분당 ~30 edit.
_DEFAULT_RETRY_AFTER = 2.0
# wake 이벤트 없이도 주기적으로 큐 확인 (Retry-After 재개 등 안전망).
_IDLE_POLL_SEC = 1.0


class TelegramCardSender:
    """카드 edit / 신규 send / 삭제 / STRONG 푸시를 전담하는 단일 데몬 쓰레드.

    tick 쓰레드는 `update_card` / `push_oneshot` / `remove_card` 로 작업을
    적재만 하고 즉시 리턴한다. 네트워크 I/O 와 429 백오프는 전부 이 쓰레드 안.
    """

    def __init__(self, token: str, chat_id: str, message_ids: dict[str, int]):
        self._token = token
        self._chat_id = chat_id
        # 외부(scheduler)와 공유하는 dict 참조. write 는 이 쓰레드에서만.
        self._message_ids = message_ids
        self._client = httpx.Client(timeout=_TIMEOUT)

        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()

        # latest-wins: code → 최신 카드 텍스트 (아직 안 보낸 것)
        self._pending: dict[str, str] = {}
        # FIFO: STRONG 푸시 등 일회성 메시지 (푸시 O)
        self._oneshots: deque[str] = deque()
        # FIFO: 카드 삭제 요청 code
        self._removals: deque[str] = deque()
        self._clear_all_req = False
        # dedup: code → 마지막으로 실제 보낸 텍스트
        self._last_sent: dict[str, str] = {}

        self._thread = threading.Thread(
            target=self._run, name="telegram-card-sender", daemon=True
        )
        self._thread.start()

    # ── tick 쓰레드가 호출하는 enqueue API (모두 non-blocking) ──────────────

    def update_card(self, code: str, text: str) -> None:
        """종목 카드 텍스트 갱신 요청 (latest-wins)."""
        with self._lock:
            self._pending[code] = text
        self._wake.set()

    def push_oneshot(self, text: str) -> None:
        """일회성 메시지 발송 요청 (STRONG 알림 등 — 푸시 O)."""
        with self._lock:
            self._oneshots.append(text)
        self._wake.set()

    def remove_card(self, code: str) -> None:
        """모니터링에서 빠진 종목 카드 삭제 요청."""
        with self._lock:
            self._pending.pop(code, None)
            self._last_sent.pop(code, None)
            self._removals.append(code)
        self._wake.set()

    def clear_all(self) -> None:
        """모든 카드 삭제 요청 (/off · 종료 시). sender 쓰레드에서 일괄 처리."""
        with self._lock:
            self._pending.clear()
            self._clear_all_req = True
        self._wake.set()

    def stop(self, timeout: float = 3.0) -> None:
        """쓰레드 종료 + 연결 정리."""
        self._stop.set()
        self._wake.set()
        if self._thread.is_alive():
            self._thread.join(timeout=timeout)
        try:
            self._client.close()
        except Exception:  # noqa: BLE001
            pass

    # ── 내부 쓰레드 루프 ────────────────────────────────────────────────────

    def _run(self) -> None:
        while True:
            # wake 대기 (작업 없으면 IDLE_POLL_SEC 마다 깨어 안전 확인).
            self._wake.wait(timeout=_IDLE_POLL_SEC)
            self._wake.clear()

            # 작업 스냅샷 — lock 은 짧게, 네트워크 I/O 는 lock 밖에서.
            with self._lock:
                clear_all = self._clear_all_req
                self._clear_all_req = False
                removals = list(self._removals)
                self._removals.clear()
                oneshots = list(self._oneshots)
                self._oneshots.clear()
                pending = self._pending
                self._pending = {}

            try:
                if clear_all:
                    self._do_clear_all()
                for code in removals:
                    self._do_delete(code)
                for text in oneshots:
                    self._do_oneshot(text)
                # latest-wins: drain 시점의 최신 텍스트만 1회 송출.
                for code, text in pending.items():
                    self._do_send_or_edit(code, text)
            except Exception as e:  # noqa: BLE001 — 루프는 절대 죽지 않는다.
                logger.warning(f"[card-sender] 송출 루프 예외: {e}")

            # stop 은 큐를 한 번 drain 한 *뒤* 확인 — 종료 시 clear_all 등 마지막
            # 작업이 누락되지 않도록 보장.
            if self._stop.is_set():
                break

    # ── 실제 네트워크 송출 (sender 쓰레드 전용) ─────────────────────────────

    def _post(self, path: str, payload: dict[str, Any]) -> httpx.Response | None:
        """429 Retry-After 를 sender 쓰레드 안에서 흡수하며 POST.

        429 면 헤더만큼(없으면 기본) sleep 후 1회 재시도. 그래도 429 면 포기
        (다음 tick 갱신이 어차피 따라옴 — latest-wins 라 stale 안 쌓임).
        """
        url = f"{_API_BASE}/bot{self._token}{path}"
        for attempt in range(2):
            try:
                resp = self._client.post(url, json=payload, timeout=_TIMEOUT)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[card-sender] POST {path} 네트워크 실패: {e}")
                return None
            if resp.status_code == 429:
                retry_after = _DEFAULT_RETRY_AFTER
                try:
                    params = resp.json().get("parameters", {})
                    retry_after = float(params.get("retry_after", _DEFAULT_RETRY_AFTER))
                except Exception:  # noqa: BLE001
                    hdr = resp.headers.get("retry-after")
                    if hdr:
                        try:
                            retry_after = float(hdr)
                        except ValueError:
                            pass
                if attempt == 0:
                    logger.debug(f"[card-sender] 429 — {retry_after}s 대기 후 재시도")
                    time.sleep(retry_after)
                    continue
                logger.warning(f"[card-sender] 429 재시도 후에도 실패 ({path})")
                return None
            return resp
        return None

    def _do_send_or_edit(self, code: str, text: str) -> None:
        text = text[:_MAX_LEN]
        # dedup — 직전 송출과 동일하면 skip (not modified 400 회피).
        if self._last_sent.get(code) == text:
            return
        msg_id = self._message_ids.get(code)
        if msg_id is not None:
            resp = self._post(
                "/editMessageText",
                {"chat_id": self._chat_id, "message_id": msg_id, "text": text},
            )
            if resp is None:
                return
            if resp.status_code == 400:
                body = resp.text
                if "not modified" in body:
                    self._last_sent[code] = text
                    return
                logger.warning(f"[card-sender] editMessageText 400: {body[:200]}")
                return
            if resp.status_code == 200:
                self._last_sent[code] = text
            return
        # 신규 — sendMessage 후 id 기록.
        resp = self._post(
            "/sendMessage", {"chat_id": self._chat_id, "text": text}
        )
        if resp is None or resp.status_code != 200:
            if resp is not None and resp.status_code == 400:
                logger.error(f"[card-sender] sendMessage 400: {resp.text[:200]}")
            return
        new_id = resp.json().get("result", {}).get("message_id")
        if isinstance(new_id, int):
            self._message_ids[code] = new_id
            self._last_sent[code] = text

    def _do_oneshot(self, text: str) -> None:
        resp = self._post(
            "/sendMessage", {"chat_id": self._chat_id, "text": text[:_MAX_LEN]}
        )
        if resp is not None and resp.status_code == 400:
            logger.error(f"[card-sender] oneshot 400: {resp.text[:200]}")

    def _do_delete(self, code: str) -> None:
        msg_id = self._message_ids.pop(code, None)
        self._last_sent.pop(code, None)
        if msg_id is not None:
            self._post(
                "/deleteMessage", {"chat_id": self._chat_id, "message_id": msg_id}
            )

    def _do_clear_all(self) -> None:
        for code in list(self._message_ids.keys()):
            self._do_delete(code)
