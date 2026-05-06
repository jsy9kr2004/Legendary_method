"""src.data.theme_crawler + storage 테마 관련 테스트. requests는 mock."""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.data.storage import (
    codes_for_theme,
    themes_are_fresh,
    themes_for_code,
    themes_last_crawled,
    write_naver_themes,
)
from src.data.theme_crawler import (
    _parse_theme_detail,
    _parse_theme_list_page,
    crawl_all,
    fetch_all_themes,
    fetch_theme_stocks,
)

try:
    from bs4 import BeautifulSoup
except ImportError:
    pytest.skip("beautifulsoup4 없음", allow_module_level=True)


# ── HTML 파싱 유닛 테스트 ────────────────────────────────────────────────────

_THEME_LIST_HTML = """
<html><body>
<a href="/sise/sise_group_detail.naver?type=theme&no=234">전기/전선</a>
<a href="/sise/sise_group_detail.naver?type=theme&no=101">원자력</a>
<a href="/item/main.naver?code=005930">삼성전자</a>
<a href="/other/page.naver">무관링크</a>
</body></html>
"""

_THEME_DETAIL_HTML = """
<html><body>
<a href="/item/main.naver?code=075180">제룡전기</a>
<a href="/item/main.naver?code=000120">CJ대한통운</a>
<a href="/item/main.naver?code=075180">제룡전기</a>  <!-- 중복 -->
<a href="/other/page.naver">무관링크</a>
</body></html>
"""


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def test_parse_theme_list_page_extracts_themes():
    soup = _soup(_THEME_LIST_HTML)
    themes = _parse_theme_list_page(soup)
    ids = [t[0] for t in themes]
    names = [t[1] for t in themes]
    assert "234" in ids
    assert "101" in ids
    assert "전기/전선" in names
    assert "원자력" in names


def test_parse_theme_list_page_ignores_non_theme_links():
    soup = _soup(_THEME_LIST_HTML)
    themes = _parse_theme_list_page(soup)
    ids = [t[0] for t in themes]
    assert "005930" not in ids  # /item/main 링크는 제외


def test_parse_theme_detail_extracts_codes():
    soup = _soup(_THEME_DETAIL_HTML)
    codes = _parse_theme_detail(soup)
    assert "075180" in codes
    assert "000120" in codes


def test_parse_theme_detail_deduplicates():
    soup = _soup(_THEME_DETAIL_HTML)
    codes = _parse_theme_detail(soup)
    assert codes.count("075180") == 1


def test_parse_theme_detail_ignores_non_stock_links():
    soup = _soup(_THEME_DETAIL_HTML)
    codes = _parse_theme_detail(soup)
    assert all(c.isdigit() and len(c) == 6 for c in codes)


# ── fetch_all_themes (mock requests) ─────────────────────────────────────────

def _mock_response(html: str) -> MagicMock:
    resp = MagicMock()
    resp.text = html
    resp.apparent_encoding = "utf-8"
    resp.raise_for_status.return_value = None
    return resp


def test_fetch_all_themes_single_page():
    with patch("src.data.theme_crawler.requests.get", return_value=_mock_response(_THEME_LIST_HTML)):
        themes = fetch_all_themes(max_pages=1)
    assert len(themes) == 2
    assert any(name == "전기/전선" for _, name in themes)


def test_fetch_all_themes_request_error():
    """요청 실패 시 빈 리스트 반환 (break on first page)."""
    import requests as req_mod
    with patch("src.data.theme_crawler.requests.get", side_effect=req_mod.ConnectionError("연결 실패")):
        themes = fetch_all_themes(max_pages=3)
    assert themes == []


# ── fetch_theme_stocks (mock requests) ───────────────────────────────────────

def test_fetch_theme_stocks_normal():
    with patch("src.data.theme_crawler.requests.get", return_value=_mock_response(_THEME_DETAIL_HTML)):
        codes = fetch_theme_stocks("234", "전기/전선")
    assert "075180" in codes
    assert "000120" in codes


def test_fetch_theme_stocks_request_error():
    import requests as req_mod
    with patch("src.data.theme_crawler.requests.get", side_effect=req_mod.ConnectionError):
        codes = fetch_theme_stocks("234", "전기/전선")
    assert codes == []


# ── crawl_all (통합, mock) ────────────────────────────────────────────────────

def test_crawl_all_returns_records():
    today = date(2026, 5, 6)
    with (
        patch("src.data.theme_crawler.fetch_all_themes", return_value=[("234", "전기/전선"), ("101", "원자력")]),
        patch("src.data.theme_crawler.fetch_theme_stocks", side_effect=lambda tid, _: ["075180"] if tid == "234" else ["005930"]),
        patch("src.data.theme_crawler.time.sleep"),
    ):
        records = crawl_all(crawled_at=today, interval_sec=0)

    assert len(records) == 2
    assert {"code": "075180", "theme": "전기/전선", "crawled_at": today} in records
    assert {"code": "005930", "theme": "원자력", "crawled_at": today} in records


def test_crawl_all_no_themes():
    with (
        patch("src.data.theme_crawler.fetch_all_themes", return_value=[]),
        patch("src.data.theme_crawler.time.sleep"),
    ):
        records = crawl_all(interval_sec=0)
    assert records == []


# ── storage helpers ──────────────────────────────────────────────────────────

def _write_sample(tmp_path, crawled_at: date):
    df = pd.DataFrame([
        {"code": "075180", "theme": "전기/전선", "crawled_at": crawled_at},
        {"code": "075180", "theme": "원자력", "crawled_at": crawled_at},
        {"code": "005930", "theme": "반도체", "crawled_at": crawled_at},
    ])
    write_naver_themes(df, tmp_path)


def test_themes_last_crawled(tmp_path):
    today = date(2026, 5, 6)
    _write_sample(tmp_path, today)
    assert themes_last_crawled(tmp_path) == today


def test_themes_last_crawled_empty(tmp_path):
    assert themes_last_crawled(tmp_path) is None


def test_themes_are_fresh_within_7_days(tmp_path):
    from src.config import today_kst
    today = today_kst()
    _write_sample(tmp_path, today - timedelta(days=6))
    assert themes_are_fresh(tmp_path, max_age_days=7) is True


def test_themes_are_fresh_exactly_7_days(tmp_path):
    from src.config import today_kst
    today = today_kst()
    _write_sample(tmp_path, today - timedelta(days=7))
    assert themes_are_fresh(tmp_path, max_age_days=7) is True


def test_themes_are_fresh_stale(tmp_path):
    from src.config import today_kst
    today = today_kst()
    _write_sample(tmp_path, today - timedelta(days=8))
    assert themes_are_fresh(tmp_path, max_age_days=7) is False


def test_themes_are_fresh_empty(tmp_path):
    assert themes_are_fresh(tmp_path) is False


def test_themes_for_code(tmp_path):
    _write_sample(tmp_path, date(2026, 5, 6))
    themes = themes_for_code(tmp_path, "075180")
    assert set(themes) == {"전기/전선", "원자력"}


def test_themes_for_code_not_found(tmp_path):
    _write_sample(tmp_path, date(2026, 5, 6))
    assert themes_for_code(tmp_path, "999999") == []


def test_codes_for_theme(tmp_path):
    _write_sample(tmp_path, date(2026, 5, 6))
    codes = codes_for_theme(tmp_path, "전기/전선")
    assert codes == ["075180"]


def test_codes_for_theme_not_found(tmp_path):
    _write_sample(tmp_path, date(2026, 5, 6))
    assert codes_for_theme(tmp_path, "존재안함") == []
