"""src.data.intraday 테스트. KIS API는 mock."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.data.intraday import (
    SNAPSHOT_COLUMNS,
    _is_limit_up_price,
    fetch_quote,
    fetch_volume_rank,
    limit_up_price,
)
from src.kis import auth
from src.kis.client import KISApiError, KISClient
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


# ── limit_up_price ──────────────────────────────────────────────────────────

def test_limit_up_price_basic():
    """전일종가 10,000 → 상한가 floor(13,000) = 13,000"""
    assert limit_up_price(10_000) == 13_000


def test_limit_up_price_fractional():
    """전일종가 7,700 → floor(7700 * 1.30) = floor(10010) = 10,010"""
    assert limit_up_price(7_700) == 10_010


def test_limit_up_price_zero():
    assert limit_up_price(0) == 0


def test_is_limit_up_price_true():
    assert _is_limit_up_price(13_000, 10_000) is True


def test_is_limit_up_price_below():
    assert _is_limit_up_price(12_999, 10_000) is False


def test_is_limit_up_price_zero_prev():
    assert _is_limit_up_price(100, 0) is False


# ── fetch_volume_rank ────────────────────────────────────────────────────────

_VOLUME_RANK_PAYLOAD = {
    "rt_cd": "0",
    "output": [
        {
            "data_rank": "1",
            "mksc_shrn_iscd": "075180",
            "hts_kor_isnm": "제룡전기",
            "stck_prpr": "91300",
            "stck_prdy_clpr": "70230",
            "prdy_ctrt": "30.00",
            "stck_hgpr": "91300",
            "acml_vol": "5000000",
            "acml_tr_pbmn": "400000000000",
        },
        {
            "data_rank": "2",
            "mksc_shrn_iscd": "005930",
            "hts_kor_isnm": "삼성전자",
            "stck_prpr": "80000",
            "stck_prdy_clpr": "79000",
            "prdy_ctrt": "1.27",
            "stck_hgpr": "81000",
            "acml_vol": "20000000",
            "acml_tr_pbmn": "1600000000000",
        },
    ],
}


def test_fetch_volume_rank_normal(tmp_path):
    client = _make_client(tmp_path)
    with patch.object(auth, "get_token", return_value=_fake_token()):
        with patch.object(client, "get", return_value=_VOLUME_RANK_PAYLOAD):
            df = fetch_volume_rank(client, top_n=30)

    assert list(df.columns) == SNAPSHOT_COLUMNS
    assert len(df) == 2
    assert df.iloc[0]["code"] == "075180"
    assert df.iloc[0]["name"] == "제룡전기"
    assert df.iloc[0]["price"] == 91_300
    assert df.iloc[0]["is_limit_up"] == True   # 91300 >= floor(70230*1.3)=91299
    assert df.iloc[1]["is_limit_up"] == False


def test_fetch_volume_rank_top_n_filter(tmp_path):
    """top_n=1 이면 1위 종목만 반환."""
    client = _make_client(tmp_path)
    with patch.object(auth, "get_token", return_value=_fake_token()):
        with patch.object(client, "get", return_value=_VOLUME_RANK_PAYLOAD):
            df = fetch_volume_rank(client, top_n=1)
    assert len(df) == 1
    assert df.iloc[0]["rank"] == 1


def test_fetch_volume_rank_api_error(tmp_path):
    """API 에러 시 빈 DataFrame 반환."""
    client = _make_client(tmp_path)
    with patch.object(auth, "get_token", return_value=_fake_token()):
        with patch.object(
            client, "get", side_effect=KISApiError("1", "ERR", "오류", {})
        ):
            df = fetch_volume_rank(client, top_n=30)
    assert df.empty


def test_fetch_volume_rank_empty_output(tmp_path):
    client = _make_client(tmp_path)
    payload = {"rt_cd": "0", "output": []}
    with patch.object(auth, "get_token", return_value=_fake_token()):
        with patch.object(client, "get", return_value=payload):
            df = fetch_volume_rank(client, top_n=30)
    assert df.empty


# ── fetch_quote ──────────────────────────────────────────────────────────────

_QUOTE_PAYLOAD = {
    "rt_cd": "0",
    "output": {
        "hts_kor_isnm": "제룡전기",
        "stck_prpr": "91300",
        "stck_prdy_clpr": "70230",
        "prdy_ctrt": "30.00",
        "stck_hgpr": "91300",
        "acml_vol": "5000000",
        "acml_tr_pbmn": "400000000000",
    },
}


def test_fetch_quote_normal(tmp_path):
    client = _make_client(tmp_path)
    with patch.object(auth, "get_token", return_value=_fake_token()):
        with patch.object(client, "get", return_value=_QUOTE_PAYLOAD):
            q = fetch_quote(client, "075180")
    assert q is not None
    assert q["code"] == "075180"
    assert q["price"] == 91_300
    assert q["is_limit_up"] is True


def test_fetch_quote_api_error(tmp_path):
    client = _make_client(tmp_path)
    with patch.object(auth, "get_token", return_value=_fake_token()):
        with patch.object(
            client, "get", side_effect=KISApiError("1", "ERR", "오류", {})
        ):
            q = fetch_quote(client, "075180")
    assert q is None


def test_fetch_quote_empty_output(tmp_path):
    client = _make_client(tmp_path)
    payload = {"rt_cd": "0", "output": {}}
    with patch.object(auth, "get_token", return_value=_fake_token()):
        with patch.object(client, "get", return_value=payload):
            q = fetch_quote(client, "075180")
    assert q is None
