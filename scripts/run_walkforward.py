"""검증 루프 일일 실행 — clean days 누적으로 walk-forward 평가 + 채택 게이트 판정.

매일(또는 새 거래일 적재 후) 돌리면, 데이터 쌓일수록 자동으로 평가가 정교해진다.
채택은 OOS 게이트가 결정 (자동 매매/적용 X — 제안까지).

사용: python -m scripts.run_walkforward
"""
from __future__ import annotations

import json

from src.research.walkforward import run_and_record


def main() -> int:
    r = run_and_record()
    print("=" * 90)
    print(f"검증 루프 — clean days {r['n_days']}개: {r.get('days')}")
    print("=" * 90)
    if r["status"] == "insufficient_days":
        print("거래일 2개 미만 — walk-forward 불가. 데이터 더 쌓이면 자동 평가됨.")
        return 0
    wf = r["walk_forward"]
    print(f"walk-forward OOS: {wf['oos_days']}폴드, 선택 config 누적 {wf['sel_trades']}거래")
    print(f"  선택 전략 OOS net : {wf['sel_net']}%")
    print(f"  baseline  OOS net : {wf['baseline_net']}%")
    print("\n폴드별 (train→test):")
    for f in wf["folds"]:
        print(f"  …~{f['test_day']}: 선택={f['chosen']:<20} test_net={f['test_net']}% (n={f['test_n']})  "
              f"vs base {f['baseline_net']}%(n={f['baseline_n']})")
    v = r["verdict"]
    print("\n" + "=" * 90)
    print(f"채택 판정: {'✅ 채택 제안' if v['adopt'] else '⛔ 보류'}")
    for reason in v["reasons"]:
        print(f"  - {reason}")
    print(f"\n레지스트리: {r['registry']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
