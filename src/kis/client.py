"""KIS Open API REST 클라이언트.

호출자(fetcher 등)는 `client.get(path, tr_id, params)` 만 사용.
- 토큰 자동 주입 (만료 시 자동 갱신)
- Rate limit (`acquire` 후 호출)
- 네트워크 에러 시 3회 재시도
- KIS 응답의 `rt_cd != "0"` 은 KISApiError 로 raise

KIS 응답 표준 구조:
    { "rt_cd": "0", "msg_cd": "...", "msg1": "...", "output": {...} | "output1": [...] }
"""
from __future__ import annotations

from typing import Any

import httpx
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.config import Settings
from src.kis import auth
from src.kis.rate_limit import RateLimiter, default_limiter


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
        self.limiter = limiter or default_limiter(settings.kis_api_mode)
        self.timeout = timeout
        self._http = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "KISClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    @property
    def base_url(self) -> str:
        return auth.kis_base_url(self.settings.kis_api_mode)

    def _headers(self, tr_id: str, custtype: str = "P") -> dict[str, str]:
        token = auth.get_token(self.settings)
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token.access_token}",
            "appkey": self.settings.kis_app_key,
            "appsecret": self.settings.kis_app_secret,
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
        self.limiter.acquire()
        url = f"{self.base_url}{path}"
        headers = self._headers(tr_id)
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
            logger.warning(str(err))
            raise err
        return payload
