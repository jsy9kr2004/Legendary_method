"""SMTP 이메일 발송 모듈.

사후 레포트(16:00) 전용. 마크다운 본문을 그대로 plain text로 발송.
HTML 변환은 v1에서 필요 시 추가.

SMTP 설정:
    호스트/포트는 Settings (환경변수) 에서 주입. 기본값은 Gmail (STARTTLS).
    인증: Gmail 앱 비밀번호 또는 동등한 SMTP 인증 정보.

재시도:
    tenacity 3회, 2~16초 지수 백오프.
    SMTP 연결 오류만 재시도. 인증 실패(535)는 즉시 fail-loud.

보안:
    API 키/토큰은 절대 본문에 포함 안 함 (호출부 책임).
    TLS 강제 (STARTTLS + check_hostname=True).
"""
from __future__ import annotations

import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

_SMTP_TIMEOUT = 30


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=16),
    retry=retry_if_exception_type((smtplib.SMTPException, OSError, TimeoutError)),
    reraise=True,
)
def _send_via_smtp(
    host: str,
    port: int,
    user: str,
    password: str,
    to: str,
    subject: str,
    body: str,
) -> None:
    """SMTP 실제 발송 (내부용). 재시도 데코레이터 적용."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to

    # plain text 파트 (마크다운 그대로)
    msg.attach(MIMEText(body, "plain", "utf-8"))

    context = ssl.create_default_context()
    with smtplib.SMTP(host, port, timeout=_SMTP_TIMEOUT) as smtp:
        smtp.ehlo()
        smtp.starttls(context=context)
        smtp.ehlo()
        smtp.login(user, password)
        smtp.sendmail(user, to, msg.as_string())


def send_email(
    user: str,
    password: str,
    to: str,
    subject: str,
    body: str,
    host: str = "",
    port: int = 0,
) -> dict[str, Any]:
    """이메일 발송.

    Args:
        user: SMTP 계정 (발신자 주소)
        password: SMTP 인증용 앱 비밀번호
        to: 수신자 주소
        subject: 제목
        body: 본문 (마크다운 plain text)
        host: SMTP 호스트 (필수, 빈 문자열이면 발송 스킵)
        port: SMTP 포트 (필수, 0이면 발송 스킵)

    Returns:
        {"ok": True} or {"ok": False, "error": str}
    """
    if not user or not password or not to or not host or port <= 0:
        logger.warning("SMTP 설정 미완료 — 이메일 발송 스킵")
        return {"ok": False, "error": "설정 미완료"}

    try:
        _send_via_smtp(host, port, user, password, to, subject, body)
        logger.info(f"이메일 발송 완료 → {to}  제목: {subject}")
        return {"ok": True}
    except smtplib.SMTPAuthenticationError as e:
        logger.error(f"SMTP 인증 실패 (앱 비밀번호 확인 필요): {e}")
        return {"ok": False, "error": f"인증 실패: {e}"}
    except Exception as e:
        logger.error(f"이메일 발송 실패: {e}")
        return {"ok": False, "error": str(e)}


def build_afterhours_subject(report_date: str) -> str:
    """사후 레포트 이메일 제목 포맷.

    Example: "[종배] 2026-05-06 사후 리뷰"
    """
    return f"[종배] {report_date} 사후 리뷰"
