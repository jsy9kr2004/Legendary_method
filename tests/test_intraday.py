"""src.data.intraday 테스트. KIS API는 mock."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import httpx
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


def test_fetch_quote_http_500_returns_none(tmp_path):
    """KIS 서버 500 (tenacity 3회 재시도 후 reraise) — 폴링 사이클을 죽이지 않고
    None 반환으로 격리. 실제 운영 케이스: 229200 같은 특정 종목의 일시적 5xx."""
    client = _make_client(tmp_path)
    req = httpx.Request("GET", "https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/inquire-price")
    resp = httpx.Response(500, request=req, text="Internal Server Error")
    err = httpx.HTTPStatusError("Server error '500 Internal Server Error'", request=req, response=resp)
    with patch.object(auth, "get_token", return_value=_fake_token()):
        with patch.object(client, "get", side_effect=err):
            q = fetch_quote(client, "229200")
    assert q is None


def test_fetch_quote_http_transport_error_returns_none(tmp_path):
    """네트워크 단절(ConnectError 등) — tenacity 재시도 후 reraise 시에도 None 반환."""
    client = _make_client(tmp_path)
    err = httpx.ConnectError("Connection refused")
    with patch.object(auth, "get_token", return_value=_fake_token()):
        with patch.object(client, "get", side_effect=err):
            q = fetch_quote(client, "229200")
    assert q is None


def test_fetch_volume_rank_http_500_returns_empty(tmp_path):
    """fetch_volume_rank 도 동일 패턴 — HTTP 5xx 시 빈 DataFrame."""
    client = _make_client(tmp_path)
    req = httpx.Request("GET", "https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/volume-rank")
    resp = httpx.Response(502, request=req, text="Bad Gateway")
    err = httpx.HTTPStatusError("Server error '502 Bad Gateway'", request=req, response=resp)
    with patch.object(auth, "get_token", return_value=_fake_token()):
        with patch.object(client, "get", side_effect=err):
            df = fetch_volume_rank(client, top_n=30)
    assert df.empty
    assert list(df.columns) == SNAPSHOT_COLUMNS


# ── 거래대금 vs 거래량 회귀 (2026-05-19 round 41 후속 3) ────────────────────
# 사용자 발견: 14:50 스냅샷 1~5위가 KODEX 인버스류로 도배 + 삼성전자 (실제 거래대금 1위)
# 가 15위로 밀림. 원인 — FID_BLNG_CLS_CODE="0" (평균거래량) 으로 보낸 버그. 정상은
# "3" (거래금액순). 종배/주도섹터 universe 전체가 거래량 top 30 으로 오염되어
# 5/12~5/18 5일 연속 0종목 현상 + round 41 backtest 검증 결과 모두 무효화됨.
#
# 본 테스트는 FID_BLNG_CLS_CODE 가 반드시 "3" (= 거래대금) 으로 나가는지 직접
# 검증한다. 누가 또 "0" 으로 되돌리면 즉시 실패.

def test_fetch_volume_rank_sends_trading_value_sort_axis(tmp_path):
    """FID_BLNG_CLS_CODE 가 '3' (거래금액순) 로 KIS 에 전송되는지 검증.

    함정: '0' 은 평균거래량 순이라 KODEX 200선물인버스2X 같은 저가 고회전 ETF 가
    1위로 잡힘. 종배 universe 가 무너진다 (2026-05-19 발견 버그).
    """
    client = _make_client(tmp_path)
    captured: dict = {}

    def fake_get(endpoint, tr_id, params=None):  # noqa: ARG001
        captured["params"] = params or {}
        return _VOLUME_RANK_PAYLOAD

    with patch.object(auth, "get_token", return_value=_fake_token()):
        with patch.object(client, "get", side_effect=fake_get):
            fetch_volume_rank(client, top_n=30)

    assert captured["params"]["FID_BLNG_CLS_CODE"] == "3", (
        f"FID_BLNG_CLS_CODE 가 '{captured['params'].get('FID_BLNG_CLS_CODE')}' "
        f"— 반드시 '3' (거래금액순) 이어야 함. '0' 은 평균거래량으로 universe 오염."
    )


def test_volume_rank_blng_cls_constant_is_trading_value():
    """상수가 '3' 으로 박혀 있는지 — 임의 변경 회귀 방지."""
    from src.data.intraday import _VOLUME_RANK_BLNG_CLS_TRADING_VALUE
    assert _VOLUME_RANK_BLNG_CLS_TRADING_VALUE == "3"


# ── rank / turnover_rank 회귀 (2026-05-18) ──────────────────────────────────
# 사용자 보고: 카드의 "(00위)" 가 HTS 와 다르다. 원인 — 이전엔 master 필터
# (ETF/펀드/리츠/스팩/우선주 제외) 후 rank 를 1부터 재부여하여 사용자가 HTS
# 거래대금 1위로 본 종목(예: KODEX200)이 빠지면 다음 보통주가 "1위" 로 표시됨.
# fix: KIS data_rank 그대로 유지. turnover_rank 컬럼 신설.

def test_fetch_volume_rank_preserves_kis_data_rank_with_master_filter(tmp_path):
    """master 필터로 ETF 제외 후에도 보통주의 rank 는 KIS data_rank 그대로
    유지. 빈 자리 메우는 재부여 X.
    """
    import pandas as pd
    payload = {
        "rt_cd": "0",
        "output": [
            # KIS 응답상 1위: ETF (master 에 없어서 제외)
            {
                "data_rank": "1",
                "mksc_shrn_iscd": "069500",  # KODEX200
                "hts_kor_isnm": "KODEX 200",
                "stck_prpr": "35000",
                "stck_prdy_clpr": "34800",
                "prdy_ctrt": "0.57",
                "stck_hgpr": "35100",
                "acml_vol": "10000000",
                "acml_tr_pbmn": "350000000000",
                "tr_pbmn_tnrt": "5.0",
            },
            # KIS 응답상 2위: 삼성전자 (master 에 있음)
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
                "tr_pbmn_tnrt": "0.3",
            },
            # KIS 응답상 3위: ETF 또 (제외)
            {
                "data_rank": "3",
                "mksc_shrn_iscd": "229200",  # KODEX 코스닥150
                "hts_kor_isnm": "KODEX 코스닥150",
                "stck_prpr": "12000",
                "stck_prdy_clpr": "11900",
                "prdy_ctrt": "0.84",
                "stck_hgpr": "12100",
                "acml_vol": "8000000",
                "acml_tr_pbmn": "96000000000",
                "tr_pbmn_tnrt": "3.0",
            },
            # KIS 응답상 4위: 제룡전기
            {
                "data_rank": "4",
                "mksc_shrn_iscd": "075180",
                "hts_kor_isnm": "제룡전기",
                "stck_prpr": "91300",
                "stck_prdy_clpr": "70230",
                "prdy_ctrt": "30.00",
                "stck_hgpr": "91300",
                "acml_vol": "5000000",
                "acml_tr_pbmn": "400000000000",
                "tr_pbmn_tnrt": "20.0",
            },
        ],
    }
    master = pd.DataFrame([
        {"code": "005930", "market_cap": 4_800_000},
        {"code": "075180", "market_cap": 2_000},
        # ETF 2개는 master 에 없음
    ])
    client = _make_client(tmp_path)
    with patch.object(auth, "get_token", return_value=_fake_token()):
        with patch.object(client, "get", return_value=payload):
            df = fetch_volume_rank(client, top_n=30, master_df=master)

    assert len(df) == 2  # ETF 2개 제외 후 보통주만
    # 핵심: rank 가 1, 2 가 아니라 KIS data_rank (2, 4) 유지.
    samsung = df[df["code"] == "005930"].iloc[0]
    jeryong = df[df["code"] == "075180"].iloc[0]
    assert samsung["rank"] == 2, f"삼성전자 rank 가 KIS 원본 2 위가 아님: {samsung['rank']}"
    assert jeryong["rank"] == 4, f"제룡전기 rank 가 KIS 원본 4 위가 아님: {jeryong['rank']}"


def test_fetch_volume_rank_assigns_turnover_rank(tmp_path):
    """turnover_rank 컬럼이 master 필터 통과 종목들의 turnover 내림차순으로
    부여됨. 회전율 1위가 turnover_rank=1, 2위가 2.
    """
    import pandas as pd
    payload = {
        "rt_cd": "0",
        "output": [
            {  # 삼성전자 — 회전율 낮음 (대형주)
                "data_rank": "1", "mksc_shrn_iscd": "005930",
                "hts_kor_isnm": "삼성전자",
                "stck_prpr": "80000", "stck_prdy_clpr": "79000",
                "prdy_ctrt": "1.27", "stck_hgpr": "81000",
                "acml_vol": "20000000", "acml_tr_pbmn": "1600000000000",
                "tr_pbmn_tnrt": "0.3",
            },
            {  # 제룡전기 — 회전율 1위 (단타 종목)
                "data_rank": "2", "mksc_shrn_iscd": "075180",
                "hts_kor_isnm": "제룡전기",
                "stck_prpr": "91300", "stck_prdy_clpr": "70230",
                "prdy_ctrt": "30.00", "stck_hgpr": "91300",
                "acml_vol": "5000000", "acml_tr_pbmn": "400000000000",
                "tr_pbmn_tnrt": "20.0",
            },
            {  # 중간 종목 — 회전율 5%
                "data_rank": "3", "mksc_shrn_iscd": "091340",
                "hts_kor_isnm": "대한광통신",
                "stck_prpr": "5000", "stck_prdy_clpr": "4800",
                "prdy_ctrt": "4.17", "stck_hgpr": "5100",
                "acml_vol": "1000000", "acml_tr_pbmn": "5000000000",
                "tr_pbmn_tnrt": "5.0",
            },
        ],
    }
    master = pd.DataFrame([
        {"code": "005930", "market_cap": 4_800_000},
        {"code": "075180", "market_cap": 2_000},
        {"code": "091340", "market_cap": 1_000},
    ])
    client = _make_client(tmp_path)
    with patch.object(auth, "get_token", return_value=_fake_token()):
        with patch.object(client, "get", return_value=payload):
            df = fetch_volume_rank(client, top_n=30, master_df=master)

    assert "turnover_rank" in df.columns
    # 제룡전기(20%) > 대한광통신(5%) > 삼성전자(0.3%)
    assert df[df["code"] == "075180"].iloc[0]["turnover_rank"] == 1
    assert df[df["code"] == "091340"].iloc[0]["turnover_rank"] == 2
    assert df[df["code"] == "005930"].iloc[0]["turnover_rank"] == 3
