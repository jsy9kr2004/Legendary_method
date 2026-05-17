"""KIS API rate limiter.

KIS 공식: 초당 20회 (실투자), 모의는 더 보수적. 토큰 버킷 단순 구현.
단일 process / multi-thread 가정. lock 으로 시각 갱신 보호.

멀티 계정: KIS 의 rate limit 은 app_key 기준이므로 키별로 별도 limiter 인스턴스.
N 개 키를 풀로 묶으면 총 호출 capacity 가 N 배가 된다.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from src.config import KisCredential

DEFAULT_REAL_CPS = 20
DEFAULT_MOCK_CPS = 2  # 보수적. 막히면 1로.


@dataclass
class RateLimiter:
    """초당 calls_per_sec 회 제한.

    `acquire()` 는 호출 간격을 강제. 가능하면 즉시, 아니면 sleep.
    """

    calls_per_sec: int = DEFAULT_REAL_CPS
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _last: float = field(default=0.0, init=False, repr=False)

    @property
    def interval(self) -> float:
        return 1.0 / self.calls_per_sec

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last
            if elapsed < self.interval:
                time.sleep(self.interval - elapsed)
            self._last = time.monotonic()


_default_limiter: RateLimiter | None = None

# 멀티 계정용 키별 limiter 저장소. key = (api_mode, credential.cache_id)
_limiters: dict[tuple[str, str], RateLimiter] = {}
_limiters_lock = threading.Lock()


def _cps_for_mode(api_mode: str) -> int:
    return DEFAULT_REAL_CPS if api_mode == "real" else DEFAULT_MOCK_CPS


def default_limiter(api_mode: str = "real") -> RateLimiter:
    """프로세스 전역 단일 limiter (단일 키 모드용, 하위 호환).

    동일 process 안에서는 같은 인스턴스를 공유해야 rate 제한이 의미가 있다.
    멀티 키 모드에서는 `limiter_for()` 사용.
    """
    global _default_limiter
    if _default_limiter is None:
        _default_limiter = RateLimiter(calls_per_sec=_cps_for_mode(api_mode))
    return _default_limiter


def limiter_for(credential: KisCredential, api_mode: str = "real") -> RateLimiter:
    """credential 별 전용 limiter. KIS rate limit 이 키 기준이므로 키마다 독립."""
    key = (api_mode, credential.cache_id)
    with _limiters_lock:
        if key not in _limiters:
            _limiters[key] = RateLimiter(calls_per_sec=_cps_for_mode(api_mode))
        return _limiters[key]


def reset_default_limiter() -> None:
    """테스트 격리용. 단일/멀티 limiter 모두 리셋."""
    global _default_limiter
    _default_limiter = None
    with _limiters_lock:
        _limiters.clear()
