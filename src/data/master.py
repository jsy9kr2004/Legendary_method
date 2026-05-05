"""KIS 종목 마스터 다운로드 / 파싱.

KIS 가 제공하는 KOSPI/KOSDAQ 마스터 zip 을 받아서 단축코드(6) + 한글종목명 +
시장 구분 컬럼으로 파싱한다. (인코딩: CP949, 고정 byte 폭)

source: KIS Open API GitHub samples (`open-trading-api`)
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import httpx
import pandas as pd
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

KOSPI_URL = "https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip"
KOSDAQ_URL = "https://new.real.download.dws.co.kr/common/master/kosdaq_code.mst.zip"

# byte 단위 폭 (KIS 공식 샘플 기준)
_SHORTCODE_OFFSET = 0
_SHORTCODE_LEN = 9   # 좌측 0 패딩, 실제 종목코드는 마지막 6자리
_STDCODE_OFFSET = 9
_STDCODE_LEN = 12
_KORNAME_OFFSET = 21
_KORNAME_LEN = 40

_OUTPUT_COLS = ["code", "name", "market", "market_cap", "listed_at"]


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10), reraise=True)
def _download_mst(url: str) -> bytes:
    resp = httpx.get(url, timeout=30.0, follow_redirects=True)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
        name = z.namelist()[0]
        return z.read(name)


def _parse_mst(content: bytes, market: str) -> pd.DataFrame:
    """KIS mst 바이너리 → DataFrame.

    한 라인에서 byte offset 으로 단축코드/표준코드/한글명을 슬라이스.
    한글은 CP949 (2 bytes/char) 라 byte 단위 슬라이스 후 디코드해야 함.
    """
    rows: list[dict] = []
    for line in content.split(b"\n"):
        if len(line) < _KORNAME_OFFSET + _KORNAME_LEN:
            continue
        try:
            short_code = (
                line[_SHORTCODE_OFFSET : _SHORTCODE_OFFSET + _SHORTCODE_LEN]
                .decode("cp949", errors="replace")
                .strip()
            )
            kor_name = (
                line[_KORNAME_OFFSET : _KORNAME_OFFSET + _KORNAME_LEN]
                .decode("cp949", errors="replace")
                .strip()
            )
        except UnicodeDecodeError:
            continue

        # KIS 단축코드는 9자 좌측 0 패딩 + KRX 종목코드 6자리.
        krx_code = short_code[-6:] if len(short_code) >= 6 else short_code
        if len(krx_code) != 6 or not krx_code.isalnum():
            continue

        rows.append(
            {
                "code": krx_code,
                "name": kor_name,
                "market": market,
                "market_cap": 0,
                "listed_at": None,
            }
        )
    return pd.DataFrame(rows, columns=_OUTPUT_COLS)


def fetch_stock_master() -> pd.DataFrame:
    """KOSPI + KOSDAQ 합본. 매일 16:30 갱신 권장."""
    logger.info("KIS 종목 마스터 다운로드 (KOSPI)")
    kospi = _parse_mst(_download_mst(KOSPI_URL), "KOSPI")
    logger.info(f"KOSPI {len(kospi)} 종목")

    logger.info("KIS 종목 마스터 다운로드 (KOSDAQ)")
    kosdaq = _parse_mst(_download_mst(KOSDAQ_URL), "KOSDAQ")
    logger.info(f"KOSDAQ {len(kosdaq)} 종목")

    return pd.concat([kospi, kosdaq], ignore_index=True)
