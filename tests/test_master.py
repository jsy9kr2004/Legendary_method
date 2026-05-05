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


def test_parse_mst_default_filters_to_ST():
    content = (
        _make_mst_line("005930", "삼성전자", "KOSPI", "ST")
        + _make_mst_line("100030", "한투ETF", "KOSPI", "EF")
        + _make_mst_line("000660", "SK하이닉스", "KOSPI", "ST")
    )
    df = master._parse_mst(content, "KOSPI")
    assert len(df) == 2
    assert set(df["code"]) == {"005930", "000660"}
    assert df.loc[df["code"] == "005930", "name"].iloc[0] == "삼성전자"


def test_parse_mst_no_filter_returns_all():
    content = (
        _make_mst_line("005930", "삼성전자", "KOSPI", "ST")
        + _make_mst_line("100030", "한투ETF", "KOSPI", "EF")
    )
    df = master._parse_mst(content, "KOSPI", group_filter=None)
    assert len(df) == 2


def test_parse_mst_custom_filter():
    content = (
        _make_mst_line("005930", "삼성전자", "KOSPI", "ST")
        + _make_mst_line("100030", "한투ETF", "KOSPI", "EF")
    )
    df = master._parse_mst(content, "KOSPI", group_filter="EF")
    assert len(df) == 1
    assert df.iloc[0]["code"] == "100030"


def test_parse_mst_kosdaq_uses_222_part2():
    """KOSDAQ part2 길이 222 (KOSPI 228 과 다름)."""
    content = _make_mst_line("091990", "셀트리온제약", "KOSDAQ", "ST")
    df = master._parse_mst(content, "KOSDAQ")
    assert len(df) == 1
    assert df.iloc[0]["code"] == "091990"
    assert df.iloc[0]["name"] == "셀트리온제약"


def test_parse_mst_skips_short_lines():
    content = b"shortline\n" + _make_mst_line("005930", "삼성전자")
    df = master._parse_mst(content, "KOSPI")
    assert len(df) == 1


def test_parse_mst_columns_match_schema():
    df = master._parse_mst(_make_mst_line("005930", "삼성전자"), "KOSPI")
    assert list(df.columns) == ["code", "name", "market", "market_cap", "listed_at"]


def test_parse_mst_handles_cr_lf():
    """일부 mst 가 CRLF 일 수 있음 — \\r 제거되는지."""
    line = _make_mst_line("005930", "삼성전자")
    # \n 앞에 \r 끼워넣기
    line_crlf = line.replace(b"\n", b"\r\n")
    df = master._parse_mst(line_crlf, "KOSPI")
    assert len(df) == 1
    assert df.iloc[0]["code"] == "005930"


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
