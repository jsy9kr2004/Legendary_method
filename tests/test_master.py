"""src.data.master 테스트. mst.zip 다운로드는 mock — bytes 만 주입.

KIS mst 는 cp949 인코딩, **문자 단위(char)** 폭. 한글 1자 = 1 char = 2 byte.
모킹은 str 로 만든 후 cp949 로 인코딩해 bytes 로 반환한다.
"""
from __future__ import annotations

from unittest.mock import patch

from src.data import master


def _make_mst_line(
    short_code: str,
    kor_name: str,
    market: str = "KOSPI",
    group_code: str = "ST",
) -> bytes:
    """KIS mst 한 라인 모킹.

    char 단위로 단축(9) + 표준(12) + 한글명 + part2(228 KOSPI / 222 KOSDAQ).
    part2 의 첫 2 char = 그룹코드.
    """
    short = short_code.zfill(9)
    std = ("KR" + short_code.zfill(10))[:12].ljust(12)
    part2_len = 228 if market == "KOSPI" else 222
    group = group_code.ljust(2)[:2]
    part2 = group + " " * (part2_len - 2)
    line = short + std + kor_name + part2 + "\n"
    return line.encode("cp949")


def test_parse_mst_default_filters_to_S_prefix():
    """KOSPI 'ST' / KOSDAQ 'S' 모두 prefix='S' 로 통과."""
    content = (
        _make_mst_line("005930", "삼성전자", "KOSPI", "ST")
        + _make_mst_line("100030", "한투ETF", "KOSPI", "EF")
        + _make_mst_line("000660", "SK하이닉스", "KOSPI", "ST")
    )
    df = master._parse_mst(content, "KOSPI")  # default 'S'
    assert len(df) == 2
    assert set(df["code"]) == {"005930", "000660"}
    assert df.loc[df["code"] == "005930", "name"].iloc[0] == "삼성전자"


def test_parse_mst_kosdaq_single_char_S_passes():
    """KOSDAQ 그룹코드는 'S '(공백 패딩 1자)로 저장됨."""
    content = _make_mst_line("091990", "셀트리온제약", "KOSDAQ", "S")
    df = master._parse_mst(content, "KOSDAQ")  # default 'S'
    assert len(df) == 1
    assert df.iloc[0]["code"] == "091990"


def test_parse_mst_no_filter_returns_all():
    content = (
        _make_mst_line("005930", "삼성전자", "KOSPI", "ST")
        + _make_mst_line("100030", "한투ETF", "KOSPI", "EF")
    )
    df = master._parse_mst(content, "KOSPI", group_prefix=None)
    assert len(df) == 2


def test_parse_mst_custom_prefix():
    content = (
        _make_mst_line("005930", "삼성전자", "KOSPI", "ST")
        + _make_mst_line("100030", "한투ETF", "KOSPI", "EF")
    )
    df = master._parse_mst(content, "KOSPI", group_prefix="E")
    assert len(df) == 1
    assert df.iloc[0]["code"] == "100030"


def test_parse_mst_kosdaq_uses_222_part2():
    content = _make_mst_line("091990", "셀트리온제약", "KOSDAQ", "S")
    df = master._parse_mst(content, "KOSDAQ")
    assert len(df) == 1
    assert df.iloc[0]["name"] == "셀트리온제약"


def test_is_preferred_stock():
    assert master.is_preferred_stock("005930") is False
    assert master.is_preferred_stock("005935") is True   # 1우
    assert master.is_preferred_stock("005937") is True   # 2우
    assert master.is_preferred_stock("005939") is True   # 3우
    assert master.is_preferred_stock("12345") is False   # 6자리 아님


def test_parse_mst_include_preferred_default_true():
    content = (
        _make_mst_line("005930", "삼성전자", "KOSPI", "ST")
        + _make_mst_line("005935", "삼성전자우", "KOSPI", "ST")
        + _make_mst_line("005937", "삼성전자2우B", "KOSPI", "ST")
    )
    df = master._parse_mst(content, "KOSPI")  # default include_preferred=True
    assert len(df) == 3


def test_parse_mst_exclude_preferred():
    content = (
        _make_mst_line("005930", "삼성전자", "KOSPI", "ST")
        + _make_mst_line("005935", "삼성전자우", "KOSPI", "ST")
        + _make_mst_line("005937", "삼성전자2우B", "KOSPI", "ST")
    )
    df = master._parse_mst(content, "KOSPI", include_preferred=False)
    assert len(df) == 1
    assert df.iloc[0]["code"] == "005930"


