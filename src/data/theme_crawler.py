"""네이버 금융 테마 크롤러.

크롤링 대상:
    1. 전체 테마 목록
       URL: https://finance.naver.com/sise/theme.naver?page={page}
       → theme_id, theme_name 추출

    2. 테마별 구성 종목
       URL: https://finance.naver.com/sise/sise_group_detail.naver?type=theme&no={theme_id}
       → 종목 코드 추출

저장 형식 (Long format):
    code, theme, crawled_at
    "075180", "전기/전선", 2026-05-06
    "075180", "원자력", 2026-05-06

정책:
    - User-Agent 헤더 명시 (Naver 요청 식별용)
    - 요청 간 1초 sleep (서버 부하 방지)
    - 요청 실패 시 해당 테마는 건너뜀 (fail-loud: 경고 로그)
    - robots.txt: naver.com/robots.txt 에 /sise/ 경로 크롤링 금지 없음 확인됨
"""
from __future__ import annotations

import time
from datetime import date
from typing import Iterator

import requests
from bs4 import BeautifulSoup
from loguru import logger

_BASE_URL = "https://finance.naver.com"
_THEME_LIST_URL = f"{_BASE_URL}/sise/theme.naver"
_THEME_DETAIL_URL = f"{_BASE_URL}/sise/sise_group_detail.naver"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; LegendaryMethodBot/1.0; "
        "+https://github.com/jsy9kr2004/Legendary_method)"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Referer": "https://finance.naver.com/",
}

_REQUEST_INTERVAL_SEC = 1.0
_REQUEST_TIMEOUT_SEC = 15


def _get(url: str, params: dict | None = None) -> BeautifulSoup:
    """HTTP GET + BeautifulSoup 파싱. 실패 시 requests.HTTPError raise."""
    resp = requests.get(
        url,
        params=params,
        headers=_HEADERS,
        timeout=_REQUEST_TIMEOUT_SEC,
    )
    resp.raise_for_status()
    # 네이버 금융은 EUC-KR 인코딩 혼용. encoding 명시.
    resp.encoding = resp.apparent_encoding
    return BeautifulSoup(resp.text, "lxml")


def _parse_theme_list_page(soup: BeautifulSoup) -> list[tuple[str, str]]:
    """테마 목록 페이지에서 (theme_id, theme_name) 추출.

    HTML 패턴:
        <a href="/sise/sise_group_detail.naver?type=theme&no=123">테마명</a>
    """
    results = []
    for a in soup.find_all("a", href=True):
        href: str = a["href"]
        if "type=theme&no=" not in href:
            continue
        no = href.split("no=")[-1].strip()
        name = a.get_text(strip=True)
        if no.isdigit() and name:
            results.append((no, name))
    return results


def _has_next_page(soup: BeautifulSoup, current_page: int) -> bool:
    """다음 페이지가 있는지 확인."""
    for a in soup.find_all("a", href=True):
        href: str = a["href"]
        if f"page={current_page + 1}" in href:
            return True
    return False


def fetch_all_themes(max_pages: int = 20) -> list[tuple[str, str]]:
    """전체 테마 목록 크롤링.

    Returns:
        [(theme_id, theme_name), ...] 중복 제거.
    """
    seen: set[str] = set()
    results: list[tuple[str, str]] = []

    for page in range(1, max_pages + 1):
        logger.info(f"테마 목록 {page}페이지 크롤링 중...")
        try:
            soup = _get(_THEME_LIST_URL, params={"page": page})
        except requests.RequestException as e:
            logger.warning(f"테마 목록 {page}페이지 요청 실패: {e}")
            break

        page_themes = _parse_theme_list_page(soup)
        new_this_page = 0
        for theme_id, theme_name in page_themes:
            if theme_id not in seen:
                seen.add(theme_id)
                results.append((theme_id, theme_name))
                new_this_page += 1

        if new_this_page == 0:
            # 더 이상 새 테마 없으면 종료
            break
        if not _has_next_page(soup, page):
            break
        time.sleep(_REQUEST_INTERVAL_SEC)

    logger.info(f"전체 테마 수: {len(results)}")
    return results


def _parse_theme_detail(soup: BeautifulSoup) -> list[str]:
    """테마 상세 페이지에서 종목 코드 목록 추출.

    HTML 패턴:
        <a href="/item/main.naver?code=075180">제룡전기</a>
    """
    codes = []
    for a in soup.find_all("a", href=True):
        href: str = a["href"]
        if "/item/main.naver?code=" in href:
            code = href.split("code=")[-1].strip()[:6]
            if code.isdigit() and len(code) == 6:
                codes.append(code)
    return list(dict.fromkeys(codes))  # 중복 제거, 순서 유지


def fetch_theme_stocks(theme_id: str, theme_name: str) -> list[str]:
    """단일 테마의 구성 종목 코드 목록 크롤링.

    Returns:
        종목 코드 리스트. 실패 시 빈 리스트.
    """
    try:
        soup = _get(_THEME_DETAIL_URL, params={"type": "theme", "no": theme_id})
    except requests.RequestException as e:
        logger.warning(f"테마 [{theme_name}({theme_id})] 종목 조회 실패: {e}")
        return []
    codes = _parse_theme_detail(soup)
    logger.debug(f"테마 [{theme_name}] 종목 수: {len(codes)}")
    return codes


def crawl_all(
    crawled_at: date | None = None,
    interval_sec: float = _REQUEST_INTERVAL_SEC,
    max_theme_pages: int = 20,
) -> list[dict]:
    """전체 테마 × 종목 매핑 크롤링.

    Args:
        crawled_at: 기록할 날짜. None이면 오늘.
        interval_sec: 테마별 요청 간격(초).
        max_theme_pages: 테마 목록 최대 페이지 수.

    Returns:
        [{"code": "075180", "theme": "전기/전선", "crawled_at": date}, ...]
    """
    from datetime import date as date_cls
    if crawled_at is None:
        crawled_at = date_cls.today()

    themes = fetch_all_themes(max_pages=max_theme_pages)
    records: list[dict] = []

    for i, (theme_id, theme_name) in enumerate(themes, 1):
        codes = fetch_theme_stocks(theme_id, theme_name)
        for code in codes:
            records.append({"code": code, "theme": theme_name, "crawled_at": crawled_at})
        if i < len(themes):
            time.sleep(interval_sec)

    logger.info(f"크롤링 완료: {len(themes)}개 테마, {len(records)}개 (종목, 테마) 쌍")
    return records
