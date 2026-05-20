"""src.scalping.score.vp (Buy.VP) 단위 테스트."""
from __future__ import annotations

from datetime import datetime, timedelta

from src.scalping.score.vp import (
    VPSeries,
    crossed_below_balanced,
    is_vp_strong,
    is_vp_weak,
)


def _ts(t: int) -> datetime:
    """초 단위 timestamp."""
    return datetime(2026, 5, 13, 10, 0, 0) + timedelta(seconds=t)


# ── VPSeries ──────────────────────────────────────────────────────────────────


def test_vp_series_latest_empty():
    s = VPSeries()
    v = s.latest()
    assert v != v  # NaN


def test_vp_series_push_and_latest():
    s = VPSeries()
    s.push(_ts(0), 120.0)
    s.push(_ts(1), 130.0)
    assert s.latest() == 130.0


def test_vp_series_push_ignores_nan():
    s = VPSeries()
    s.push(_ts(0), float("nan"))
    s.push(_ts(1), None)  # type: ignore[arg-type]
    s.push(_ts(2), 100.0)
    assert s.latest() == 100.0


def test_vp_series_ma_warmup_returns_nan():
    """샘플이 windows 50% 미만이면 NaN — 워밍업 미달."""
    s = VPSeries()
    # 5MA = 300초 = 5분 윈도우. 30초만 채우면 워밍업 부족.
    for i in range(30):
        s.push(_ts(i), 110.0)
    # 5분 안에 있는 샘플은 30개, 50% 임계는 충분히 큼 → NaN
    val = s.ma_5(_ts(30))
    assert val != val  # NaN (워밍업 미달)


def test_vp_series_ma_basic():
    s = VPSeries()
    # 300초 동안 1초당 1샘플
    for i in range(310):
        s.push(_ts(i), 120.0)
    # 5MA 윈도우 안에 ~300개 샘플 → 평균 120
    val = s.ma_5(_ts(310))
    assert abs(val - 120.0) < 0.01


def test_vp_series_ma_changing_values():
    s = VPSeries()
    for i in range(310):
        # 마지막 60초만 80, 나머지 250초 = 120
        v = 80.0 if i >= 250 else 120.0
        s.push(_ts(i), v)
    val = s.ma_5(_ts(310))
    # 5MA = 평균(120 × 240 + 80 × 60) / 300 = 112
    assert 110.0 <= val <= 114.0


# ── 임계 판정 ─────────────────────────────────────────────────────────────────


def test_is_vp_strong_true():
    assert is_vp_strong(120.0, 105.0) is True


def test_is_vp_strong_just_balanced():
    """경계: VP_5MA = 100 은 FALSE (strict >)."""
    assert is_vp_strong(120.0, 100.0) is False


def test_is_vp_strong_below_110():
    assert is_vp_strong(110.0, 105.0) is False


def test_is_vp_strong_nan_safe():
    assert is_vp_strong(float("nan"), 105.0) is False
    assert is_vp_strong(120.0, float("nan")) is False


def test_is_vp_weak_true():
    assert is_vp_weak(95.0) is True


def test_is_vp_weak_at_balanced():
    """경계: VP = 100 은 FALSE (strict <)."""
    assert is_vp_weak(100.0) is False


def test_is_vp_weak_nan_safe():
    assert is_vp_weak(float("nan")) is False


def test_crossed_below_balanced_true():
    assert crossed_below_balanced(105.0, 98.0) is True


def test_crossed_below_balanced_no_cross():
    """이전부터 100 아래였으면 cross 아님."""
    assert crossed_below_balanced(95.0, 90.0) is False


def test_crossed_below_balanced_recovery():
    """100 이상에서 그대로 → cross 아님."""
    assert crossed_below_balanced(105.0, 110.0) is False


def test_crossed_below_balanced_nan_safe():
    assert crossed_below_balanced(float("nan"), 95.0) is False
    assert crossed_below_balanced(105.0, float("nan")) is False
