"""매매법 분류 (돌파 / 눌림 / 추격 / 없음) — 라이브 카드 라벨 + 로깅용.

docs/trading-method-separation-discussion.md §11.1: 현재 단일 STRONG 옆에 *매매법*을
보여준다. 게이트(정의신호 AND 필수) 점수는 `src/research/strategy_config` 의 검증된
프리셋과 **단일 출처**로 묶음 — 검증 루프가 수치를 튜닝하면 여기도 같이 바뀐다.

⚠ 자동 매매 금지 — 이건 카드 표시/로깅용. 채택(운영 가중치 확정)은 OOS 게이트 통과 후.
순수 함수 (부작용 X) — 데몬/연구/테스트 어디서든 호출.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from src.research.strategy_config import candidate_configs

# ── 검증된 프리셋에서 게이트/가중 단일 출처로 가져옴 ──
_PRESETS = {c.method: c for c in candidate_configs()}
_BO = _PRESETS["breakout"]
_PB = _PRESETS["pullback"]

# 추격(chase) 정의: 고점 정각 근처 + 이미 연장(블로우오프). recent_5m 없이 daemon 가용
# 신호로 근사 — 일봉 과열 OR 1분 가속 폭발.
CHASE_NEAR_HIGH_PCT = -0.5
CHASE_BLOWOFF_DAILY_RETURN = 20.0
CHASE_BLOWOFF_ACCEL_1M = 3.0


def _ok(x: float) -> bool:
    return x is not None and not (isinstance(x, float) and math.isnan(x))


@dataclass(frozen=True)
class MethodLabel:
    setup: str            # "breakout" | "pullback" | "chase" | "none"
    score_breakout: float
    score_pullback: float
    chase_warning: bool
    reason: str


def _score_breakout(dist_high, va5, bullish, upper_wick, vp, volratio, daily_return) -> float:
    if not (_ok(dist_high) and _ok(va5)):
        return 0.0
    core = (_BO.bo_level_lo <= dist_high <= _BO.bo_level_hi) and (va5 >= _BO.bo_vol_accel_min)
    if not core:
        return 0.0
    s = 4.0
    if bullish and _ok(upper_wick) and upper_wick < 0.3:
        s += _BO.bo_w_candle
    if _ok(vp) and vp >= 110:
        s += _BO.bo_w_vp
    if _ok(volratio) and volratio >= 1.5:
        s += _BO.bo_w_volratio
    if _ok(daily_return) and daily_return >= _BO.bo_blowoff_dr:
        s -= _BO.bo_blowoff_pen
    return s


def _score_pullback(dist_high, daily_return, ma5, va1, lower_wick, vp, divbull) -> float:
    if not (_ok(daily_return) and _ok(ma5) and _ok(va1)):
        return 0.0
    core = (daily_return >= _PB.pb_surge_min) and (_PB.pb_ma5_lo <= ma5 <= _PB.pb_ma5_hi) and (va1 >= _PB.pb_reentry_min)
    if not core:
        return 0.0
    s = 4.0
    if _ok(lower_wick) and lower_wick >= _PB.pb_hammer_min:
        s += _PB.pb_w_hammer
    if divbull:
        s += _PB.pb_w_divbull
    if _ok(dist_high) and dist_high <= -1.0:
        s += _PB.pb_w_pulled
    if _ok(vp) and vp >= 100:
        s += _PB.pb_w_vp
    return s


def classify_method(
    *,
    dist_high_pct: float = float("nan"),
    daily_return_pct: float = float("nan"),
    vol_accel_5m: float = float("nan"),
    vol_accel_1m: float = float("nan"),
    vp: float = float("nan"),
    candle_bullish: bool = False,
    candle_upper_wick: float = float("nan"),
    candle_lower_wick: float = float("nan"),
    price_vs_ma5_pct: float = float("nan"),
    volume_ratio_vs_prev_day: float = float("nan"),
    divergence_bullish: bool = False,
) -> MethodLabel:
    """현재 tick 신호 → 매매법 라벨.

    돌파/눌림은 게이트(정의신호 AND) 통과 + 컷 도달 시. 둘 다면 높은 점수.
    추격 = 고점 정각 + 연장 (블로우오프) → 회피 경고.
    """
    sbo = _score_breakout(dist_high_pct, vol_accel_5m, candle_bullish,
                          candle_upper_wick, vp, volume_ratio_vs_prev_day, daily_return_pct)
    spb = _score_pullback(dist_high_pct, daily_return_pct, price_vs_ma5_pct,
                          vol_accel_1m, candle_lower_wick, vp, divergence_bullish)

    chase = (
        _ok(dist_high_pct) and dist_high_pct >= CHASE_NEAR_HIGH_PCT
        and ((_ok(daily_return_pct) and daily_return_pct >= CHASE_BLOWOFF_DAILY_RETURN)
             or (_ok(vol_accel_1m) and vol_accel_1m >= CHASE_BLOWOFF_ACCEL_1M))
    )

    if chase:
        return MethodLabel("chase", sbo, spb, True, "고점 정각+연장 = 추격 회피")
    if sbo >= _BO.cut and sbo >= spb:
        return MethodLabel("breakout", sbo, spb, False, f"돌파 게이트 통과 ({sbo:.1f})")
    if spb >= _PB.cut:
        return MethodLabel("pullback", sbo, spb, False, f"눌림 게이트 통과 ({spb:.1f})")
    return MethodLabel("none", sbo, spb, False, "셋업 없음")
