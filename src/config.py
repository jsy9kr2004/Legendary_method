"""환경설정 로더.

.env에서 값을 읽어 `Settings` dataclass로 노출한다.
경로는 모두 `pathlib.Path`, 시각은 모두 Asia/Seoul (KST) 기준.

KIS 멀티 계정 지원: KIS_APP_KEY / KIS_APP_KEY_2 / KIS_APP_KEY_3 ... 자동 스캔.
조회 분산 + rate limit 합산 효과. 본인+가족 키를 풀로 묶어 사용.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import pytz
from dotenv import load_dotenv

KST = pytz.timezone("Asia/Seoul")

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")


def _path_env(name: str, default: str) -> Path:
    raw = os.getenv(name, default)
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = (PROJECT_ROOT / p).resolve()
    return p


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class KisCredential:
    """KIS 계정 1개의 인증 정보. app_key+secret+계좌번호 묶음."""

    app_key: str
    app_secret: str
    account_no: str
    label: str  # 식별/로그용. e.g. "primary", "secondary", "wife"

    @property
    def cache_id(self) -> str:
        """토큰 캐시 파일명/limiter dict 키용 안정 식별자. app_key 해시 8자."""
        return hashlib.sha256(self.app_key.encode()).hexdigest()[:8]


@dataclass(frozen=True)
class Settings:
    # 경로
    data_dir: Path
    log_dir: Path

    # 운영
    log_level: str
    dry_run: bool

    # KIS (하위 호환용 — 첫 번째 credential 값이 그대로 들어감)
    kis_app_key: str
    kis_app_secret: str
    kis_account_no: str
    kis_api_mode: str  # "real" | "mock"

    # Telegram
    telegram_bot_token: str
    telegram_chat_id: str

    # Gmail
    gmail_user: str
    gmail_app_password: str
    gmail_to: str

    # SMTP 서버 (기본: Gmail. 다른 메일 서비스도 사용 가능)
    smtp_host: str = "smtp." + "gmail" + ".com"
    smtp_port: int = 587

    # 종배 레포트 전용 채팅/그룹 (2026-05-25). 비면 telegram_chat_id 로 fallback.
    # 종배 동료를 그룹에 초대 → 종배 레포트(모닝/정기/결정/상한가/사후/청산)만 그룹으로.
    # 단타 M6 카드 + 봇 명령 + 에러알림은 telegram_chat_id(개인 DM) 유지 → 단타 미노출.
    telegram_eod_chat_id: str = ""

    # 레포트 방 / 알림 방 분리 (2026-06-15). 비면 telegram_eod_chat_id → telegram_chat_id 로
    # fallback. 설정 시 **정기 스케줄 레포트만** (모닝 09:30 / 정기추적 11·13·14 / 결정
    # 14:50 / 사후 16:00) 이 방으로 간다. 즉시 이벤트성 알림 — 상한가 진입 / 단저단고
    # STRONG 푸시(worker 직접 발송) / 종배 청산지원·막판점검 / 에러알림 / 봇 명령 응답 —
    # 은 telegram_chat_id(알림 방) 유지. 사용자가 "읽을거리 레포트" 와 "즉시 행동 알림"
    # 을 다른 방에서 보고 싶을 때.
    telegram_report_chat_id: str = ""

    # 레포트별 텔레그램 발송 토글 (2026-06-16). 각 레포트의 텔레그램 send 만 on/off.
    # OFF 여도 레포트 생성/파일 저장/상태 갱신(주도테마 추적 등)/forward 로깅은 그대로
    # 동작 — 텔레그램 메시지만 skip 한다. default = 결정 레포트(14:50)만 ON, 나머지 OFF.
    # env: REPORT_SEND_MORNING / _PERIODIC / _DECISION / _AFTERHOURS / _LIMIT_UP.
    report_send_morning: bool = False
    report_send_periodic: bool = False
    report_send_decision: bool = True
    report_send_afterhours: bool = False
    report_send_limit_up: bool = False

    # M6 모니터링 카드 텔레그램 발송 토글. False 면 dashboard_tick 의 카드 send/
    # edit/delete 만 skip — PWA 페이로드 / KIS fetch / 명령 응답 / 09:30 모닝 /
    # 11~14:00 periodic / 14:50 결정 / 16:00 사후 / 상한가 이벤트 / 09:01 시초
    # 청산 권고는 모두 정상 동작 (Dispatcher 경유 발송은 본 토글과 무관 — 자세히는
    # src/notify/dispatcher.py 참조). 사용자가 PWA 만 보면서 tick 시간 단축이
    # 목적인 경우 (텔레그램 동기 HTTP POST 가 종목당 200-500ms 직렬이라 tick
    # 시간 큰 비중).
    monitoring_telegram_cards_enabled: bool = True

    # KIS 멀티 계정. load_settings() 에서 채워짐. 직접 Settings() 생성한 경우
    # 비어 있으면 kis_app_key 단일로부터 합성된다 (KISClient/auth 에서 처리).
    kis_credentials: tuple[KisCredential, ...] = field(default_factory=tuple)

    def credentials(self) -> tuple[KisCredential, ...]:
        """KIS 호출에 사용할 credential 목록. 빈 경우 단일 키로부터 합성."""
        if self.kis_credentials:
            return self.kis_credentials
        if self.kis_app_key and self.kis_app_secret:
            return (
                KisCredential(
                    app_key=self.kis_app_key,
                    app_secret=self.kis_app_secret,
                    account_no=self.kis_account_no,
                    label="primary",
                ),
            )
        return ()


def _scan_kis_credentials() -> tuple[KisCredential, ...]:
    """환경변수에서 KIS_APP_KEY / KIS_APP_KEY_2 / KIS_APP_KEY_3 ... 스캔.

    첫 번째는 인덱스 없음 (기존 .env 호환). 두 번째부터 _2, _3.
    각 키마다 짝이 되는 KIS_APP_SECRET[_N] / KIS_ACCOUNT_NO[_N] 도 같이 읽음.
    """
    creds: list[KisCredential] = []

    # 첫 번째 키 (인덱스 없음, 기존 호환)
    primary_key = os.getenv("KIS_APP_KEY", "")
    primary_secret = os.getenv("KIS_APP_SECRET", "")
    if primary_key and primary_secret:
        creds.append(
            KisCredential(
                app_key=primary_key,
                app_secret=primary_secret,
                account_no=os.getenv("KIS_ACCOUNT_NO", ""),
                label="primary",
            )
        )

    # 두 번째부터 _2, _3, ... — 연속 인덱스만, 비면 종료
    idx = 2
    while True:
        key = os.getenv(f"KIS_APP_KEY_{idx}", "")
        secret = os.getenv(f"KIS_APP_SECRET_{idx}", "")
        if not key or not secret:
            break
        creds.append(
            KisCredential(
                app_key=key,
                app_secret=secret,
                account_no=os.getenv(f"KIS_ACCOUNT_NO_{idx}", ""),
                label=os.getenv(f"KIS_LABEL_{idx}", f"account_{idx}"),
            )
        )
        idx += 1

    return tuple(creds)


def load_settings() -> Settings:
    creds = _scan_kis_credentials()
    primary = creds[0] if creds else None
    return Settings(
        data_dir=_path_env("DATA_DIR", "./data"),
        log_dir=_path_env("LOG_DIR", "./logs"),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        dry_run=_bool_env("DRY_RUN", False),
        kis_app_key=primary.app_key if primary else os.getenv("KIS_APP_KEY", ""),
        kis_app_secret=primary.app_secret if primary else os.getenv("KIS_APP_SECRET", ""),
        kis_account_no=primary.account_no if primary else os.getenv("KIS_ACCOUNT_NO", ""),
        kis_api_mode=os.getenv("KIS_API_MODE", "mock"),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        telegram_eod_chat_id=os.getenv("TELEGRAM_EOD_CHAT_ID", ""),
        telegram_report_chat_id=os.getenv("TELEGRAM_REPORT_CHAT_ID", ""),
        report_send_morning=_bool_env("REPORT_SEND_MORNING", default=False),
        report_send_periodic=_bool_env("REPORT_SEND_PERIODIC", default=False),
        report_send_decision=_bool_env("REPORT_SEND_DECISION", default=True),
        report_send_afterhours=_bool_env("REPORT_SEND_AFTERHOURS", default=False),
        report_send_limit_up=_bool_env("REPORT_SEND_LIMIT_UP", default=False),
        monitoring_telegram_cards_enabled=_bool_env(
            "MONITORING_TELEGRAM_CARDS_ENABLED", default=True,
        ),
        gmail_user=os.getenv("GMAIL_USER", ""),
        gmail_app_password=os.getenv("GMAIL_APP_PASSWORD", ""),
        gmail_to=os.getenv("GMAIL_TO", ""),
        smtp_host=os.getenv("SMTP_HOST", "smtp." + "gmail" + ".com"),
        smtp_port=int(os.getenv("SMTP_PORT", "587")),
        kis_credentials=creds,
    )


def now_kst() -> datetime:
    return datetime.now(KST)


def today_kst() -> date:
    return now_kst().date()
