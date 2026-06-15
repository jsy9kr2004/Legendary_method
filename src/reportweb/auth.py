"""쿠키 기반 비번-only 인증 — 종배 동료 공유용.

- 입력은 **비밀번호 한 칸** (아이디 없음). `/login` 페이지에서 비번 입력 → 검증되면
  쿠키 발급 → 이후 자동 통과. 동료는 비번만 알면 됨.
- 비번 미설정이면 앱이 기동 거부 (create_app 에서 raise) — 인증 없이 공개 방지
  (fail-loud, CLAUDE.md).
- /healthz, /static, /login 은 인증 제외. /api/* 는 미인증 시 401(JSON),
  그 외 페이지는 /login 으로 redirect.
- 쿠키 값 = sha256(password 파생 토큰) — 평문 비번 미저장. HTTPS(Funnel) + HttpOnly.
"""
from __future__ import annotations

import hashlib
import os
import secrets
from urllib.parse import quote

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

_EXEMPT_PREFIXES = ("/healthz", "/static", "/login")
COOKIE_NAME = "rw_auth"
COOKIE_MAX_AGE = 30 * 24 * 3600  # 30일 — 동료 재로그인 빈도 최소화


def get_password() -> str:
    """REPORTWEB_PASSWORD. 미설정 시 ValueError — 호출자(create_app)가 기동 차단."""
    password = os.getenv("REPORTWEB_PASSWORD", "").strip()
    if not password:
        raise ValueError(
            "REPORTWEB_PASSWORD 미설정 — 인증 없이 공개될 수 없음. "
            ".env 에 REPORTWEB_PASSWORD 를 설정하세요."
        )
    return password


def token_for(password: str) -> str:
    """비번 → 쿠키 토큰 (평문 비번을 쿠키에 담지 않기 위한 파생값)."""
    return hashlib.sha256(("rw1:" + password).encode("utf-8")).hexdigest()


def check_password(candidate: str, password: str) -> bool:
    """타이밍 안전 비번 비교."""
    return secrets.compare_digest(candidate, password)


class CookieAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, password: str) -> None:
        super().__init__(app)
        self._token = token_for(password)

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith(_EXEMPT_PREFIXES):
            return await call_next(request)

        cookie = request.cookies.get(COOKIE_NAME, "")
        if cookie and secrets.compare_digest(cookie, self._token):
            return await call_next(request)

        # 미인증 — API 는 401, 페이지는 로그인으로 redirect (원래 경로 보존).
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        nxt = request.url.path
        if request.url.query:
            nxt += "?" + request.url.query
        return RedirectResponse(url=f"/login?next={quote(nxt, safe='')}", status_code=302)