def test_parse_mst_skips_short_lines():
    content = b"shortline\n" + _make_mst_line("005930", "삼성전자")
    df = master._parse_mst(content, "KOSPI")
    assert len(df) == 1


def test_parse_mst_columns_match_schema():
    df = master._parse_mst(_make_mst_line("005930", "삼성전자"), "KOSPI")
    assert list(df.columns) == ["code", "name", "market", "group_code", "market_cap", "listed_at"]


def test_parse_mst_handles_cr_lf():
    """일부 mst 가 CRLF 일 수 있음 — \\r 제거되는지."""
    line = _make_mst_line("005930", "삼성전자")
    # \n 앞에 \r 끼워넣기
    line_crlf = line.replace(b"\n", b"\r\n")
    df = master._parse_mst(line_crlf, "KOSPI")
    assert len(df) == 1
    assert df.iloc[0]["code"] == "005930"


def test_is_etf_like_name():
    assert master.is_etf_like_name("KODEX 200") is True
    assert master.is_etf_like_name("TIGER 미국S&P500") is True
    assert master.is_etf_like_name("KBSTAR 게임테마") is True
    assert master.is_etf_like_name("ARIRANG 고배당") is True
    assert master.is_etf_like_name("삼성전자") is False
    assert master.is_etf_like_name("제룡전기") is False
    assert master.is_etf_like_name("") is False


def test_is_tradable_for_jongbae_basic():
    assert master.is_tradable_for_jongbae("005930", "삼성전자", "ST") is True
    assert master.is_tradable_for_jongbae("091990", "셀트리온제약", "S") is True


def test_is_tradable_for_jongbae_blocks_etf_group():
    assert master.is_tradable_for_jongbae("100030", "한투ETF", "EF") is False
    assert master.is_tradable_for_jongbae("123456", "어떤ETN", "EN") is False
    assert master.is_tradable_for_jongbae("111111", "어떤펀드", "FU") is False


def test_is_tradable_for_jongbae_blocks_etf_name():
    assert master.is_tradable_for_jongbae("123456", "KODEX 200", "ST") is False
    assert master.is_tradable_for_jongbae("123456", "TIGER 헬스케어", "ST") is False
    assert master.is_tradable_for_jongbae("123456", "KBSTAR 게임", "ST") is False


def test_is_tradable_for_jongbae_blocks_spac_and_reit():
    assert master.is_tradable_for_jongbae("123456", "어떤스팩제3호", "ST") is False
    assert master.is_tradable_for_jongbae("123456", "신한알파리츠", "ST") is False
    assert master.is_tradable_for_jongbae("123456", "어떤것 ETF", "ST") is False
    assert master.is_tradable_for_jongbae("123456", "어떤것 ETN", "ST") is False


def test_is_tradable_for_jongbae_invalid_code():
    assert master.is_tradable_for_jongbae("12345", "삼성전자", "ST") is False
    assert master.is_tradable_for_jongbae("ABCDEF", "삼성전자", "ST") is False


def test_parse_mst_jongbae_only_excludes_etf_name():
    """그룹코드는 'S' 지만 종목명이 KODEX 인 경우 jongbae_only 로 제외 (실제론 드뭄)."""
    content = (
        _make_mst_line("005930", "삼성전자", "KOSPI", "ST")
        + _make_mst_line("123456", "KODEX200", "KOSPI", "ST")
    )
    df = master._parse_mst(content, "KOSPI", jongbae_only=True)
    assert len(df) == 1
    assert df.iloc[0]["code"] == "005930"


def test_parse_mst_jongbae_only_keeps_normal_stocks():
    content = _make_mst_line("005930", "삼성전자", "KOSPI", "ST")
    df = master._parse_mst(content, "KOSPI", jongbae_only=True)
    assert len(df) == 1


def test_parse_int_safe():
    assert master._parse_int_safe("12345") == 12345
    assert master._parse_int_safe("  12345  ") == 12345
    assert master._parse_int_safe("") == 0
    assert master._parse_int_safe("abc") == 0
    assert master._parse_int_safe(None) == 0  # type: ignore


def test_parse_listed_at():
    from datetime import date
    assert master._parse_listed_at("19980101") == date(1998, 1, 1)
    assert master._parse_listed_at("20240315") == date(2024, 3, 15)
    assert master._parse_listed_at("") is None
    assert master._parse_listed_at("99999999") is None
    assert master._parse_listed_at("abcd1234") is None


