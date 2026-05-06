"""통합 발송기.

Settings에서 토큰/SMTP 정보를 읽어 텔레그램/이메일을 발송.
레포트 생성기(src/report/)와 발송 채널(telegram/email) 사이의 접착층.

사용 패턴:
    from src.notify.dispatcher import Dispatcher
    from src.config import load_settings

    d = Dispatcher(load_settings())
    d.telegram(report_text)           # 텔레그램 발송
    d.telegram_error("오류 내용")      # 에러 알림
    d.email(subject, body)            # 이메일 발송

DRY_RUN 모드:
    settings.dry_run=True 이면 실제 발송 안 하고 로그만 출력.
    운영 전 검증 / 테스트 목적.
"""
from __future__ import annotations

from typing import Any

from loguru import logger

from src.config import Settings
from src.notify.email import build_afterhours_subject, send_email
from src.notify.telegram import send_error_alert, send_message


class Dispatcher:
    """설정 기반 발송기. 인스턴스 1개를 앱 전체에서 공유."""

    def __init__(self, settings: Settings) -> None:
        self._s = settings

    # ── 텔레그램 ────────────────────────────────────────────────────────────

    def telegram(
        self,
        text: str,
        parse_mode: str | None = "Markdown",
    ) -> list[dict[str, Any]]:
        """텔레그램 메시지 발송.

        DRY_RUN이면 로그만 출력.
        """
        if self._s.dry_run:
            logger.info(f"[DRY_RUN] 텔레그램 발송 스킵 ({len(text)}자):\n{text[:200]}...")
            return [{"ok": True, "dry_run": True}]
        return send_message(
            self._s.telegram_bot_token,
            self._s.telegram_chat_id,
            text,
            parse_mode=parse_mode,
        )

    def telegram_error(self, error_msg: str, context: str = "") -> dict[str, Any]:
        """시스템 장애 에러 알림 텔레그램 발송.

        DRY_RUN과 무관하게 항상 로그는 남긴다.
        DRY_RUN이면 실제 발송은 스킵.
        """
        logger.error(f"[에러 알림] {context}: {error_msg}")
        if self._s.dry_run:
            return {"ok": True, "dry_run": True}
        return send_error_alert(
            self._s.telegram_bot_token,
            self._s.telegram_chat_id,
            error_msg,
            context=context,
        )

    # ── 이메일 ──────────────────────────────────────────────────────────────

    def email(self, subject: str, body: str) -> dict[str, Any]:
        """이메일 발송.

        DRY_RUN이면 로그만 출력.
        """
        if self._s.dry_run:
            logger.info(f"[DRY_RUN] 이메일 발송 스킵 → {self._s.gmail_to}  제목: {subject}")
            return {"ok": True, "dry_run": True}
        return send_email(
            self._s.gmail_user,
            self._s.gmail_app_password,
            self._s.gmail_to,
            subject,
            body,
        )

    def email_afterhours(self, report_text: str, report_date: str) -> dict[str, Any]:
        """사후 레포트 이메일 발송 편의 메서드."""
        subject = build_afterhours_subject(report_date)
        return self.email(subject, report_text)

    # ── 레포트별 발송 편의 메서드 ───────────────────────────────────────────

    def send_morning(self, report: str) -> None:
        """모닝 레포트 발송 (텔레그램)."""
        results = self.telegram(report)
        _log_results("모닝", results)

    def send_periodic(self, report: str, label: str = "추적") -> None:
        """정기 추적 레포트 발송 (텔레그램)."""
        results = self.telegram(report)
        _log_results(label, results)

    def send_decision(self, report_parts: list[str]) -> None:
        """결정 레포트 발송 (텔레그램, 여러 메시지 가능)."""
        for i, part in enumerate(report_parts, 1):
            results = self.telegram(part)
            _log_results(f"결정({i}/{len(report_parts)})", results)

    def send_limit_up_event(self, alert: str) -> None:
        """상한가 이벤트 알림 발송 (텔레그램)."""
        results = self.telegram(alert)
        _log_results("상한가", results)

    def send_early_morning(self, alert: str | None) -> None:
        """장초반 변화감지 알림 발송. None이면 변화 없음 → 스킵."""
        if alert is None:
            return
        results = self.telegram(alert)
        _log_results("장초반", results)


def _log_results(label: str, results: list[dict[str, Any]]) -> None:
    ok = all(r.get("ok") for r in results)
    if ok:
        logger.info(f"[{label}] 발송 성공 ({len(results)}건)")
    else:
        failed = [r for r in results if not r.get("ok")]
        logger.warning(f"[{label}] 발송 실패 {len(failed)}/{len(results)}건: {failed}")
