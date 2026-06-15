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
    # 본 Dispatcher 의 telegram() 및 모든 send_* 메서드는 MONITORING_TELEGRAM_
    # CARDS_ENABLED 토글과 **무관** — 토글은 dashboard_tick 의 모니터링 카드
    # send/edit/delete 만 제어한다. 결정/사후/모닝/periodic/상한가/시초청산
    # 레포트는 dry_run=True 일 때만 skip, 그 외엔 항상 발송. 자세히는
    # src/config.py 의 monitoring_telegram_cards_enabled 주석 참조.

    def telegram(
        self,
        text: str,
        parse_mode: str | None = "Markdown",
        chat_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """텔레그램 메시지 발송.

        chat_id 미지정 시 알림 방(telegram_chat_id)으로. 정기 스케줄 레포트는
        send_morning/periodic/decision/afterhours 가 _report_target() 을 넘겨
        레포트 방으로 보낸다.

        DRY_RUN이면 로그만 출력.
        """
        if self._s.dry_run:
            logger.info(f"[DRY_RUN] 텔레그램 발송 스킵 ({len(text)}자):\n{text[:200]}...")
            return [{"ok": True, "dry_run": True}]
        # 기본 대상 = 알림 방(telegram_chat_id). 즉시 이벤트(상한가/청산지원/막판점검)/
        # 봇 명령/에러알림. 단타 M6 카드 + STRONG 푸시는 worker 가 telegram_chat_id 로 직접 발송.
        target = chat_id or self._s.telegram_chat_id
        return send_message(
            self._s.telegram_bot_token,
            target,
            text,
            parse_mode=parse_mode,
        )

    def _report_target(self) -> str:
        """정기 스케줄 레포트(모닝/정기/결정/사후) 발송 대상.

        레포트 방(telegram_report_chat_id) 우선 → (하위호환) 종배 그룹
        (telegram_eod_chat_id) → 알림 방(telegram_chat_id). 모두 비면 한 방으로.
        """
        return (
            self._s.telegram_report_chat_id
            or self._s.telegram_eod_chat_id
            or self._s.telegram_chat_id
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
            host=self._s.smtp_host,
            port=self._s.smtp_port,
        )

    def email_afterhours(self, report_text: str, report_date: str) -> dict[str, Any]:
        """사후 레포트 이메일 발송 편의 메서드."""
        subject = build_afterhours_subject(report_date)
        return self.email(subject, report_text)

    # ── 레포트별 발송 편의 메서드 ───────────────────────────────────────────

    def send_morning(self, report: str) -> None:
        """모닝 레포트 발송 (텔레그램, 레포트 방)."""
        results = self.telegram(report, chat_id=self._report_target())
        _log_results("모닝", results)

    def send_periodic(self, report: str, label: str = "추적") -> None:
        """정기 추적 레포트 발송 (텔레그램, 레포트 방)."""
        results = self.telegram(report, chat_id=self._report_target())
        _log_results(label, results)

    def send_decision(self, report_parts: list[str]) -> None:
        """결정 레포트 발송 (텔레그램, 레포트 방, 여러 메시지 가능)."""
        target = self._report_target()
        for i, part in enumerate(report_parts, 1):
            results = self.telegram(part, chat_id=target)
            _log_results(f"결정({i}/{len(report_parts)})", results)

    def send_limit_up_event(self, alert: str) -> None:
        """상한가 이벤트 알림 발송 (텔레그램, 알림 방 — 즉시 이벤트)."""
        results = self.telegram(alert)
        _log_results("상한가", results)

    def send_afterhours(self, report: str) -> None:
        """사후 레포트 발송 (텔레그램, 레포트 방, 4096자 초과 시 자동 분할)."""
        results = self.telegram(report, chat_id=self._report_target())
        _log_results("사후", results)

    def send_eod_entry(self, report: str) -> None:
        """종배 막판 진입 점검 발송 (15:00/10/20, 종배 채널). 표시만 — 자동주문 X."""
        results = self.telegram(report)
        _log_results("막판점검", results)

    def send_jongbae_open_exit(self, report: str) -> None:
        """종배 청산 시초가 권고 발송 (round 32, P3-2 wiring).

        09:01~09:05 사이 보유 종목별 evaluate_jongbae_open_exit 결과를
        텔레그램으로. 자동 주문 X — 권고만 (CLAUDE.md 정책).
        """
        results = self.telegram(report)
        _log_results("시초청산", results)

    # send_early_morning 은 폐기됨 (M5.5/M6). M6 dashboard worker 가 직접
    # send_message_single / edit_message 호출로 대체.


def _log_results(label: str, results: list[dict[str, Any]]) -> None:
    ok = all(r.get("ok") for r in results)
    if ok:
        logger.info(f"[{label}] 발송 성공 ({len(results)}건)")
    else:
        failed = [r for r in results if not r.get("ok")]
        logger.warning(f"[{label}] 발송 실패 {len(failed)}/{len(results)}건: {failed}")
