"""HTTP Basic auth 미들웨어 — 종배 동료 공유용 공유 비밀번호.

- 공유 비번 1개 (env REPORTWEB_PASSWORD). **아이디는 무시** — 동료는 브라우저 인증창의
  아이디 칸에 아무거나(또는 공백) 넣고 비번만 입력하면 됨 (REPORTWEB_USER 는 미사용).
- 비번 미설정이면 앱이 기동 거부 (create_app 에서 raise) — 공개 사이트가 인증 없이
  뜨는 사고 방지 (fail-loud, CLAUDE.md).
- /healthz, /static 은 인증 제외 (민감 데이터 없음 — 헬스체크/CSS·JS).
- URL 비밀이 아니라 비번이 자물쇠 (Funnel 호스트네임은 CT 로그로 발견 가능).
"""
from __future__ import annotations

import base64
import binascii
import os
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

_EXEMPT_PREFIXES = ("/healthz", "/static")
_REALM = "Jongbae Reports"


def get_credentials() -> tuple[str, str]:
    """(user, password). 비번 미설정 시 ValueError — 호출자(create_app)가 기동 차단."""
    password = os.getenv("REPORTWEB_PASSWORD", "").strip()
    if not password:
        raise ValueError(
            "REPORTWEB_PASSWORD 미설정 — 인증 없이 공개될 수 없음. "
            ".env 에 REPORTWEB_PASSWORD 를 설정하세요."
        )
    user = os.getenv("REPORTWEB_USER", "jongbae").strip() or "jongbae"
    return user, password


class BasicAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, user: str, password: str) -> None:
        super().__init__(app)
        self._user = user
        self._password = password

    def _unauthorized(self) -> Response:
        return PlainTextResponse(
            "인증 필요",
            status_code=401,
            headers={"WWW-Authenticate": f'Basic realm="{_REALM}", charset="UTF-8"'},
        )

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith(_EXEMPT_PREFIXES):
            return await call_next(request)

        header = request.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return self._unauthorized()
        try:
            decoded = base64.b64decode(header[6:]).decode("utf-8")
            user, _, password = decoded.partition(":")
        except (binascii.Error, UnicodeDecodeError):
            return self._unauthorized()

        # 비번만 검증 (아이디는 무시 — 동료는 아이디 칸에 아무거나 넣고 비번만 공유).
        # user 변수는 파싱만 하고 사용 X.
        if not secrets.compare_digest(password, self._password):
            return self._unauthorized()

        return await call_next(request)
