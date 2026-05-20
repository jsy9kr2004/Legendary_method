"""src.scalping.paper_trade (ritual 2 자동화) 단위 테스트.

docs/plan.md "Buy.Score/Exit.Triggers 가중치 검증 ritual" 참조.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.scalping.paper_trade import (
    PaperTradeRecord,
    compute_summary,
    load_records,
    record_decision,
    record_open_result,
)


# ── record_decision / load_records ───────────────────────────────────────────


def test_record_and_load_roundtrip(tmp_path: Path):
    """저장 후 로드 — record 그대로 복원."""
    r = PaperTradeRecord(
        code="091340", name="유아이엘",
        grade="STRONG", score=6.5,
        reasons=["+1 회전율 10위내", "+2 VP 142"],
        decision_price=91300.0,
        decision_time="2026-05-14T14:50:00+09:00",
    )
    record_decision("2026-05-14", [r], tmp_path)
    loaded = load_records(tmp_path)
    assert len(loaded) == 1
    assert loaded[0].code == "091340"
    assert loaded[0].grade == "STRONG"
    assert loaded[0].score == 6.5
    assert loaded[0].reasons == ["+1 회전율 10위내", "+2 VP 142"]
    assert loaded[0].decision_price == 91300.0


def test_record_decision_overwrites_same_date(tmp_path: Path):
    """같은 날짜 재실행 — 기존 덮어쓰기 (재실행 안전)."""
    r1 = PaperTradeRecord(code="A", grade="STRONG", score=5.0)
    record_decision("2026-05-14", [r1], tmp_path)
    r2 = PaperTradeRecord(code="B", grade="WATCH", score=3.0)
    record_decision("2026-05-14", [r2], tmp_path)
    loaded = load_records(tmp_path)
    codes = [r.code for r in loaded]
    assert codes == ["B"]


def test_load_records_date_filter(tmp_path: Path):
    """date_from / date_to 필터."""
    record_decision("2026-05-12", [PaperTradeRecord(code="X")], tmp_path)
    record_decision("2026-05-13", [PaperTradeRecord(code="Y")], tmp_path)
    record_decision("2026-05-14", [PaperTradeRecord(code="Z")], tmp_path)
    loaded = load_records(tmp_path, date_from="2026-05-13", date_to="2026-05-13")
    assert [r.code for r in loaded] == ["Y"]


def test_load_records_empty_dir(tmp_path: Path):
    assert load_records(tmp_path) == []


# ── record_open_result ───────────────────────────────────────────────────────


def test_open_result_updates_gap_and_morning_return(tmp_path: Path):
    r = PaperTradeRecord(
        code="091340", grade="STRONG", score=6.5,
        decision_price=100_000.0,
    )
    record_decision("2026-05-14", [r], tmp_path)
    ok = record_open_result(
        "2026-05-14", "091340", data_dir=tmp_path,
        open_price=103_000.0,        # +3% 갭
        intraday_high=110_000.0,     # 종가 대비 +10% 오전 고가
        jongbae_exit_action="sell_all",
    )
    assert ok is True
    loaded = load_records(tmp_path)
    assert len(loaded) == 1
    rec = loaded[0]
    assert rec.open_price == 103_000.0
    assert rec.open_gap_pct == pytest.approx(3.0, rel=1e-6)
    assert rec.morning_return_pct == pytest.approx(10.0, rel=1e-6)
    assert rec.jongbae_exit_action == "sell_all"


def test_open_result_missing_code_returns_false(tmp_path: Path):
    record_decision("2026-05-14", [PaperTradeRecord(code="A")], tmp_path)
    ok = record_open_result(
        "2026-05-14", "B", data_dir=tmp_path, open_price=100.0,
    )
    assert ok is False


def test_open_result_missing_file_returns_false(tmp_path: Path):
    ok = record_open_result(
        "2099-01-01", "A", data_dir=tmp_path, open_price=100.0,
    )
    assert ok is False


def test_open_result_partial_update_preserves_other_fields(tmp_path: Path):
    """intraday_high 만 업데이트 — 이전 open_price 보존."""
    r = PaperTradeRecord(code="A", decision_price=100.0)
    record_decision("2026-05-14", [r], tmp_path)
    record_open_result("2026-05-14", "A", data_dir=tmp_path, open_price=105.0)
    record_open_result("2026-05-14", "A", data_dir=tmp_path, intraday_high=120.0)
    loaded = load_records(tmp_path)
    assert loaded[0].open_price == 105.0
    assert loaded[0].morning_return_pct == pytest.approx(20.0)


# ── compute_summary ──────────────────────────────────────────────────────────


def test_summary_counts(tmp_path: Path):
    records = [
        PaperTradeRecord(code="A", grade="STRONG", score=5.0),
        PaperTradeRecord(code="B", grade="STRONG", score=6.0),
        PaperTradeRecord(code="C", grade="WATCH", score=3.0),
    ]
    s = compute_summary(records)
    assert s["n_total"] == 3
    assert s["n_strong"] == 2
    assert s["n_watch"] == 1
    assert s["n_with_result"] == 0


def test_summary_avg_morning_return_strong_only():
    records = [
        PaperTradeRecord(code="A", grade="STRONG", score=5.0, morning_return_pct=10.0),
        PaperTradeRecord(code="B", grade="STRONG", score=6.0, morning_return_pct=20.0),
        PaperTradeRecord(code="C", grade="WATCH", score=3.0, morning_return_pct=5.0),
    ]
    s = compute_summary(records)
    assert s["avg_morning_return_strong_pct"] == pytest.approx(15.0)
    assert s["avg_morning_return_watch_pct"] == pytest.approx(5.0)


def test_summary_spearman_perfect_positive():
    """점수와 수익률이 같은 순서면 ρ = 1.0."""
    records = [
        PaperTradeRecord(code="A", score=1.0, morning_return_pct=1.0),
        PaperTradeRecord(code="B", score=2.0, morning_return_pct=5.0),
        PaperTradeRecord(code="C", score=3.0, morning_return_pct=10.0),
        PaperTradeRecord(code="D", score=4.0, morning_return_pct=20.0),
    ]
    s = compute_summary(records)
    assert s["spearman_score_vs_morning_return"] == pytest.approx(1.0)


def test_summary_spearman_perfect_negative():
    """역순이면 -1.0."""
    records = [
        PaperTradeRecord(code="A", score=1.0, morning_return_pct=20.0),
        PaperTradeRecord(code="B", score=2.0, morning_return_pct=10.0),
        PaperTradeRecord(code="C", score=3.0, morning_return_pct=5.0),
        PaperTradeRecord(code="D", score=4.0, morning_return_pct=1.0),
    ]
    s = compute_summary(records)
    assert s["spearman_score_vs_morning_return"] == pytest.approx(-1.0)


def test_summary_spearman_insufficient_samples():
    """표본 < 2 면 NaN."""
    records = [
        PaperTradeRecord(code="A", score=5.0, morning_return_pct=10.0),
    ]
    s = compute_summary(records)
    assert s["spearman_score_vs_morning_return"] != s["spearman_score_vs_morning_return"]


def test_summary_empty():
    s = compute_summary([])
    assert s["n_total"] == 0
    assert s["n_with_result"] == 0
    assert s["avg_morning_return_strong_pct"] != s["avg_morning_return_strong_pct"]


# ── atomic write ─────────────────────────────────────────────────────────────


def test_atomic_write_doesnt_leave_tmp_files(tmp_path: Path):
    """정상 저장 후 tmp* 파일 없음."""
    record_decision(
        "2026-05-14",
        [PaperTradeRecord(code="091340", grade="STRONG")],
        tmp_path,
    )
    pt_dir = tmp_path / "paper_trade"
    files = list(pt_dir.glob("*"))
    assert len(files) == 1
    assert files[0].name == "2026-05-14.json"
