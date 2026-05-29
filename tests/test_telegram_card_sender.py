"""TelegramCardSender — 전용 송출 쓰레드 단위 테스트 (2026-05-29).

검증:
  - 신규 종목 → sendMessage + message_id 기록
  - 기존 종목 → editMessageText
  - dedup — 동일 텍스트 재요청 시 재송출 X
  - latest-wins — 송출 전 여러 갱신 쌓이면 최신 1개만
  - 429 Retry-After — sender 쓰레드만 sleep, 재시도
  - remove_card / clear_all — deleteMessage + message_ids 정리
  - oneshot (STRONG 푸시) — sendMessage 별도 발송

httpx.Client 는 mock. 쓰레드 처리 완료는 짧은 폴링으로 대기 (deterministic).
"""
from __future__ import annotations

import threading
import time
from typing import Any
from unittest.mock import MagicMock, patch

from src.dashboard.telegram_sender import TelegramCardSender


class _FakeResp:
    def __init__(self, status_code: int = 200, message_id: int | None = None,
                 text: str = "", retry_after: float | None = None):
        self.status_code = status_code
        self._message_id = message_id
        self.text = text
        self.headers: dict[str, str] = {}
        if retry_after is not None:
            self.headers["retry-after"] = str(retry_after)
        self._retry_after = retry_after

    def json(self) -> dict[str, Any]:
        if self.status_code == 429:
            params = {}
            if self._retry_after is not None:
                params["retry_after"] = self._retry_after
            return {"ok": False, "parameters": params}
        result = {}
        if self._message_id is not None:
            result["message_id"] = self._message_id
        return {"ok": True, "result": result}


def _wait_until(pred, timeout: float = 2.0) -> bool:
    """pred() 가 True 가 될 때까지 폴링 (쓰레드 처리 완료 대기)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.005)
    return False


def _make_sender(post_mock: MagicMock, message_ids: dict | None = None) -> TelegramCardSender:
    fake_client = MagicMock()
    fake_client.post = post_mock
    with patch("src.dashboard.telegram_sender.httpx.Client", return_value=fake_client):
        return TelegramCardSender("token", "chat", message_ids if message_ids is not None else {})


def test_new_card_sends_and_records_message_id():
    post = MagicMock(return_value=_FakeResp(200, message_id=555))
    mids: dict = {}
    s = _make_sender(post, mids)
    try:
        s.update_card("075180", "카드 텍스트")
        assert _wait_until(lambda: mids.get("075180") == 555)
        url = post.call_args[0][0]
        assert url.endswith("/sendMessage")
    finally:
        s.stop()


def test_existing_card_edits():
    post = MagicMock(return_value=_FakeResp(200))
    mids = {"075180": 999}
    s = _make_sender(post, mids)
    try:
        s.update_card("075180", "갱신 텍스트")
        assert _wait_until(lambda: post.call_count >= 1)
        url = post.call_args[0][0]
        assert url.endswith("/editMessageText")
        # message_id 가 페이로드에 포함
        assert post.call_args.kwargs["json"]["message_id"] == 999
    finally:
        s.stop()


def test_dedup_skips_identical_text():
    post = MagicMock(return_value=_FakeResp(200, message_id=10))
    s = _make_sender(post, {})
    try:
        s.update_card("A", "same")
        assert _wait_until(lambda: post.call_count == 1)
        # 동일 텍스트 재요청 — 재송출 X
        s.update_card("A", "same")
        time.sleep(0.1)
        assert post.call_count == 1
        # 다른 텍스트 — edit 발생
        s.update_card("A", "different")
        assert _wait_until(lambda: post.call_count == 2)
        assert post.call_args[0][0].endswith("/editMessageText")
    finally:
        s.stop()


def test_latest_wins_when_updates_stack():
    """송출이 진행 중일 때 여러 갱신이 쌓이면 drain 시점 최신만 보낸다."""
    gate = threading.Event()
    seen_texts: list[str] = []

    def slow_post(url, **kwargs):
        seen_texts.append(kwargs["json"].get("text", ""))
        # 첫 호출에서 gate 가 열릴 때까지 막아 후속 갱신을 쌓이게 함.
        if len(seen_texts) == 1:
            gate.wait(1.0)
        return _FakeResp(200, message_id=1)

    post = MagicMock(side_effect=slow_post)
    s = _make_sender(post, {})
    try:
        s.update_card("A", "v1")          # 첫 송출 — slow_post 안에서 block
        assert _wait_until(lambda: len(seen_texts) == 1)
        # block 동안 v2, v3 쌓기 — latest-wins 로 v3 만 다음 배치에서 송출돼야.
        s.update_card("A", "v2")
        s.update_card("A", "v3")
        gate.set()
        assert _wait_until(lambda: "v3" in seen_texts)
        # v2 는 건너뛰어졌다 (latest-wins).
        assert "v2" not in seen_texts
    finally:
        gate.set()
        s.stop()


def test_429_retry_after_sleeps_then_retries():
    calls = {"n": 0}

    def post_fn(url, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeResp(429, retry_after=0.01)
        return _FakeResp(200, message_id=7)

    post = MagicMock(side_effect=post_fn)
    mids: dict = {}
    with patch("src.dashboard.telegram_sender.time.sleep") as sleep_mock:
        s = _make_sender(post, mids)
        try:
            s.update_card("A", "txt")
            assert _wait_until(lambda: mids.get("A") == 7)
            # 429 → sleep 1회 (retry_after 0.01) → 재시도 후 성공
            assert sleep_mock.called
            assert calls["n"] == 2
        finally:
            s.stop()


def test_remove_card_deletes_message():
    post = MagicMock(return_value=_FakeResp(200))
    mids = {"A": 321}
    s = _make_sender(post, mids)
    try:
        s.remove_card("A")
        assert _wait_until(lambda: "A" not in mids)
        assert post.call_args[0][0].endswith("/deleteMessage")
    finally:
        s.stop()


def test_clear_all_deletes_every_message():
    post = MagicMock(return_value=_FakeResp(200))
    mids = {"A": 1, "B": 2, "C": 3}
    s = _make_sender(post, mids)
    try:
        s.clear_all()
        assert _wait_until(lambda: len(mids) == 0)
        # 3종목 모두 deleteMessage
        delete_calls = [c for c in post.call_args_list if c[0][0].endswith("/deleteMessage")]
        assert len(delete_calls) == 3
    finally:
        s.stop()


def test_oneshot_sends_separate_message():
    post = MagicMock(return_value=_FakeResp(200, message_id=88))
    mids: dict = {}
    s = _make_sender(post, mids)
    try:
        s.push_oneshot("🚨 STRONG 알림")
        assert _wait_until(lambda: post.call_count >= 1)
        url = post.call_args[0][0]
        assert url.endswith("/sendMessage")
        assert post.call_args.kwargs["json"]["text"] == "🚨 STRONG 알림"
        # oneshot 은 message_ids 에 기록 X (카드 아님).
        assert mids == {}
    finally:
        s.stop()
