"""KIS Open API REST 클라이언트.

호출자(fetcher 등)는 `client.get(path, tr_id, params)` 만 사용.
- 토큰 자동 주입 (만료 시 자동 갱신)
- Rate limit (`acquire` 후 호출)
- 네트워크 에러 시 3회 재시도
- KIS 응답의 `rt_cd != "0"` 은 KISApiError 로 raise
- **멀티 계정 자동 라운드 로빈**: settings.kis_credentials 가 N개면
  호출이 들어올 때마다 다음 키를 순환 선택. 호출자 코드 변경 없음.
  KIS rate limit 은 키 기준이라 N개면 capacity 가 N배가 된다.

KIS 응답 표준 구조:
    { "rt_cd": "0", "msg_cd": "...", "msg1": "...", "output": {...} | "output1": [...] }
"""
from __future__ import annotations

import threading
from typing import Any

import httpx
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.config import KisCredential, Settings
from src.kis import auth
from src.kis.rate_limit import RateLimiter, default_limiter, limiter_for


class KISApiError(Exception):
    """KIS 가 rt_cd != '0' 으로 응답한 경우."""

    def __init__(self, rt_cd: str, msg_cd: str, msg: str, payload: dict[str, Any]) -> None:
        super().__init__(f"KIS API error rt_cd={rt_cd} msg_cd={msg_cd} msg={msg}")
        self.rt_cd = rt_cd
        self.msg_cd = msg_cd
        self.msg = msg
        self.payload = payload


class KISClient:
    def __init__(
        self,
        settings: Settings,
        limiter: RateLimiter | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.settings = settings
        self.timeout = timeout
        self._http = httpx.Client(timeout=timeout)

        creds = settings.credentials()
        if not creds:
            raise RuntimeError("KIS_APP_KEY / KIS_APP_SECRET 가 .env 에 비어 있음.")

        # 멀티 키: 각 credential 마다 limiter 별도 (KIS rate limit 이 키 기준).
        # 단일 키 + 외부 limiter 명시 (테스트 등) 인 경우엔 그 limiter 사용.
        if limiter is not None and len(creds) == 1:
            self._slots: list[tuple[KisCredential, RateLimiter]] = [(creds[0], limiter)]
        else:
            if limiter is not None and len(creds) > 1:
                logger.warning(
                    "KISClient: limiter 인자가 무시됨 (멀티 키 모드는 키별 자동 limiter)"
                )
            self._slots = [(c, limiter_for(c, settings.kis_api_mode)) for c in creds]

        self._next_idx = 0
        self._idx_lock = threading.Lock()

        if len(self._slots) > 1:
            labels = ", ".join(c.label for c, _ in self._slots)
            logger.info(f"KISClient 멀티 키 라운드 로빈: {len(self._slots)}개 ({labels})")

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "KISClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    @property
    def base_url(self) -> str:
        return auth.kis_base_url(self.settings.kis_api_mode)

    @property
    def limiter(self) -> RateLimiter:
        """하위 호환: 첫 번째 slot 의 limiter (단일 키 모드 동작과 동일)."""
        return self._slots[0][1]

    def _pick_slot(self) -> tuple[KisCredential, RateLimiter]:
        """라운드 로빈으로 다음 (credential, limiter) 반환."""
        with self._idx_lock:
            slot = self._slots[self._next_idx % len(self._slots)]
            self._next_idx += 1
            return slot

    def _headers(
        self,
        tr_id: str,
        credential: KisCredential,
        custtype: str = "P",
    ) -> dict[str, str]:
        token = auth.get_token(self.settings, credential)
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token.access_token}",
            "appkey": credential.app_key,
            "appsecret": credential.app_secret,
            "tr_id": tr_id,
            "custtype": custtype,
        }

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=10),
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        reraise=True,
    )
    def get(
        self,
        path: str,
        tr_id: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        credential, limiter = self._pick_slot()
        limiter.acquire()
        url = f"{self.base_url}{path}"
        headers = self._headers(tr_id, credential)
        resp = self._http.get(url, headers=headers, params=params or {})
        resp.raise_for_status()
        payload = resp.json()

        rt_cd = payload.get("rt_cd")
        if rt_cd is not None and rt_cd != "0":
            err = KISApiError(
                rt_cd=rt_cd,
                msg_cd=payload.get("msg_cd", ""),
                msg=payload.get("msg1", ""),
                payload=payload,
            )
            logger.warning(f"{err} (label={credential.label})")
            raise err
        return payload
