"""KIS Open API 토큰 관리.

발급(`/oauth2/tokenP`) → 파일 캐시(`{DATA_DIR}/meta/kis_token_<mode>_<keyhash>.json`)
→ 만료 5분 전 자동 재발급.

멀티 계정 지원: credential 별로 토큰이 다르므로 캐시도 분리한다.
api_mode (real/mock) 도 같이 캐시 키에 포함.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config import KisCredential, Settings

_TOKEN_ENDPOINT = "/oauth2/tokenP"
_REFRESH_BUFFER = timedelta(minutes=5)

REAL_BASE_URL = "https://openapi.koreainvestment.com:9443"
MOCK_BASE_URL = "https://openapivts.koreainvestment.com:29443"


@dataclass(frozen=True)
class Token:
    access_token: str
    expires_at: datetime
    api_mode: str

    def is_valid(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        return now + _REFRESH_BUFFER < self.expires_at


def kis_base_url(api_mode: str) -> str:
    return REAL_BASE_URL if api_mode == "real" else MOCK_BASE_URL


def _resolve_credential(settings: Settings, credential: KisCredential | None) -> KisCredential:
    """credential 이 명시 안 됐으면 settings 의 첫 번째 (단일 키 호환)."""
    if credential is not None:
        return credential
    creds = settings.credentials()
    if not creds:
        raise RuntimeError("KIS_APP_KEY / KIS_APP_SECRET 가 .env 에 비어 있음.")
    return creds[0]


def _token_path(settings: Settings, credential: KisCredential) -> Path:
    filename = f"kis_token_{settings.kis_api_mode}_{credential.cache_id}.json"
    return settings.data_dir / "meta" / filename


def _load_cached(settings: Settings, credential: KisCredential) -> Token | None:
    path = _token_path(settings, credential)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if data.get("api_mode") != settings.kis_api_mode:
        return None
    try:
        expires_at = datetime.fromisoformat(data["expires_at"])
    except (KeyError, ValueError):
        return None
    return Token(
        access_token=data["access_token"],
        expires_at=expires_at,
        api_mode=data["api_mode"],
    )


def _save_cache(token: Token, settings: Settings, credential: KisCredential) -> None:
    path = _token_path(settings, credential)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "access_token": token.access_token,
                "expires_at": token.expires_at.isoformat(),
                "api_mode": token.api_mode,
            }
        )
    )


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10), reraise=True)
def _request_new_token(settings: Settings, credential: KisCredential) -> Token:
    url = f"{kis_base_url(settings.kis_api_mode)}{_TOKEN_ENDPOINT}"
    body = {
        "grant_type": "client_credentials",
        "appkey": credential.app_key,
        "appsecret": credential.app_secret,
    }
    resp = httpx.post(url, json=body, timeout=10.0)
    resp.raise_for_status()
    payload = resp.json()

    if "access_token" not in payload:
        raise RuntimeError(
            f"KIS 토큰 발급 실패 (mode={settings.kis_api_mode}, "
            f"label={credential.label}): {payload}"
        )

    expires_in = int(payload.get("expires_in", 86400))
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    return Token(
        access_token=payload["access_token"],
        expires_at=expires_at,
        api_mode=settings.kis_api_mode,
    )


def get_token(
    settings: Settings,
    credential: KisCredential | None = None,
    force_refresh: bool = False,
) -> Token:
    """유효한 토큰 반환. 캐시에 있고 유효하면 그대로, 아니면 새로 발급.

    credential 미지정 시 settings 의 첫 번째 credential 사용 (단일 키 모드 호환).
    """
    cred = _resolve_credential(settings, credential)

    if not force_refresh:
        cached = _load_cached(settings, cred)
        if cached is not None and cached.is_valid():
            return cached

    logger.info(
        f"KIS 토큰 신규 발급 (mode={settings.kis_api_mode}, label={cred.label})"
    )
    token = _request_new_token(settings, cred)
    _save_cache(token, settings, cred)
    return token
