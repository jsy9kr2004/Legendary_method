"""src.kis.rate_limit 테스트. monotonic 측정으로 강제 sleep 검증."""
from __future__ import annotations

import time

from src.kis.rate_limit import RateLimiter, default_limiter, reset_default_limiter


def test_first_acquire_no_wait():
    rl = RateLimiter(calls_per_sec=20)
    start = time.monotonic()
    rl.acquire()
    elapsed = time.monotonic() - start
    assert elapsed < 0.05


def test_back_to_back_acquires_enforce_interval():
    rl = RateLimiter(calls_per_sec=20)  # 0.05s 간격
    rl.acquire()
    start = time.monotonic()
    rl.acquire()
    elapsed = time.monotonic() - start
    assert elapsed >= 0.04  # 약간의 jitter 허용


def test_low_cps_enforces_longer_wait():
    rl = RateLimiter(calls_per_sec=2)  # 0.5s 간격
    rl.acquire()
    start = time.monotonic()
    rl.acquire()
    elapsed = time.monotonic() - start
    assert elapsed >= 0.45


def test_default_limiter_is_singleton():
    reset_default_limiter()
    a = default_limiter("mock")
    b = default_limiter("mock")
    assert a is b
    reset_default_limiter()


def test_default_limiter_real_vs_mock_cps():
    reset_default_limiter()
    real = default_limiter("real")
    assert real.calls_per_sec == 20
    reset_default_limiter()
    mock = default_limiter("mock")
    assert mock.calls_per_sec == 2
    reset_default_limiter()
