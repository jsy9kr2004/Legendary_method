"""src.data.integrity_check 테스트."""
from __future__ import annotations

from datetime import date

import pandas as pd

from src.data import integrity_check as ic


def _row(code: str, d: date, close: int) -> dict:
    return {
        "code": code,
        "date": d,
        "open": close - 100,
        "high": close + 100,
        "low": close - 200,
        "close": close,
        "volume": 1_000_000,
        "trading_value": close * 1_000_000,
        "change_rate": pd.NA,
    }


def _make(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["close"] = df["close"].astype("Int64")
    return df


# ─────────────────────────────────────────
# check_recent_coverage
# ─────────────────────────────────────────


def test_coverage_passes_when_within_threshold():
    df = _make(
        [_row(f"00000{i}", date(2025, 5, 1), 1000) for i in range(100)]
        + [_row(f"00000{i}", date(2025, 5, 2), 1000) for i in range(98)]
    )
    ok, msg = ic.check_recent_coverage(df, 0.95)
    assert ok is True
    assert "98" in msg


def test_coverage_fails_below_threshold():
    df = _make(
        [_row(f"00000{i}", date(2025, 5, 1), 1000) for i in range(100)]
        + [_row(f"00000{i}", date(2025, 5, 2), 1000) for i in range(80)]
    )
    ok, _ = ic.check_recent_coverage(df, 0.95)
    assert ok is False


def test_coverage_single_date_returns_ok():
    df = _make([_row("005930", date(2025, 5, 1), 70000)])
    ok, msg = ic.check_recent_coverage(df, 0.95)
    assert ok is True
    assert "비교할" in msg


def test_coverage_empty_df():
    ok, _ = ic.check_recent_coverage(pd.DataFrame(), 0.95)
    assert ok is False


# ─────────────────────────────────────────
# find_price_outliers
# ─────────────────────────────────────────


def test_no_outliers_when_within_threshold():
    df = _make(
        [
            _row("005930", date(2025, 5, 1), 70000),
            _row("005930", date(2025, 5, 2), 71000),  # +1.4%
            _row("005930", date(2025, 5, 5), 72000),  # +1.4%
        ]
    )
    out = ic.find_price_outliers(df, 0.5)
    assert out.empty


def test_detects_50pct_jump():
    df = _make(
        [
            _row("005930", date(2025, 5, 1), 10000),
            _row("005930", date(2025, 5, 2), 16000),  # +60%
        ]
    )
    out = ic.find_price_outliers(df, 0.5)
    assert len(out) == 1
    assert out.iloc[0]["code"] == "005930"
    assert out.iloc[0]["date"] == date(2025, 5, 2)


def test_outliers_per_code_independent():
    df = _make(
        [
            _row("005930", date(2025, 5, 1), 10000),
            _row("005930", date(2025, 5, 2), 10100),  # +1%
            _row("000660", date(2025, 5, 1), 10000),
            _row("000660", date(2025, 5, 2), 16000),  # +60%
        ]
    )
    out = ic.find_price_outliers(df, 0.5)
    assert len(out) == 1
    assert out.iloc[0]["code"] == "000660"


# ─────────────────────────────────────────
# find_weekend_rows
# ─────────────────────────────────────────


def test_no_weekend_rows_normally():
    df = _make([_row("005930", date(2025, 5, 2), 70000)])  # 금
    assert ic.find_weekend_rows(df).empty


def test_detects_saturday_row():
    df = _make([_row("005930", date(2025, 5, 3), 70000)])  # 토
    out = ic.find_weekend_rows(df)
    assert len(out) == 1


def test_detects_sunday_row():
    df = _make([_row("005930", date(2025, 5, 4), 70000)])  # 일
    out = ic.find_weekend_rows(df)
    assert len(out) == 1


def test_weekend_check_empty_df():
    assert ic.find_weekend_rows(pd.DataFrame()).empty


# ── build_alert_text ─────────────────────────────────────────────────────────


def test_build_alert_text_failures_and_warnings():
    text = ic.build_alert_text(
        failures=["커버리지 미달 — 95% 미만"],
        warnings=["가격 이상치 3건"],
    )
    assert "FAIL" in text
    assert "1건" in text
    assert "WARN" in text
    assert "커버리지" in text
    assert "이상치" in text


def test_build_alert_text_empty_returns_empty():
    assert ic.build_alert_text([], []) == ""


def test_build_alert_text_only_failures():
    text = ic.build_alert_text(failures=["주말 적재 5행"], warnings=[])
    assert "FAIL" in text
    assert "WARN" not in text


def test_build_alert_text_only_warnings():
    text = ic.build_alert_text(failures=[], warnings=["가격 이상치"])
    assert "WARN" in text
    assert "FAIL" not in text


# ── main(--send) 텔레그램 발송 ──────────────────────────────────────────────


def test_main_send_calls_dispatcher_on_failure(tmp_path, monkeypatch):
    """주말 적재 행 → FAIL → --send 옵션 시 dispatcher.telegram_error 호출."""
    from unittest.mock import MagicMock, patch
    import pandas as pd

    # 주말 적재 1행 (FAIL 유발)
    df = pd.DataFrame([{
        "code": "005930", "date": date(2025, 5, 3),  # 토요일
        "open": 70000, "high": 71000, "low": 69500, "close": 70500,
        "volume": 100, "trading_value": 7_050_000,
    }])

    fake_settings = MagicMock()
    fake_settings.data_dir = tmp_path
    fake_settings.log_dir = tmp_path

    fake_dispatcher = MagicMock()
    with patch.object(ic.storage, "read_daily_ohlcv", return_value=df), \
         patch.object(ic, "load_settings", return_value=fake_settings), \
         patch.object(ic, "setup_logging"), \
         patch("src.notify.dispatcher.Dispatcher", return_value=fake_dispatcher):
        rc = ic.main(["--send"])

    assert rc == 1  # FAIL
    fake_dispatcher.telegram_error.assert_called_once()
    args, kwargs = fake_dispatcher.telegram_error.call_args
    body = args[0] if args else kwargs.get("text", "")
    assert "주말 적재" in body


def test_main_send_skips_dispatcher_when_clean(tmp_path, monkeypatch):
    """이슈 없으면 dispatcher 호출 안 함."""
    from unittest.mock import MagicMock, patch
    import pandas as pd

    df = pd.DataFrame([
        {"code": "005930", "date": date(2025, 5, 1), "open": 70000,
         "high": 71000, "low": 69500, "close": 70500,
         "volume": 100, "trading_value": 7_050_000},
        {"code": "005930", "date": date(2025, 5, 2), "open": 70500,
         "high": 71500, "low": 70000, "close": 71000,
         "volume": 100, "trading_value": 7_100_000},
    ])

    fake_settings = MagicMock()
    fake_settings.data_dir = tmp_path
    fake_settings.log_dir = tmp_path

    fake_dispatcher = MagicMock()
    with patch.object(ic.storage, "read_daily_ohlcv", return_value=df), \
         patch.object(ic, "load_settings", return_value=fake_settings), \
         patch.object(ic, "setup_logging"), \
         patch("src.notify.dispatcher.Dispatcher", return_value=fake_dispatcher):
        rc = ic.main(["--send"])

    assert rc == 0
    fake_dispatcher.telegram_error.assert_not_called()


def test_main_no_send_does_not_call_dispatcher_on_failure(tmp_path):
    """--send 미지정 시 dispatcher 호출 안 함."""
    from unittest.mock import MagicMock, patch
    import pandas as pd

    df = pd.DataFrame([{
        "code": "005930", "date": date(2025, 5, 3),
        "open": 70000, "high": 71000, "low": 69500, "close": 70500,
        "volume": 100, "trading_value": 7_050_000,
    }])

    fake_settings = MagicMock()
    fake_settings.data_dir = tmp_path

    fake_dispatcher = MagicMock()
    with patch.object(ic.storage, "read_daily_ohlcv", return_value=df), \
         patch.object(ic, "load_settings", return_value=fake_settings), \
         patch.object(ic, "setup_logging"), \
         patch("src.notify.dispatcher.Dispatcher", return_value=fake_dispatcher):
        rc = ic.main([])

    assert rc == 1
    fake_dispatcher.telegram_error.assert_not_called()
