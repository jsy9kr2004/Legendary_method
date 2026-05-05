"""KIS 종목 마스터 다운로드 / 파싱.

KIS 가 제공하는 KOSPI/KOSDAQ 마스터 zip 을 받아서 보통주(주권 ST)만 필터링한
DataFrame 을 반환한다.

mst 파일 구조 (KIS Open API GitHub `open-trading-api` 참조):
    | offset | length | content                                                  |
    | ------ | ------ | -------------------------------------------------------- |
    | 0      | 9      | 단축코드 (좌측 0 패딩, 마지막 6자가 KRX 종목코드)           |
    | 9      | 12     | 표준코드 (KR + ...)                                       |
    | 21     | 가변   | 한글종목명 (line 끝 - part2_len 까지)                     |
    | (line 끝 - part2_len) | 2 | 그룹코드 (ST/EF/EW/EN/...)                  |

    part2_len: KOSPI=228, KOSDAQ=222

그룹코드:
    ST  주권 (보통주/우선주)  ← 우리가 받을 것
    EF  ETF
    EW  ELW
    EN  ETN
    DR  신주인수권증권
    SC  수익증권
    IF  인프라투융자회사
    ...

인코딩: CP949 (한글 2 bytes)
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

_KOSPI_PART2_LEN = 228
_KOSDAQ_PART2_LEN = 222

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


def _parse_mst(content: bytes, market: str, group_filter: str | None = "ST") -> pd.DataFrame:
    """KIS mst 바이너리 → DataFrame.

    `group_filter` 가 주어지면 그 그룹코드만 통과 (default: ST=주권).
    None 으로 주면 전체 반환.
    """
    part2_len = _part2_len(market)
    min_len = _KORNAME_OFFSET + part2_len + 1  # 한글명 최소 1byte

    rows: list[dict] = []
    for line in content.split(b"\n"):
        if len(line) < min_len:
            continue
        try:
            short_code = line[0:_SHORTCODE_LEN].decode("cp949", errors="replace").strip()
            kor_name_end = len(line) - part2_len
            kor_name = line[_KORNAME_OFFSET:kor_name_end].decode("cp949", errors="replace").strip()
            part2 = line[-part2_len:]
            group_code = part2[0:2].decode("cp949", errors="replace").strip()
        except UnicodeDecodeError:
            continue

        krx_code = short_code[-6:] if len(short_code) >= 6 else short_code
        if len(krx_code) != 6 or not krx_code.isalnum():
            continue
        if group_filter is not None and group_code != group_filter:
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


def fetch_stock_master(group_filter: str | None = "ST") -> pd.DataFrame:
    """KOSPI + KOSDAQ 합본. 기본은 보통주(ST)만.

    `group_filter=None` 이면 ETF/ETN 등 모두 포함.
    매일 16:30 갱신 권장.
    """
    logger.info("KIS 종목 마스터 다운로드 (KOSPI)")
    kospi = _parse_mst(_download_mst(KOSPI_URL), "KOSPI", group_filter)
    logger.info(f"KOSPI {len(kospi)} 종목 (group={group_filter or 'ALL'})")

    logger.info("KIS 종목 마스터 다운로드 (KOSDAQ)")
    kosdaq = _parse_mst(_download_mst(KOSDAQ_URL), "KOSDAQ", group_filter)
    logger.info(f"KOSDAQ {len(kosdaq)} 종목 (group={group_filter or 'ALL'})")

    return pd.concat([kospi, kosdaq], ignore_index=True)
