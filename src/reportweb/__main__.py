"""종배 레포트 웹사이트 실행 진입점.

    python -m src.reportweb

환경변수:
    REPORTWEB_PASSWORD : 공유 비밀번호 (필수 — 미설정 시 기동 거부)
    REPORTWEB_USER     : 사용자명 (기본 jongbae)
    REPORTWEB_HOST     : bind 호스트 (기본 127.0.0.1 — Funnel/serve 가 앞단)
    REPORTWEB_PORT     : 포트 (기본 8001 — 단타 PWA 8000 과 분리)
    DATA_DIR           : 데이터 루트 (load_settings 기준)

외부 공유 (종배 동료): 별도로 Tailscale Funnel 을 8001 에 연결.
    tailscale funnel 8001
→ https://<머신>.<tailnet>.ts.net 고정 URL + TLS. 본인 IP 비노출.
"""
from __future__ import annotations

import os
import sys

import uvicorn
from loguru import logger

from src.config import load_settings
from src.reportweb.app import create_app


def main() -> None:
    settings = load_settings()
    host = os.getenv("REPORTWEB_HOST", "127.0.0.1")
    port = int(os.getenv("REPORTWEB_PORT", "8001"))
    try:
        app = create_app(settings.data_dir)
    except ValueError as e:
        logger.error(f"[reportweb] 기동 실패: {e}")
        sys.exit(1)
    logger.info(f"[reportweb] 종배 레포트 사이트 — http://{host}:{port}/ (DATA_DIR={settings.data_dir})")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
