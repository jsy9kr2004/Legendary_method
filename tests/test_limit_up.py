"""src.jongbae.limit_up 테스트."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pandas as pd
import pytest

from src.jongbae.limit_up import (
    detect_new_limit_up,
    filter_limit_up_from_snapshot,
    filter_strong_candidates,
    is_limit_up,
    is_strong_candidate,
    limit_up_price,
)
from src.kis import auth
from src.kis.client import KISClient
from src.kis.rate_limit import RateLimiter


def _fake_token():
    return auth.Token(
        access_token="TOK",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=23),
        api_mode="mock",
    )


def _make_client(tmp_path):
    from src.config import Settings

    s = Settings(
        data_dir=tmp_path,
        log_dir=tmp_path / "logs",
        log_level="INFO",
        dry_run=False,
        kis_app_key="K",
        kis_app_secret="S",
        kis_account_no="50000000-01",
        kis_api_mode="mock",
        telegram_bot_token="",
        telegram_chat_id="",
        gmail_user="",
        gmail_app_password="",
        gmail_to="",
    )
    return KISClient(s, limiter=RateLimiter(calls_per_sec=1000))


# ── limit_up_price / is_limit_up ────────────────────────────────────────────

def test_limit_up_price_10000():
    assert limit_up_price(10_000) == 13_000


def test_limit_up_price_70230():
    """5/4 제룡전기: 전일종가 70,230 → 상한가 floor(91299) = 91,299"""
    assert limit_up_price(70_230) == 91_299


def test_is_limit_up_at_limit():
    assert is_limit_up(91_299, 70_230) is True


def test_is_limit_up_above_limit():
    assert is_limit_up(91_300, 70_230) is True


def test_is_limit_up_below_limit():
    assert is_limit_up(91_298, 70_230) is False


def test_is_limit_up_zero_prev():
    assert is_limit_up(100, 0) is False


# ── is_strong_candidate ──────────────────────────────────────────────────────

def test_is_strong_candidate_exactly_20():
    assert is_strong_candidate(20.0) is True


def test_is_strong_candidate_above_20():
    assert is_strong_candidate(30.0) is True


def test_is_strong_candidate_below_20():
    assert is_strong_candidate(19.99) is False


# ── filter_limit_up_from_snapshot ────────────────────────────────────────────

def _make_snapshot_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "rank": 1, "code": "075180", "name": "제룡전기",
                "price": 91_300, "prev_close": 70_230,
                "daily_return": 30.0, "intraday_high": 91_300,
                "volume": 5_000_000, "trading_value": 400_000_000_000,
                "is_limit_up": True,
            },
            {
                "rank": 2, "code": "005930", "name": "삼성전자",
                "price": 80_000, "prev_close": 79_000,
                "daily_return": 1.27, "intraday_high": 81_000,
                "volume": 20_000_000, "trading_value": 1_600_000_000_000,
                "is_limit_up": False,
            },
        ]
    )


def test_filter_limit_up_from_snapshot():
    df = _make_snapshot_df()
    result = filter_limit_up_from_snapshot(df)
    assert len(result) == 1
    assert result.iloc[0]["code"] == "075180"


def test_filter_limit_up_from_snapshot_empty():
    result = filter_limit_up_from_snapshot(pd.DataFrame())
    assert result.empty


def test_filter_strong_candidates():
    df = _make_snapshot_df()
    result = filter_strong_candidates(df)
    assert len(result) == 1
    assert result.iloc[0]["code"] == "075180"


def test_filter_strong_candidates_empty():
    result = filter_strong_candidates(pd.DataFrame())
    assert result.empty


# ── detect_new_limit_up ──────────────────────────────────────────────────────

_QUOTE_JERYONG = {
    "code": "075180",
    "name": "제룡전기",
    "price": 91_300,
    "prev_close": 70_230,
    "daily_return": 30.0,
    "intraday_high": 91_300,
    "volume": 5_000_000,
    "trading_value": 400_000_000_000,
    "is_limit_up": True,
}

_QUOTE_SAMSUNG = {
    "code": "005930",
    "name": "삼성전자",
    "price": 80_000,
    "prev_close": 79_000,
    "daily_return": 1.27,
    "intraday_high": 81_000,
    "volume": 20_000_000,
    "trading_value": 1_600_000_000_000,
    "is_limit_up": False,
}


def test_detect_new_limit_up_finds_new(tmp_path):
    client = _make_client(tmp_path)
    bulk_df = pd.DataFrame([_QUOTE_JERYONG, _QUOTE_SAMSUNG])

    with patch("src.jongbae.limit_up.fetch_quotes_bulk", return_value=bulk_df):
        new_entries, updated = detect_new_limit_up(client, ["075180", "005930"], set())

    assert len(new_entries) == 1
    assert new_entries[0]["code"] == "075180"
    assert "075180" in updated


def test_detect_new_limit_up_skips_already_known(tmp_path):
    """이미 상한가로 기록된 종목은 재알림 X."""
    client = _make_client(tmp_path)
    bulk_df = pd.DataFrame([_QUOTE_JERYONG])

    with patch("src.jongbae.limit_up.fetch_quotes_bulk", return_value=bulk_df):
        new_entries, updated = detect_new_limit_up(
            client, ["075180"], already_limit_up={"075180"}
        )

    assert new_entries == []
    assert "075180" in updated


def test_detect_new_limit_up_empty_codes(tmp_path):
    client = _make_client(tmp_path)
    new_entries, updated = detect_new_limit_up(client, [], set())
    assert new_entries == []
    assert updated == set()


def test_detect_new_limit_up_no_limit_up_in_result(tmp_path):
    client = _make_client(tmp_path)
    bulk_df = pd.DataFrame([_QUOTE_SAMSUNG])

    with patch("src.jongbae.limit_up.fetch_quotes_bulk", return_value=bulk_df):
        new_entries, updated = detect_new_limit_up(client, ["005930"], set())

    assert new_entries == []
    assert updated == set()
