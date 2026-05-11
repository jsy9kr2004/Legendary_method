"""src.data.wics_crawler / storage.read_wics_sectors 단위 테스트.

httpx 호출은 mock — 실제 wiseindex.com 접속 X.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from src.data import wics_crawler as wc
from src.data.storage import (
    read_wics_sectors,
    sector_for_code,
    wics_is_fresh,
    wics_last_crawled,
    write_wics_sectors,
)


# ── fetch_wics_sectors ───────────────────────────────────────────────────────


def test_fetch_wics_sectors_collects_all_codes():
    """10개 섹터 모두 호출됨."""
    def fake_one(client, sec_cd, dt):
        return [
            {"CMP_CD": f"{sec_cd[1:]}0001", "CMP_KOR": f"{sec_cd}_종목1"},
            {"CMP_CD": f"{sec_cd[1:]}0002", "CMP_KOR": f"{sec_cd}_종목2"},
        ]

    with patch.object(wc, "_fetch_one_sector", side_effect=fake_one):
        with patch("src.data.wics_crawler.time.sleep"):
            df = wc.fetch_wics_sectors(date(2026, 5, 10))

    # 10개 섹터 × 2종목 = 20행 (단 6자리 검증으로 일부 제외 가능)
    assert len(df) > 0
    sectors = set(df["sector_code"].unique())
    assert sectors == set(wc.WICS_SECTOR_CODES.keys())


def test_fetch_wics_sectors_skips_invalid_code_length():
    def fake_one(client, sec_cd, dt):
        return [
            {"CMP_CD": "12345", "CMP_KOR": "잘못된코드"},   # 5자리 → skip
            {"CMP_CD": "005930", "CMP_KOR": "삼성전자"},
        ]
    with patch.object(wc, "_fetch_one_sector", side_effect=fake_one):
        with patch("src.data.wics_crawler.time.sleep"):
            df = wc.fetch_wics_sectors(date(2026, 5, 10))
    # 6자리 코드만 통과
    assert all(len(c) == 6 for c in df["code"])


def test_fetch_wics_sectors_dedupes_codes():
    """같은 종목이 여러 섹터에 나오면 첫 등장만 보존 (이론상 1:1 매핑)."""
    def fake_one(client, sec_cd, dt):
        # 모든 섹터에서 005930 반환
        return [{"CMP_CD": "005930", "CMP_KOR": "삼성전자"}]
    with patch.object(wc, "_fetch_one_sector", side_effect=fake_one):
        with patch("src.data.wics_crawler.time.sleep"):
            df = wc.fetch_wics_sectors(date(2026, 5, 10))
    assert (df["code"] == "005930").sum() == 1


def test_fetch_wics_sectors_partial_failure_returns_partial():
    """일부 섹터 실패해도 부분 결과 반환."""
    call_count = [0]

    def fake_one(client, sec_cd, dt):
        call_count[0] += 1
        if call_count[0] <= 5:
            raise RuntimeError("test failure")
        return [{"CMP_CD": "005930", "CMP_KOR": "삼성전자"}]

    with patch.object(wc, "_fetch_one_sector", side_effect=fake_one):
        with patch("src.data.wics_crawler.time.sleep"):
            df = wc.fetch_wics_sectors(date(2026, 5, 10))
    assert len(df) >= 1


def test_fetch_wics_sectors_all_failure_empty():
    def fake_one(client, sec_cd, dt):
        raise RuntimeError("all fail")
    with patch.object(wc, "_fetch_one_sector", side_effect=fake_one):
        with patch("src.data.wics_crawler.time.sleep"):
            df = wc.fetch_wics_sectors(date(2026, 5, 10))
    assert df.empty


def test_wics_sector_codes_has_10_entries():
    assert len(wc.WICS_SECTOR_CODES) == 10
    assert "G45" in wc.WICS_SECTOR_CODES
    assert wc.WICS_SECTOR_CODES["G45"] == "정보기술"


# ── storage 헬퍼 ─────────────────────────────────────────────────────────────


def _sample_df() -> pd.DataFrame:
    return pd.DataFrame([
        {"code": "005930", "name": "삼성전자",
         "sector_code": "G45", "sector_name": "정보기술",
         "crawled_at": date(2026, 5, 10)},
        {"code": "075180", "name": "제룡전기",
         "sector_code": "G20", "sector_name": "산업재",
         "crawled_at": date(2026, 5, 10)},
    ])


def test_write_and_read_wics(tmp_path: Path):
    write_wics_sectors(_sample_df(), tmp_path)
    df = read_wics_sectors(tmp_path)
    assert len(df) == 2
    assert set(df["code"]) == {"005930", "075180"}


def test_read_wics_missing_file_returns_empty(tmp_path: Path):
    df = read_wics_sectors(tmp_path)
    assert df.empty


def test_wics_last_crawled(tmp_path: Path):
    write_wics_sectors(_sample_df(), tmp_path)
    assert wics_last_crawled(tmp_path) == date(2026, 5, 10)


def test_wics_last_crawled_no_file(tmp_path: Path):
    assert wics_last_crawled(tmp_path) is None


def test_wics_is_fresh_within_window(tmp_path: Path):
    """크롤링 날짜가 today 기준 N일 이내면 fresh."""
    from src.config import today_kst
    today = today_kst()
    df = _sample_df().assign(crawled_at=today)
    write_wics_sectors(df, tmp_path)
    assert wics_is_fresh(tmp_path, max_age_days=35) is True


def test_wics_is_fresh_returns_false_when_no_file(tmp_path: Path):
    assert wics_is_fresh(tmp_path) is False


def test_sector_for_code_returns_dict(tmp_path: Path):
    write_wics_sectors(_sample_df(), tmp_path)
    info = sector_for_code(tmp_path, "075180")
    assert info == {"sector_code": "G20", "sector_name": "산업재"}


def test_sector_for_code_unknown_returns_none(tmp_path: Path):
    write_wics_sectors(_sample_df(), tmp_path)
    assert sector_for_code(tmp_path, "999999") is None
