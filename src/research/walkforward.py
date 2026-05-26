"""Walk-forward 선택 + OOS 게이트 + 파라미터 레지스트리.

핵심 (사용자 vision = 매일 발전, 단 과적합 X):
- 매 폴드: train(과거)에서 후보 config 중 최선 선택 → test(직전 미래)에서 성과 기록.
  = 미래를 안 보고 선택. 이 walk-forward OOS 성과가 "실제로 따라갔을 때" 기대값.
- daily 재fit X — 후보 set 안에서 *선택* 만. 가중치 수치 탐색은 N 충분해진 뒤(P2).
- 채택 게이트: walk-forward OOS net 이 baseline 을 마진 이상 + 최소 표본 충족 시만 live.
- 레지스트리: 후보/메트릭/live 포인터/이력 을 JSON 으로 — "발전 기록".

운영 자동매매 X — 게이트는 "채택 제안" 까지. 실제 적용은 사람 + ritual.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytz

from src.research.backtest import clean_days, data_dir, evaluate
from src.research.strategy_config import StrategyConfig, candidate_configs

# ── 채택 게이트 임계 ──
GATE_MIN_OOS_DAYS = 10        # walk-forward test 폴드(=OOS 거래일) 최소
GATE_MIN_OOS_TRADES = 30      # 선택된 config 의 OOS 누적 거래 최소
GATE_MARGIN_NET = 0.10        # baseline 대비 OOS net 우위 마진 (%p)

KST = pytz.timezone("Asia/Seoul")


def registry_path() -> Path:
    return data_dir() / "research" / "param_registry.json"


def walk_forward(days: list[str], candidates: list[StrategyConfig],
                 baseline_label: str = "baseline_current") -> dict:
    """expanding window walk-forward 선택.

    fold k: train=days[:k] (>=1일), test=days[k]. train net 최선 config 선택 →
    그 config 의 test net 기록. baseline 도 동일 test 에서 기록.
    """
    folds = []
    sel_trades = 0
    sel_net_sum = 0.0
    base_net_sum = 0.0
    base = next((c for c in candidates if c.label == baseline_label), None)
    for k in range(1, len(days)):
        train, test = days[:k], [days[k]]
        # train 에서 후보별 net (baseline 제외하고 선택, baseline 은 비교군)
        train_eval = {c.label: evaluate(train, c) for c in candidates}
        pickable = [c for c in candidates if c.label != baseline_label
                    and train_eval[c.label]["n"] > 0 and train_eval[c.label]["net"] == train_eval[c.label]["net"]]
        if not pickable:
            chosen = base
        else:
            chosen = max(pickable, key=lambda c: train_eval[c.label]["net"])
        test_eval = evaluate(test, chosen)
        base_eval = evaluate(test, base) if base else {"net": float("nan"), "n": 0}
        folds.append({
            "train_days": train, "test_day": days[k],
            "chosen": chosen.label,
            "test_net": test_eval["net"], "test_n": test_eval["n"],
            "baseline_net": base_eval["net"], "baseline_n": base_eval["n"],
        })
        if test_eval["n"]:
            sel_trades += test_eval["n"]
            sel_net_sum += test_eval["net"] * test_eval["n"]
        if base_eval["n"]:
            base_net_sum += base_eval["net"] * base_eval["n"]
    oos_days = len(folds)
    sel_net = (sel_net_sum / sel_trades) if sel_trades else float("nan")
    base_total_n = sum(f["baseline_n"] for f in folds)
    base_net = (base_net_sum / base_total_n) if base_total_n else float("nan")
    return {
        "oos_days": oos_days, "sel_trades": sel_trades,
        "sel_net": round(sel_net, 3) if sel_net == sel_net else None,
        "baseline_net": round(base_net, 3) if base_net == base_net else None,
        "folds": folds,
    }


def adoption_verdict(wf: dict) -> dict:
    """walk-forward 결과 → 채택 가능 여부 (게이트)."""
    reasons = []
    ok = True
    if wf["oos_days"] < GATE_MIN_OOS_DAYS:
        ok = False
        reasons.append(f"OOS 거래일 부족 ({wf['oos_days']}<{GATE_MIN_OOS_DAYS})")
    if wf["sel_trades"] < GATE_MIN_OOS_TRADES:
        ok = False
        reasons.append(f"OOS 거래 부족 ({wf['sel_trades']}<{GATE_MIN_OOS_TRADES})")
    sn, bn = wf["sel_net"], wf["baseline_net"]
    if sn is None or bn is None:
        ok = False
        reasons.append("net 계산 불가")
    elif sn <= 0:
        ok = False
        reasons.append(f"OOS net 음수 ({sn:+.2f}% — 비용 못 넘음)")
    elif sn - bn < GATE_MARGIN_NET:
        ok = False
        reasons.append(f"baseline 우위 마진 부족 ({sn:+.2f} vs {bn:+.2f}, <{GATE_MARGIN_NET})")
    return {"adopt": ok, "reasons": reasons or ["게이트 통과"]}


def run_and_record() -> dict:
    """clean days 전체로 walk-forward 실행 + 레지스트리 기록 + 판정 반환."""
    days = clean_days()
    cands = candidate_configs()
    if len(days) < 2:
        result = {"status": "insufficient_days", "n_days": len(days), "days": days}
    else:
        wf = walk_forward(days, cands)
        verdict = adoption_verdict(wf)
        result = {"status": "ok", "n_days": len(days), "days": days,
                  "walk_forward": wf, "verdict": verdict,
                  "candidates": [c.label for c in cands]}

    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    reg = json.loads(path.read_text()) if path.exists() else {"live": "baseline_current", "history": []}
    reg["history"].append({"run_at": datetime.now(KST).isoformat(), **result})
    reg["history"] = reg["history"][-200:]  # 최근 200 run 만
    # 채택 제안 시 live 갱신 (자동 적용 X — 기록만; 실제 운영 전환은 사람)
    if result.get("status") == "ok" and result["verdict"]["adopt"]:
        best = max(result["walk_forward"]["folds"], key=lambda f: f.get("test_net") or -99)
        reg["proposed_live"] = best["chosen"]
    path.write_text(json.dumps(reg, ensure_ascii=False, indent=2))
    result["registry"] = str(path)
    return result
