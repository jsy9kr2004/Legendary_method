"""통합 파이프라인 테스트 — demo 모드로 실제 KIS API 없이 E2E 검증."""
from __future__ import annotations

from datetime import date

import pytest

from src.pipeline import run_pipeline


TARGET_DATE = date(2025, 5, 4)  # 제룡전기 상한가 사례


# ── E2E: demo 모드 실행 ───────────────────────────────────────────────────────

def test_pipeline_demo_returns_string(tmp_path):
    """demo 모드가 에러 없이 문자열 레포트를 반환한다."""
    report = run_pipeline(TARGET_DATE, demo=True, data_dir=tmp_path)
    assert isinstance(report, str)
    assert len(report) > 0


def test_pipeline_demo_contains_date(tmp_path):
    """레포트에 기준 날짜가 포함돼야 한다."""
    report = run_pipeline(TARGET_DATE, demo=True, data_dir=tmp_path)
    assert "2025-05-04" in report


def test_pipeline_demo_contains_leading_theme(tmp_path):
    """전기/전선 테마가 주도테마로 식별돼야 한다 (5종목이 해당 테마)."""
    report = run_pipeline(TARGET_DATE, demo=True, data_dir=tmp_path)
    assert "전기/전선" in report


def test_pipeline_demo_contains_jellyung(tmp_path):
    """제룡전기(075180)가 종배 후보로 레포트에 나와야 한다."""
    report = run_pipeline(TARGET_DATE, demo=True, data_dir=tmp_path)
    # 종목명 또는 코드 중 하나라도 포함
    assert "제룡전기" in report or "075180" in report


def test_pipeline_demo_contains_historical_stats(tmp_path):
    """Historical Layer 통계가 레포트에 포함돼야 한다."""
    report = run_pipeline(TARGET_DATE, demo=True, data_dir=tmp_path)
    # Layer 1 이상의 통계가 반드시 나온다
    assert "Layer" in report


def test_pipeline_demo_contains_sizing(tmp_path):
    """사이징 섹션 (Kelly / Sharpe / Equal) 이 포함돼야 한다."""
    report = run_pipeline(TARGET_DATE, demo=True, data_dir=tmp_path)
    assert "Kelly" in report or "Sharpe" in report or "균등" in report


def test_pipeline_demo_save(tmp_path):
    """--save 옵션 시 파일이 생성돼야 한다."""
    run_pipeline(TARGET_DATE, snapshot_time="14:50", demo=True,
                 data_dir=tmp_path, save=True)
    reports_dir = tmp_path / "reports" / "2025-05-04"
    md_files = list(reports_dir.glob("*.md")) if reports_dir.exists() else []
    assert len(md_files) == 1, f"reports dir: {list(reports_dir.iterdir()) if reports_dir.exists() else 'missing'}"


def test_pipeline_empty_snapshot_returns_warning(tmp_path):
    """스냅샷이 없는 날짜는 경고 메시지를 반환한다 (demo=False, 파일 없음)."""
    report = run_pipeline(date(2020, 1, 2), demo=False, data_dir=tmp_path)
    assert "스냅샷" in report or "⚠" in report


# ── 단위: demo fixture 검증 ──────────────────────────────────────────────────

def test_demo_snapshot_has_limit_up():
    """demo 스냅샷에 상한가 종목(제룡전기)이 포함돼야 한다."""
    from src.demo_fixtures import make_snapshot
    snap = make_snapshot(TARGET_DATE)
    jellyung = snap[snap["code"] == "075180"]
    assert not jellyung.empty
    assert jellyung.iloc[0]["is_limit_up"] == True


def test_demo_snapshot_columns():
    """demo 스냅샷이 파이프라인에 필요한 컬럼을 갖춰야 한다."""
    from src.demo_fixtures import make_snapshot
    required = {"code", "price", "prev_close", "daily_return",
                "intraday_high", "intraday_low", "volume", "trading_value", "is_limit_up"}
    snap = make_snapshot(TARGET_DATE)
    assert required.issubset(set(snap.columns))


def test_demo_snapshot_intraday_low_present():
    """H1: intraday_low 가 0보다 크고 intraday_high 보다 작아야 한다."""
    from src.demo_fixtures import make_snapshot
    snap = make_snapshot(TARGET_DATE)
    for _, row in snap.iterrows():
        assert row["intraday_low"] > 0, f"{row['code']}: low={row['intraday_low']}"
        assert row["intraday_low"] <= row["intraday_high"]


def test_demo_daily_ohlcv_shape():
    """demo 일봉이 10종목 × ~260 거래일 이상을 가져야 한다."""
    from src.demo_fixtures import make_daily_ohlcv
    df = make_daily_ohlcv(TARGET_DATE, lookback_days=260)
    n_codes = df["code"].nunique()
    assert n_codes == 10
    assert len(df) >= 10 * 200  # 적어도 200 거래일치


def test_demo_daily_ohlcv_jellyung_limit_up():
    """demo 일봉에서 제룡전기의 target_date 종가가 +30% 급등이어야 한다."""
    from src.demo_fixtures import make_daily_ohlcv
    df = make_daily_ohlcv(TARGET_DATE, lookback_days=260)
    jd = df[(df["code"] == "075180") & (df["date"] == TARGET_DATE)]
    assert not jd.empty
    row = jd.iloc[0]
    # 전날 종가 대비 +25% 이상 (정확한 +30% 는 시드/랜덤워크 조합으로 약간 차이 가능)
    prev = df[(df["code"] == "075180") & (df["date"] < TARGET_DATE)].sort_values("date")
    if not prev.empty:
        prev_close = prev.iloc[-1]["close"]
        ret = (row["close"] - prev_close) / prev_close
        assert ret >= 0.25, f"expected >=25% return, got {ret:.2%}"


def test_demo_theme_mapping_contains_jellyung():
    """demo 테마 매핑에 제룡전기 코드가 포함돼야 한다."""
    from src.demo_fixtures import make_theme_mapping
    themes = make_theme_mapping(TARGET_DATE)
    jellyung_themes = themes[themes["code"] == "075180"]["theme"].tolist()
    assert "전기/전선" in jellyung_themes
    assert "원자력" in jellyung_themes
