"""테스트 공용 fixture / 환경 설정.

pykrx가 설치되지 않은 CI/샌드박스에서도 mock patch 경로(`pykrx.stock.*`)가
유효하도록 가짜 모듈을 sys.modules 에 주입한다. 실제 pykrx가 설치돼 있으면
그대로 사용한다.
"""
from __future__ import annotations

import sys

try:
    import pykrx  # noqa: F401
except ImportError:
    from types import ModuleType
    from unittest.mock import MagicMock

    fake_pykrx = ModuleType("pykrx")
    fake_stock = MagicMock(name="pykrx.stock")
    fake_pykrx.stock = fake_stock
    sys.modules["pykrx"] = fake_pykrx
    sys.modules["pykrx.stock"] = fake_stock
