"""NXT(넥스트레이드) 거래가능 종목 — 레포트 표시용 (2026-05-25).

종배에서 NXT 가능 시: (a) KRX 점상한가 종목을 NXT 애프터마켓(~20:00)에서 매수,
(b) 다음날 NXT 프리장(08:00~)에서 청산 분산(09:00 동시매도 러시 회피).
**자동 주문 X — 가능 여부만 표시.**

데이터 소스: 넥스트레이드 단계별 매매가능 종목 (www.nextrade.co.kr). 출범 후 단계
확대 중(2025-03 10→110종목→...)이라 목록을 월 1회 갱신(테마/WICS 와 동일 운영).
본 모듈은 `data/meta/nxt_tradable.txt` (6자리 코드 줄단위) 로드. **파일 부재 시
None('추정') 반환** — 크롤러(nextrade.co.kr)는 별도 TODO. KIS REST 의 NXT 시세/주문
파라미터(시장구분 NX/통합, 거래소ID구분)는 레포 샘플 확인 후 v1 (현재 표시만).
"""
from __future__ import annotations

from pathlib import Path


def load_nxt_tradable(data_dir) -> set[str] | None:
    """NXT 매매가능 종목 코드 집합 로드. 파일 부재/빈 파일이면 None('추정')."""
    p = Path(data_dir) / "meta" / "nxt_tradable.txt"
    if not p.exists():
        return None
    codes: set[str] = set()
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s and s.isdigit() and len(s) == 6:
                codes.add(s)
    except OSError:
        return None
    return codes or None


def is_nxt_tradable(code: str, nxt_set: set[str] | None) -> bool | None:
    """NXT 거래 가능 여부. nxt_set None(목록 미적재)이면 None('추정')."""
    if nxt_set is None:
        return None
    return str(code).zfill(6) in nxt_set


def nxt_label(flag: bool | None) -> str:
    """카드 표시용 한 줄."""
    if flag is True:
        return "NXT: ✓ 가능 (애프터마켓 매수 / 프리장 분산청산 가능)"
    if flag is False:
        return "NXT: ✗ 불가 (KRX 09:00 시초만)"
    return "NXT: 추정 가능 (목록 미적재 — 2026 rollout 대부분 가능)"
