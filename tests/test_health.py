"""src.ops.health 모듈 테스트."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from src.ops.health import (
    CheckResult,
    HealthReport,
    DISK_CRIT_PCT,
    DISK_WARN_PCT,
    check_data_dir_size,
    check_disk,
    check_log_dir,
    check_ohlcv_freshness,
    check_theme_freshness,
    run_health_check,
)


# ── CheckResult / HealthReport ────────────────────────────────────────────────

def test_health_report_all_ok():
    r = HealthReport(checked_at="2026-05-06 09:00:00 KST", overall_ok=True)
    r.add(CheckResult("디스크", True, "ok", "50% 사용"))
    assert r.overall_ok is True


def test_health_report_one_fail():
    r = HealthReport(checked_at="2026-05-06 09:00:00 KST", overall_ok=True)
    r.add(CheckResult("디스크", False, "warn", "85% 사용"))
    assert r.overall_ok is False


def test_health_report_to_text_contains_name():
    r = HealthReport(checked_at="2026-05-06", overall_ok=True)
    r.add(CheckResult("디스크", True, "ok", "정상"))
    text = r.to_text()
    assert "디스크" in text


def test_health_report_to_alert_empty_when_ok():
    r = HealthReport(checked_at="2026-05-06", overall_ok=True)
    r.add(CheckResult("디스크", True, "ok", "정상"))
    assert r.to_alert() == ""


def test_health_report_to_alert_contains_failed():
    r = HealthReport(checked_at="2026-05-06", overall_ok=True)
    r.add(CheckResult("디스크", False, "crit", "사용률 95%"))
    alert = r.to_alert()
    assert "디스크" in alert
    assert "95%" in alert


def test_health_report_to_dict():
    r = HealthReport(checked_at="2026-05-06", overall_ok=True)
    r.add(CheckResult("테스트", True, "ok", "OK", {"x": 1}))
    d = r.to_dict()
    assert "checks" in d
    assert d["checks"][0]["name"] == "테스트"


# ── check_disk ────────────────────────────────────────────────────────────────

def _mock_disk_usage(used_pct: float, total_gb: float = 100):
    total = int(total_gb * 1024 ** 3)
    used = int(total * used_pct / 100)
    free = total - used

    class _Usage:
        pass

    u = _Usage()
    u.total = total
    u.used = used
    u.free = free
    return u


def test_check_disk_ok(tmp_path):
    with patch("src.ops.health.shutil.disk_usage", return_value=_mock_disk_usage(50)):
        result = check_disk(tmp_path)
    assert result.ok is True
    assert result.level == "ok"


def test_check_disk_warn(tmp_path):
    with patch("src.ops.health.shutil.disk_usage", return_value=_mock_disk_usage(85)):
        result = check_disk(tmp_path)
    assert result.ok is False
    assert result.level == "warn"


def test_check_disk_crit(tmp_path):
    with patch("src.ops.health.shutil.disk_usage", return_value=_mock_disk_usage(95)):
        result = check_disk(tmp_path)
    assert result.ok is False
    assert result.level == "crit"


# ── check_ohlcv_freshness ─────────────────────────────────────────────────────

def test_check_ohlcv_no_file(tmp_path):
    result = check_ohlcv_freshness(tmp_path)
    assert result.ok is False
    assert result.level == "crit"


def test_check_ohlcv_fresh(tmp_path):
    from src.data.storage import daily_ohlcv_path
    p = daily_ohlcv_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("dummy")
    result = check_ohlcv_freshness(tmp_path)
    assert result.ok is True


def test_check_ohlcv_stale(tmp_path):
    from src.data.storage import daily_ohlcv_path
    import time, os
    p = daily_ohlcv_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("dummy")
    # mtime을 10일 전으로 변경
    old_time = time.time() - 10 * 86400
    os.utime(p, (old_time, old_time))
    result = check_ohlcv_freshness(tmp_path)
    assert result.ok is False
    assert result.level == "warn"


# ── check_theme_freshness ─────────────────────────────────────────────────────

def test_check_theme_no_file(tmp_path):
    result = check_theme_freshness(tmp_path)
    assert result.ok is False
    assert result.level == "warn"


def test_check_theme_fresh(tmp_path):
    import pandas as pd
    from src.data.storage import write_naver_themes
    df = pd.DataFrame([{"code": "075180", "theme": "전기/전선",
                         "crawled_at": date.today()}])
    write_naver_themes(df, tmp_path)
    result = check_theme_freshness(tmp_path)
    assert result.ok is True


def test_check_theme_stale(tmp_path):
    import pandas as pd
    from src.data.storage import write_naver_themes
    old_date = date.today() - timedelta(days=10)
    df = pd.DataFrame([{"code": "075180", "theme": "전기/전선",
                         "crawled_at": old_date}])
    write_naver_themes(df, tmp_path)
    result = check_theme_freshness(tmp_path)
    assert result.ok is False


# ── check_log_dir ─────────────────────────────────────────────────────────────

def test_check_log_dir_missing(tmp_path):
    result = check_log_dir(tmp_path / "nonexistent")
    assert result.ok is False


def test_check_log_dir_empty(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    result = check_log_dir(log_dir)
    assert result.ok is True


def test_check_log_dir_large(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    # LOG_MAX_SIZE_MB=500이므로 실제로 큰 파일 만들기 어려움 → 임계값을 패치
    with patch("src.ops.health.LOG_MAX_SIZE_MB", 0):
        log_dir.joinpath("app.log").write_text("log content")
        result = check_log_dir(log_dir)
    assert result.ok is False
    assert result.level == "warn"


# ── check_data_dir_size ────────────────────────────────────────────────────────

def test_check_data_dir_missing(tmp_path):
    result = check_data_dir_size(tmp_path / "nodata")
    assert result.ok is False
    assert result.level == "crit"


def test_check_data_dir_ok(tmp_path):
    (tmp_path / "file.parquet").write_bytes(b"x" * 1024)
    result = check_data_dir_size(tmp_path)
    assert result.ok is True


# ── run_health_check (통합) ────────────────────────────────────────────────────

def test_run_health_check_returns_report(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    with patch("src.ops.health.shutil.disk_usage", return_value=_mock_disk_usage(50)):
        report = run_health_check(tmp_path, log_dir)
    assert isinstance(report, HealthReport)
    assert len(report.checks) >= 4


def test_run_health_check_overall_ok_with_all_files(tmp_path):
    import pandas as pd
    from src.data.index_storage import write_index_daily
    from src.data.storage import daily_ohlcv_path, write_naver_themes

    # 일봉 파일 생성
    p = daily_ohlcv_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("dummy")

    # 테마 파일 생성
    df = pd.DataFrame([{"code": "075180", "theme": "전기/전선",
                         "crawled_at": date.today()}])
    write_naver_themes(df, tmp_path)

    # 지수 일봉 (260일치, 최근 적재)
    idx_df = pd.DataFrame([
        {"date": date.today() - timedelta(days=i), "close": 2600.0 + i}
        for i in range(260)
    ])
    write_index_daily(idx_df, tmp_path, "0001")

    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    with patch("src.ops.health.shutil.disk_usage", return_value=_mock_disk_usage(50)):
        report = run_health_check(tmp_path, log_dir)

    assert report.overall_ok is True


# ── check_index_daily ────────────────────────────────────────────────────────

from src.ops.health import check_index_daily  # noqa: E402


def test_check_index_no_file(tmp_path):
    r = check_index_daily(tmp_path)
    assert r.ok is False
    assert "init-index" in r.message


def test_check_index_insufficient_history(tmp_path):
    """적재량 부족 → warn."""
    import pandas as pd
    from src.data.index_storage import write_index_daily
    df = pd.DataFrame([
        {"date": date.today() - timedelta(days=i), "close": 2600.0 + i}
        for i in range(50)
    ])
    write_index_daily(df, tmp_path, "0001")
    r = check_index_daily(tmp_path)
    assert r.ok is False
    assert "ma200" in r.message


def test_check_index_stale_freshness(tmp_path):
    """충분 적재됐지만 최근 적재일이 오래됨 → warn."""
    import pandas as pd
    from src.data.index_storage import write_index_daily
    df = pd.DataFrame([
        {"date": date.today() - timedelta(days=i + 10), "close": 2600.0 + i}
        for i in range(260)
    ])
    write_index_daily(df, tmp_path, "0001")
    r = check_index_daily(tmp_path)
    assert r.ok is False
    assert "마지막 적재" in r.message


def test_check_index_ok(tmp_path):
    """충분 적재 + 최근 적재 → ok."""
    import pandas as pd
    from src.data.index_storage import write_index_daily
    df = pd.DataFrame([
        {"date": date.today() - timedelta(days=i), "close": 2600.0 + i}
        for i in range(260)
    ])
    write_index_daily(df, tmp_path, "0001")
    r = check_index_daily(tmp_path)
    assert r.ok is True