def test_fetch_stock_master_concatenates_markets():
    kospi_bytes = _make_mst_line("005930", "삼성전자", "KOSPI", "ST")
    kosdaq_bytes = _make_mst_line("091990", "셀트리온제약", "KOSDAQ", "ST")

    def fake_download(url: str) -> bytes:
        return kospi_bytes if "kospi" in url else kosdaq_bytes

    with patch.object(master, "_download_mst", side_effect=fake_download):
        df = master.fetch_stock_master()

    assert len(df) == 2
    assert set(df["market"]) == {"KOSPI", "KOSDAQ"}
    assert "005930" in set(df["code"])
    assert "091990" in set(df["code"])


# ── 시총/상장주식수 backfill (스냅샷 역산, 2026-05-24) ─────────────────────────

def _write_snapshot(tmp_path, day: str, hhmm: str, rows: list[dict]) -> None:
    import pandas as pd
    p = tmp_path / "intraday" / "snapshots" / day
    p.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(p / f"{hhmm}.parquet", index=False)


def test_backfill_market_cap_from_snapshots(tmp_path):
    """거래대금/회전율 역산으로 시총(억)+상장주식수 채움. 미등장 종목은 0 유지."""
    import pandas as pd
    # 05-20: 062970 거래대금 200억 / 회전율 20% → 시총 1000억
    _write_snapshot(tmp_path, "2026-05-20", "14_50", [
        {"code": "062970", "price": 2000, "trading_value": 20_000_000_000, "turnover": 20.0},
    ])
    # 05-21 (더 최신): 062970 거래대금 100억 / 회전율 10% → 시총 1000억, price 2500
    #                 009150 거래대금 1조 / 회전율 1% → 시총 100조 (=1,000,000억)
    _write_snapshot(tmp_path, "2026-05-21", "14_50", [
        {"code": "062970", "price": 2500, "trading_value": 10_000_000_000, "turnover": 10.0},
        {"code": "009150", "price": 1_000_000, "trading_value": 1_000_000_000_000, "turnover": 1.0},
    ])
    master_df = pd.DataFrame({
        "code": ["062970", "009150", "999999"],
        "name": ["한국첨단소재", "삼성전기", "없는종목"],
        "market": ["KOSDAQ", "KOSPI", "KOSPI"],
        "market_cap": [0, 0, 0],
    })
    out = master.backfill_market_cap_from_snapshots(master_df, tmp_path)
    rec = {r["code"]: r for _, r in out.iterrows()}

    # 062970: 최신(05-21) 채택 — 시총 1000억, shares = 1000억원 / 2500
    assert rec["062970"]["market_cap"] == 1000
    assert rec["062970"]["shares"] == int(round(1000 * 1e8 / 2500))  # 4,000,000 (05-20 이었으면 5,000,000)
    # 009150: 시총 100조 = 1,000,000억
    assert rec["009150"]["market_cap"] == 1_000_000
    # 스냅샷 미등장 종목 → 0 유지
    assert rec["999999"]["market_cap"] == 0
    assert rec["999999"]["shares"] == 0


def test_backfill_skips_zero_or_missing_turnover(tmp_path):
    """회전율 0/거래대금 0/가격 0 행은 역산 불가 → 0 유지."""
    import pandas as pd
    _write_snapshot(tmp_path, "2026-05-21", "14_50", [
        {"code": "062970", "price": 2500, "trading_value": 10_000_000_000, "turnover": 0.0},
        {"code": "036540", "price": 0, "trading_value": 10_000_000_000, "turnover": 5.0},
    ])
    master_df = pd.DataFrame({
        "code": ["062970", "036540"], "name": ["x", "y"],
        "market": ["KOSDAQ", "KOSDAQ"], "market_cap": [0, 0],
    })
    out = master.backfill_market_cap_from_snapshots(master_df, tmp_path)
    assert out.set_index("code").loc["062970", "market_cap"] == 0
    assert out.set_index("code").loc["036540", "market_cap"] == 0


def test_backfill_no_snapshots_keeps_existing(tmp_path):
    """스냅샷 디렉토리 없으면 기존 market_cap 보존 (예외 X)."""
    import pandas as pd
    master_df = pd.DataFrame({
        "code": ["062970"], "name": ["x"], "market": ["KOSDAQ"], "market_cap": [777],
    })
    out = master.backfill_market_cap_from_snapshots(master_df, tmp_path)
    assert out.iloc[0]["market_cap"] == 777  # 기존 비-0 값 보존
    assert out.iloc[0]["shares"] == 0
