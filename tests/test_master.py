"""src.data.master 테스트. mst.zip 다운로드는 mock — bytes 만 주입."""
from __future__ import annotations

from unittest.mock import patch

from src.data import master


def _make_mst_line(short_code: str, kor_name: str) -> bytes:
    """0~8 단축코드(9) + 9~20 표준코드(12) + 21~60 한글명(40, CP949) + padding."""
    short = short_code.zfill(9).encode("cp949")
    std = ("KR" + short_code.zfill(10)).encode("cp949")[:12].ljust(12, b" ")
    kor_bytes = kor_name.encode("cp949")
    kor_padded = kor_bytes + b" " * (40 - len(kor_bytes))
    padding = b" " * 200
    return short + std + kor_padded + padding + b"\n"


def test_parse_mst_extracts_code_and_name():
    content = _make_mst_line("005930", "삼성전자") + _make_mst_line("000660", "SK하이닉스")
    df = master._parse_mst(content, "KOSPI")

    assert len(df) == 2
    assert set(df["code"]) == {"005930", "000660"}
    assert df.loc[df["code"] == "005930", "name"].iloc[0] == "삼성전자"
    assert df.loc[df["code"] == "000660", "name"].iloc[0] == "SK하이닉스"
    assert (df["market"] == "KOSPI").all()


def test_parse_mst_skips_short_lines():
    content = b"shortline\n" + _make_mst_line("005930", "삼성전자")
    df = master._parse_mst(content, "KOSPI")
    assert len(df) == 1


def test_parse_mst_columns_match_storage_schema():
    content = _make_mst_line("005930", "삼성전자")
    df = master._parse_mst(content, "KOSPI")
    assert list(df.columns) == ["code", "name", "market", "market_cap", "listed_at"]


def test_fetch_stock_master_concatenates_markets():
    kospi_bytes = _make_mst_line("005930", "삼성전자")
    kosdaq_bytes = _make_mst_line("091990", "셀트리온제약")

    def fake_download(url: str) -> bytes:
        return kospi_bytes if "kospi" in url else kosdaq_bytes

    with patch.object(master, "_download_mst", side_effect=fake_download):
        df = master.fetch_stock_master()

    assert len(df) == 2
    assert set(df["market"]) == {"KOSPI", "KOSDAQ"}
    assert "005930" in set(df["code"])
    assert "091990" in set(df["code"])
