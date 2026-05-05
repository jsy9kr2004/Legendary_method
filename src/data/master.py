"""KIS 종목 마스터 다운로드 / 파싱.

KIS 가 제공하는 KOSPI/KOSDAQ 마스터 zip 을 받아서 보통주(주권 ST) 만 필터링한
DataFrame 을 반환한다.

mst 파일 구조 (KIS Open API GitHub `open-trading-api` 참조,
**문자 단위(char)** 슬라이스):
    | offset | length | content                                                  |
    | ------ | ------ | -------------------------------------------------------- |
    | 0      | 9      | 단축코드 (좌측 0 패딩, 마지막 6자가 KRX 종목코드)           |
    | 9      | 12     | 표준코드 (KR + ...)                                       |
    | 21     | 가변   | 한글종목명 (line 끝 - part2_len 까지)                     |
    | (len - part2_len) | 2 | 그룹코드 (ST/EF/EW/EN/...)                       |

    part2_len(char): KOSPI=228, KOSDAQ=222

그룹코드(파트2 첫 2 chars):
    KOSPI 는 보통 'ST' 2자, KOSDAQ 는 'S '/'F '/'D ' 등 1자 + 공백.
    그래서 첫 1 char 만 비교한다.

    S  주권 (보통주/우선주, 종배 룰 대상)  ← default 필터
    E  ETF / ETN / ELW
    F  펀드 / 수익증권
    D  DR (신주인수권증권)

인코딩: CP949. 한글 1자 = 1 char = 2 bytes 라 byte 슬라이스 X, 반드시
**decode 후 char 단위 슬라이스**.
"""
from __future__ import annotations

import io
import zipfile

import httpx
import pandas as pd
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

KOSPI_URL = "https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip"
KOSDAQ_URL = "https://new.real.download.dws.co.kr/common/master/kosdaq_code.mst.zip"

_KOSPI_PART2_LEN = 228   # char 단위
_KOSDAQ_PART2_LEN = 222  # char 단위

_SHORTCODE_LEN = 9
_STDCODE_LEN = 12
_KORNAME_OFFSET = _SHORTCODE_LEN + _STDCODE_LEN  # 21

_OUTPUT_COLS = ["code", "name", "market", "market_cap", "listed_at"]


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10), reraise=True)
def _download_mst(url: str) -> bytes:
    resp = httpx.get(url, timeout=30.0, follow_redirects=True)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
        name = z.namelist()[0]
        return z.read(name)


def _part2_len(market: str) -> int:
    return _KOSPI_PART2_LEN if market == "KOSPI" else _KOSDAQ_PART2_LEN


def _parse_mst(content: bytes, market: str, group_prefix: str | None = "S") -> pd.DataFrame:
    """KIS mst → DataFrame.

    `content` 는 raw bytes. cp949 로 decode 후 char 단위로 슬라이스.
    `group_prefix`: 그룹코드 첫 글자 매칭 (default 'S'=주권). None 이면 전체.
    """
    text = content.decode("cp949", errors="replace")
    part2_len = _part2_len(market)
    min_len = _KORNAME_OFFSET + part2_len + 1

    rows: list[dict] = []
    for line in text.split("\n"):
        line = line.rstrip("\r")
        if len(line) < min_len:
            continue

        short_code = line[0:_SHORTCODE_LEN].strip()
        kor_name_end = len(line) - part2_len
        kor_name = line[_KORNAME_OFFSET:kor_name_end].strip()
        part2 = line[-part2_len:]
        group_code = part2[0:2].strip()  # KOSPI 'ST' / KOSDAQ 'S' 등

        krx_code = short_code[-6:] if len(short_code) >= 6 else short_code
        if len(krx_code) != 6 or not krx_code.isalnum():
            continue
        if group_prefix is not None:
            if not group_code or not group_code.startswith(group_prefix):
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


def fetch_stock_master(group_prefix: str | None = "S") -> pd.DataFrame:
    """KOSPI + KOSDAQ 합본. 기본은 주권(S 시작 — KOSPI 'ST', KOSDAQ 'S').

    `group_prefix=None` 이면 ETF/ETN 등 모두 포함.
    매일 16:30 갱신 권장.
    """
    logger.info("KIS 종목 마스터 다운로드 (KOSPI)")
    kospi = _parse_mst(_download_mst(KOSPI_URL), "KOSPI", group_prefix)
    logger.info(f"KOSPI {len(kospi)} 종목 (group_prefix={group_prefix or 'ALL'})")

    logger.info("KIS 종목 마스터 다운로드 (KOSDAQ)")
    kosdaq = _parse_mst(_download_mst(KOSDAQ_URL), "KOSDAQ", group_prefix)
    logger.info(f"KOSDAQ {len(kosdaq)} 종목 (group_prefix={group_prefix or 'ALL'})")

    return pd.concat([kospi, kosdaq], ignore_index=True)
