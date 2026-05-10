"""WICS (Wise Industry Classification Standard) 섹터 매핑 크롤러.

FnGuide(에프앤가이드) 가 발표하는 GICS 호환 한국 산업분류. WI Sector 는
대분류 10개 / 중분류 28개 / 소분류 79개.

본 모듈은 **대분류 10개**만 적재 (v0). 중분류는 추후 확장.

API:
    URL: http://www.wiseindex.com/Index/GetIndexComponets
    파라미터:
        ceil_yn: 0 (구성종목 천장 미적용)
        dt:      YYYYMMDD
        sec_cd:  WICS 대분류 코드 (G10/G15/G20/G25/G30/G35/G40/G45/G50/G55)

    응답 (JSON):
        {
            "list": [
                {"CMP_CD": "005930", "CMP_KOR": "삼성전자",
                 "SEC_CD": "G45", "SEC_NM_KOR": "정보기술", ...},
                ...
            ]
        }

⚠ 응답 필드명/스키마는 wiseindex 공개 API 추정. 운영 시 한 번 검증 필요.
"""
from __future__ import annotations

import time
from datetime import date

import httpx
import pandas as pd
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

_API_URL = "http://www.wiseindex.com/Index/GetIndexComponets"
_USER_AGENT = (
    "Mozilla/5.0 (Linux; legendary_method WICS crawler) "
    "AppleWebKit/537.36 (KHTML, like Gecko)"
)
_TIMEOUT = 30.0
_INTER_REQUEST_DELAY = 1.0  # robots.txt 매너

# WICS 대분류 코드 → 한글 이름 (검증/표시용)
WICS_SECTOR_CODES: dict[str, str] = {
    "G10": "에너지",
    "G15": "소재",
    "G20": "산업재",
    "G25": "경기관련소비재",
    "G30": "필수소비재",
    "G35": "건강관리",
    "G40": "금융",
    "G45": "정보기술",
    "G50": "커뮤니케이션서비스",
    "G55": "유틸리티",
}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10), reraise=True)
def _fetch_one_sector(client: httpx.Client, sector_cd: str, dt: str) -> list[dict]:
    """단일 WICS 대분류 코드의 구성 종목 조회."""
    params = {"ceil_yn": "0", "dt": dt, "sec_cd": sector_cd}
    resp = client.get(_API_URL, params=params, timeout=_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    return list(data.get("list") or [])


def fetch_wics_sectors(target_date: date | None = None) -> pd.DataFrame:
    """WICS 10개 대분류 전체 크롤링 → long format DataFrame.

    Args:
        target_date: 기준 날짜. None 이면 오늘.

    Returns:
        columns=[code, name, sector_code, sector_name, crawled_at]
        실패한 섹터는 스킵, 부분 결과 반환. 모든 섹터 실패 시 빈 DataFrame.
    """
    if target_date is None:
        target_date = date.today()
    dt = target_date.strftime("%Y%m%d")

    rows: list[dict] = []
    headers = {"User-Agent": _USER_AGENT}
    with httpx.Client(headers=headers) as client:
        for sec_cd, sec_name in WICS_SECTOR_CODES.items():
            try:
                items = _fetch_one_sector(client, sec_cd, dt)
                logger.info(f"WICS {sec_cd} {sec_name}: {len(items)}종목")
                for item in items:
                    code = str(item.get("CMP_CD", "")).strip()
                    name = str(item.get("CMP_KOR", "")).strip()
                    if not code or len(code) != 6:
                        continue
                    rows.append({
                        "code": code,
                        "name": name,
                        "sector_code": sec_cd,
                        "sector_name": sec_name,
                        "crawled_at": target_date,
                    })
                time.sleep(_INTER_REQUEST_DELAY)  # robots.txt 매너
            except Exception as e:  # noqa: BLE001
                logger.error(f"WICS {sec_cd} 크롤링 실패: {e}")

    if not rows:
        logger.error("WICS 크롤링 전체 실패 — 빈 결과")
        return pd.DataFrame(columns=[
            "code", "name", "sector_code", "sector_name", "crawled_at",
        ])
    df = pd.DataFrame(rows)
    # 같은 종목이 여러 섹터에 중복 등록되지 않도록 (이론상 1:1) 첫 등장 보존
    df = df.drop_duplicates(subset=["code"], keep="first").reset_index(drop=True)
    return df
