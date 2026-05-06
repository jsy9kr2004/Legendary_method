"""운영 상태 점검 모듈.

디스크 사용량, 데이터 신선도, 로그 디렉토리 상태를 체크하고
이상 시 텔레그램 에러 알림을 발송한다.

실행:
    python -m src.ops.health              # 점검 후 stdout 출력
    python -m src.ops.health --send       # 이상 시 텔레그램 발송
    python -m src.ops.health --json       # JSON 출력 (cron/monitoring 통합용)
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger

from src.config import KST, load_settings
from src.data.storage import (
    daily_ohlcv_path,
    naver_themes_path,
    themes_are_fresh,
    themes_last_crawled,
)
from src.logging_setup import setup_logging

# 임계값
DISK_WARN_PCT = 80.0       # 디스크 사용률 경고 (%)
DISK_CRIT_PCT = 90.0       # 디스크 사용률 위험 (%)
OHLCV_MAX_AGE_DAYS = 3     # 일봉 파일 최대 허용 경과 일수 (주말 포함)
LOG_MAX_SIZE_MB = 500      # 로그 디렉토리 최대 허용 크기 (MB)
DATA_MAX_SIZE_GB = 10.0    # 데이터 디렉토리 최대 허용 크기 (GB)


@dataclass
class CheckResult:
    name: str
    ok: bool
    level: str           # "ok" | "warn" | "crit"
    message: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class HealthReport:
    checked_at: str
    overall_ok: bool
    checks: list[CheckResult] = field(default_factory=list)

    def add(self, result: CheckResult) -> None:
        self.checks.append(result)
        if not result.ok:
            self.overall_ok = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["checks"] = [asdict(c) for c in self.checks]
        return d

    def to_text(self) -> str:
        lines: list[str] = []
        icon = "✅" if self.overall_ok else "⚠️"
        lines.append(f"{icon} 헬스체크 — {self.checked_at}")
        for c in self.checks:
            mark = "✅" if c.ok else ("⚠️" if c.level == "warn" else "🚨")
            lines.append(f"  {mark} {c.name}: {c.message}")
        return "\n".join(lines)

    def to_alert(self) -> str:
        """텔레그램 전송용 — 이상 항목만."""
        failed = [c for c in self.checks if not c.ok]
        if not failed:
            return ""
        lines = [f"🚨 헬스체크 이상 — {self.checked_at}"]
        for c in failed:
            mark = "⚠️" if c.level == "warn" else "🚨"
            lines.append(f"  {mark} {c.name}: {c.message}")
        return "\n".join(lines)


# ── 개별 체크 함수 ────────────────────────────────────────────────────────────

def check_disk(path: Path) -> CheckResult:
    """마운트 포인트 디스크 사용률 체크."""
    usage = shutil.disk_usage(path)
    used_pct = usage.used / usage.total * 100
    free_gb = usage.free / 1024 ** 3
    detail = {
        "path": str(path),
        "used_pct": round(used_pct, 1),
        "free_gb": round(free_gb, 2),
        "total_gb": round(usage.total / 1024 ** 3, 2),
    }
    if used_pct >= DISK_CRIT_PCT:
        return CheckResult("디스크", False, "crit",
                           f"사용률 {used_pct:.1f}% (여유 {free_gb:.1f}GB)", detail)
    if used_pct >= DISK_WARN_PCT:
        return CheckResult("디스크", False, "warn",
                           f"사용률 {used_pct:.1f}% (여유 {free_gb:.1f}GB)", detail)
    return CheckResult("디스크", True, "ok",
                       f"사용률 {used_pct:.1f}% (여유 {free_gb:.1f}GB)", detail)


def check_ohlcv_freshness(data_dir: Path) -> CheckResult:
    """일봉 파일 존재 + 최근 수정일 체크."""
    p = daily_ohlcv_path(data_dir)
    if not p.exists():
        return CheckResult("일봉 파일", False, "crit", "파일 없음", {"path": str(p)})

    mtime = datetime.fromtimestamp(p.stat().st_mtime).date()
    today = date.today()
    age_days = (today - mtime).days
    size_mb = p.stat().st_size / 1024 ** 2
    detail = {"path": str(p), "mtime": str(mtime), "age_days": age_days,
              "size_mb": round(size_mb, 1)}

    if age_days > OHLCV_MAX_AGE_DAYS:
        return CheckResult("일봉 신선도", False, "warn",
                           f"마지막 갱신 {age_days}일 전 ({mtime})", detail)
    return CheckResult("일봉 신선도", True, "ok",
                       f"최신 ({mtime}, {size_mb:.1f}MB)", detail)


def check_theme_freshness(data_dir: Path) -> CheckResult:
    """네이버 테마 파일 신선도 체크 (7일 기준)."""
    p = naver_themes_path(data_dir)
    if not p.exists():
        return CheckResult("테마 파일", False, "warn", "파일 없음 — update_themes 실행 필요",
                           {"path": str(p)})

    crawled = themes_last_crawled(data_dir)
    fresh = themes_are_fresh(data_dir, max_age_days=7)
    age_str = str(crawled) if crawled else "알 수 없음"
    size_mb = p.stat().st_size / 1024 ** 2
    detail = {"path": str(p), "last_crawled": age_str, "size_mb": round(size_mb, 2)}

    if not fresh:
        return CheckResult("테마 신선도", False, "warn",
                           f"마지막 크롤링 {age_str} — 7일 이상 경과", detail)
    return CheckResult("테마 신선도", True, "ok",
                       f"최신 ({age_str}, {size_mb:.2f}MB)", detail)


def check_log_dir(log_dir: Path) -> CheckResult:
    """로그 디렉토리 존재 + 크기 체크."""
    if not log_dir.exists():
        return CheckResult("로그 디렉토리", False, "warn", f"디렉토리 없음: {log_dir}",
                           {"path": str(log_dir)})

    total_mb = sum(f.stat().st_size for f in log_dir.rglob("*") if f.is_file()) / 1024 ** 2
    detail = {"path": str(log_dir), "total_mb": round(total_mb, 1)}

    if total_mb > LOG_MAX_SIZE_MB:
        return CheckResult("로그 크기", False, "warn",
                           f"{total_mb:.0f}MB — logrotate 확인 필요", detail)
    return CheckResult("로그 크기", True, "ok", f"{total_mb:.1f}MB", detail)


def check_data_dir_size(data_dir: Path) -> CheckResult:
    """데이터 디렉토리 전체 크기 체크."""
    if not data_dir.exists():
        return CheckResult("데이터 디렉토리", False, "crit",
                           f"디렉토리 없음: {data_dir}", {"path": str(data_dir)})

    total_gb = sum(f.stat().st_size for f in data_dir.rglob("*") if f.is_file()) / 1024 ** 3
    detail = {"path": str(data_dir), "total_gb": round(total_gb, 3)}

    if total_gb > DATA_MAX_SIZE_GB:
        return CheckResult("데이터 크기", False, "warn",
                           f"{total_gb:.2f}GB — 디스크 정리 필요", detail)
    return CheckResult("데이터 크기", True, "ok", f"{total_gb:.3f}GB", detail)


# ── 통합 실행 ─────────────────────────────────────────────────────────────────

def run_health_check(data_dir: Path, log_dir: Path) -> HealthReport:
    """전체 헬스체크 실행."""
    now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")
    report = HealthReport(checked_at=now_kst, overall_ok=True)

    report.add(check_disk(data_dir))
    report.add(check_ohlcv_freshness(data_dir))
    report.add(check_theme_freshness(data_dir))
    report.add(check_log_dir(log_dir))
    report.add(check_data_dir_size(data_dir))

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="운영 헬스체크")
    parser.add_argument("--send", action="store_true", help="이상 시 텔레그램 발송")
    parser.add_argument("--json", action="store_true", help="JSON 출력")
    args = parser.parse_args(argv)

    settings = load_settings()
    setup_logging(settings)

    report = run_health_check(settings.data_dir, settings.log_dir)

    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(report.to_text())

    if args.send and not report.overall_ok:
        from src.notify.dispatcher import Dispatcher
        d = Dispatcher(settings)
        alert = report.to_alert()
        if alert:
            d.telegram_error(alert, context="헬스체크")

    return 0 if report.overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
