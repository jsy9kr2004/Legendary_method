"""nxt (NXT 거래가능 표시) 단위 테스트."""
from __future__ import annotations

from src.overnight.nxt import is_nxt_tradable, load_nxt_tradable, nxt_label


def test_load_absent_returns_none(tmp_path):
    assert load_nxt_tradable(tmp_path) is None


def test_load_and_membership(tmp_path):
    meta = tmp_path / "meta"
    meta.mkdir()
    (meta / "nxt_tradable.txt").write_text("033100\n006260\n# comment\nbad\n000660\n", encoding="utf-8")
    s = load_nxt_tradable(tmp_path)
    assert s == {"033100", "006260", "000660"}
    assert is_nxt_tradable("033100", s) is True
    assert is_nxt_tradable("999999", s) is False
    assert is_nxt_tradable("33100", s) is True  # zero-pad


def test_is_tradable_none_set_returns_none():
    assert is_nxt_tradable("033100", None) is None


def test_labels():
    assert "가능" in nxt_label(True)
    assert "불가" in nxt_label(False)
    assert "추정" in nxt_label(None)


def test_empty_file_returns_none(tmp_path):
    meta = tmp_path / "meta"
    meta.mkdir()
    (meta / "nxt_tradable.txt").write_text("\n# only comments\n", encoding="utf-8")
    assert load_nxt_tradable(tmp_path) is None
