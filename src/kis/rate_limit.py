"""KIS API rate limiter.

KIS 공식: 초당 20회 (실투자), 모의는 더 보수적. 토큰 버킷 단순 구현.
단일 process / multi-thread 가정. lock 으로 시각 갱신 보호.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

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


def default_limiter(api_mode: str = "real") -> RateLimiter:
    """프로세스 전역 limiter (api_mode 기준 기본값).

    동일 process 안에서는 같은 인스턴스를 공유해야 rate 제한이 의미가 있다.
    """
    global _default_limiter
    if _default_limiter is None:
        cps = DEFAULT_REAL_CPS if api_mode == "real" else DEFAULT_MOCK_CPS
        _default_limiter = RateLimiter(calls_per_sec=cps)
    return _default_limiter


def reset_default_limiter() -> None:
    """테스트 격리용."""
    global _default_limiter
    _default_limiter = None
