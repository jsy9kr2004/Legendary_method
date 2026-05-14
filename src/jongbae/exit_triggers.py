"""매도 트리거 + 감시/보유 상태 머신 (R15).

`docs/jongbae-strategy.md` R15 참조. 정정 이력 round 16.

핵심 정책 (정정 round 17):
    **모든 매도 트리거 = 보유 모드 카드 안 표시.** 별도 텔레그램 푸시 알림 X.
    카드 렌더러가 `TriggerEvent.text` (한 줄 사유) 와 `holding.triggers_fired`
    상태를 보고 "🔔 매도 트리거 상태" 섹션을 ❌ → ✅ 갱신.
    KIS 실주문 자동 등록 X. CLAUDE.md "자동 매매 절대 금지" 정책 유지.

상태:
    감시 모드 ←→ 보유 모드
    /buy 091340 91300       → 감시 → 보유
    /sell 091340            → 보유 → 감시
    매도 트리거 발화 후     → 사람이 청산 여부 결정 후 명시적 /sell 필요

트리거 종류 (OR, 하나라도 발동 시 푸시):
    A1. 손절 — 가격 ≤ 진입가 × 0.985
    A2. 손절 — 봉 저점 이탈
    A3. 손절 — 5분 이평 이탈
    A4. 손절 — 시간 (N분 내 +0.5% 미달)
    B1. 익절 1차 — +2% (1/3, 1회만)
    B2. 익절 2차 — +3.5% (1/3, 1회만)
    B3. 트레일링 — 고점 × 0.985 (B1 발화 후 활성)
    C1. 시그널 — VP_5MA 100 하향 돌파
    C2. 시그널 — Bearish Divergence
    C3. 시그널 — vol_accel_1m < 0.5 (2분 연속)
    C4. 시그널 — 윗꼬리 50%↑ 음봉
    C5. 시그널 — VI 발동 후 5분 내 고가 회복 X

영속화: `data/state/holdings.json` (data-infra.md 참조).
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Literal

from src.config import load_settings
from src.jongbae.candle import CandleShape, is_bearish_exit_signal
from src.jongbae.config_thresholds import (
    ENTRY_BAR_MA_MINUTES,
    EOD_CUTOFF_HH,
    EOD_CUTOFF_MM,
    STOP_LOSS_PCT,
    TAKE_PROFIT_1_PCT,
    TAKE_PROFIT_2_PCT,
    TIME_STOP_MINUTES_DEFAULT,
    TIME_STOP_REQUIRED_PROFIT_PCT,
    TRAILING_STOP_PCT,
    VI_FAILURE_WINDOW_SECONDS,
    VOL_ACCEL_1M_DRAIN,
    VOL_ACCEL_DRAIN_PERSIST_SECONDS,
)
from src.jongbae.divergence import DivergenceState
from src.jongbae.volume_power import crossed_below_balanced


class Mode(str, Enum):
    WATCH = "watch"   # 감시 모드
    HOLD = "hold"     # 보유 모드


TriggerKind = Literal[
    "A1_stop_price", "A2_stop_bar_low", "A3_stop_ma", "A4_stop_time",
    "A5_eod_ma_break",
    "B1_take_profit_1", "B2_take_profit_2", "B3_trailing",
    "C1_vp_below_100", "C2_bearish_divergence",
    "C3_vol_drain", "C4_bearish_candle", "C5_vi_failure",
]

TRIGGER_LABELS: dict[TriggerKind, str] = {
    "A1_stop_price":         "A1 가격 손절 -1.5%",
    "A2_stop_bar_low":       "A2 진입봉 저점 이탈",
    "A3_stop_ma":            "A3 5분 이평 이탈",
    "A4_stop_time":          "A4 시간 손절",
    "A5_eod_ma_break":       "A5 EOD 이평+음봉 강제",
    "B1_take_profit_1":      "B1 익절 1차 +2.0%",
    "B2_take_profit_2":      "B2 익절 2차 +3.5%",
    "B3_trailing":           "B3 트레일링 스탑",
    "C1_vp_below_100":       "C1 VP 5MA 100 하향",
    "C2_bearish_divergence": "C2 Bearish Divergence",
    "C3_vol_drain":          "C3 자금 고갈 (2분 지속)",
    "C4_bearish_candle":     "C4 윗꼬리 음봉",
    "C5_vi_failure":         "C5 VI 재상승 실패",
}

# 멱등 트리거 — 1회만 발화 (B1/B2)
ONESHOT_TRIGGERS: set[TriggerKind] = {"B1_take_profit_1", "B2_take_profit_2"}


@dataclass
class Holding:
    """보유 모드 1종목."""
    code: str
    entry_price: float
    entry_time: datetime
    entry_bar_low: float = 0.0
    time_stop_minutes: int = TIME_STOP_MINUTES_DEFAULT
    high_since_entry: float = 0.0
    triggers_fired: set[TriggerKind] = field(default_factory=set)
    # C3/C5 지속 카운터
    vol_drain_since: datetime | None = None
    vi_triggered_at: datetime | None = None

    @property
    def stop_loss_price(self) -> float:
        return self.entry_price * (1.0 + STOP_LOSS_PCT / 100.0)

    @property
    def take_profit_1_price(self) -> float:
        return self.entry_price * (1.0 + TAKE_PROFIT_1_PCT / 100.0)

    @property
    def take_profit_2_price(self) -> float:
        return self.entry_price * (1.0 + TAKE_PROFIT_2_PCT / 100.0)

    def trailing_stop_price(self) -> float:
        if self.high_since_entry <= 0:
            return 0.0
        return self.high_since_entry * (1.0 + TRAILING_STOP_PCT / 100.0)

    def pnl_pct(self, current_price: float) -> float:
        if self.entry_price <= 0:
            return float("nan")
        return (current_price - self.entry_price) / self.entry_price * 100.0


@dataclass
class TriggerEvent:
    """매도 트리거 발화 1건. 정정 round 17 후엔 카드 안 표시 전용 (별도 푸시 X).

    Attributes:
        kind: 트리거 종류 (A1~C5).
        code: 종목코드.
        text: 카드 안 한 줄 사유. 예: "A1 가격 손절 89,930 (-1.50%)".
        is_stop_loss: A* 손절선 — 카드 헤더 이모지 🛑 / 우선순위 강조.
    """
    kind: TriggerKind
    code: str
    text: str
    is_stop_loss: bool


# ── 평가 함수 (pure, 1 tick 마다 호출) ────────────────────────────────────────


def compute_c_signal_states(
    *,
    vp_5ma_prev: float | None,
    vp_5ma_now: float | None,
    divergence: DivergenceState | None,
    vol_accel_1m: float | None,
    candle: CandleShape | None,
    holding: Holding | None = None,
) -> dict[str, bool]:
    """C1~C5 시그널 발화 상태 (카드 표시용).

    감시 모드 (holding=None):
        지금 이 시점 시그널이 켜져 있는지 즉시 판정. 진입 의사결정 보조용 —
        매도 시그널이 켜진 종목은 매수 진입 회피. C5 는 VI 감지 인프라 없어
        항상 False (호출자가 감시 모드에서는 C5 행을 숨길 것).

    보유 모드 (holding 주어짐):
        holding.triggers_fired 에 이번 보유 중 한번이라도 들어간 적이 있는지.
        sticky — 한번 발화한 시그널은 청산 전까지 ✅ 유지.

    인자는 evaluate_triggers 와 동일한 시장 메트릭. 감시 모드에서는
    instantaneous 라 NaN/None 은 모두 False 로 보수적 판정.
    """
    if holding is not None:
        return {
            "C1_vp_below_100":       "C1_vp_below_100"       in holding.triggers_fired,
            "C2_bearish_divergence": "C2_bearish_divergence" in holding.triggers_fired,
            "C3_vol_drain":          "C3_vol_drain"          in holding.triggers_fired,
            "C4_bearish_candle":     "C4_bearish_candle"     in holding.triggers_fired,
            "C5_vi_failure":         "C5_vi_failure"         in holding.triggers_fired,
        }

    # 감시 모드 — 현재 시점 instantaneous.
    # C1: VP_5MA 현재값이 균형선 100 아래인지. 보유 모드의 "교차" 와 달리
    # 진입 회피용은 "지금 약한가" 가 더 직관적.
    c1 = vp_5ma_now is not None and vp_5ma_now == vp_5ma_now and vp_5ma_now < 100.0

    c2 = bool(divergence is not None and divergence.bearish)

    c3 = bool(
        vol_accel_1m is not None
        and vol_accel_1m == vol_accel_1m  # NaN guard
        and vol_accel_1m < VOL_ACCEL_1M_DRAIN
    )

    c4 = bool(candle is not None and is_bearish_exit_signal(candle))

    # C5: VI 감지 인프라 부재 — 감시 모드에서는 항상 False (render 에서 행 숨김).
    c5 = False

    return {
        "C1_vp_below_100":       c1,
        "C2_bearish_divergence": c2,
        "C3_vol_drain":          c3,
        "C4_bearish_candle":     c4,
        "C5_vi_failure":         c5,
    }


def evaluate_triggers(
    holding: Holding,
    *,
    now: datetime,
    current_price: float,
    minute_ma_5: float | None = None,
    candle: CandleShape | None = None,
    vp_5ma_prev: float | None = None,
    vp_5ma_now: float | None = None,
    divergence: DivergenceState | None = None,
    vol_accel_1m_value: float | None = None,
    vi_triggered_at: datetime | None = None,
    vi_recovered: bool = False,
) -> list[TriggerEvent]:
    """1 tick 평가 — 발화된 새 트리거 리스트 반환 (멱등 트리거는 1회만).

    Args:
        holding: 보유 상태 (in-place 갱신 — high_since_entry / triggers_fired / 카운터).
        now: 현재 시각.
        current_price: 현재가.
        minute_ma_5: 5분 이평 (A3 입력). None 이면 A3 스킵.
        candle: 직전 완성 봉 (C4 입력). None 이면 C4 스킵.
        vp_5ma_prev, vp_5ma_now: VP 5MA 직전/현재 값 (C1 입력).
        divergence: R13 다이버전스 (C2 입력).
        vol_accel_1m_value: R11 1분 가속 (C3 입력).
        vi_triggered_at: VI 발동 시각 (C5 입력).
        vi_recovered: VI 후 고가 회복 여부 (호출자가 분봉으로 판정).

    Returns:
        새로 발화된 TriggerEvent 리스트. 빈 리스트면 변화 없음.
    """
    events: list[TriggerEvent] = []

    # 고점 갱신
    if current_price > holding.high_since_entry:
        holding.high_since_entry = current_price

    pnl = holding.pnl_pct(current_price)

    def fire(kind: TriggerKind, is_stop_loss: bool, detail: str) -> None:
        """카드 안 한 줄 사유 — 별도 푸시 발송 X (round 17)."""
        if kind in ONESHOT_TRIGGERS and kind in holding.triggers_fired:
            return
        holding.triggers_fired.add(kind)
        events.append(TriggerEvent(
            kind=kind,
            code=holding.code,
            is_stop_loss=is_stop_loss,
            text=f"{TRIGGER_LABELS[kind]} — {detail}",
        ))

    # ── A: 손절 (최우선) ─────────────────────────────────────────────────────
    if current_price <= holding.stop_loss_price:
        fire(
            "A1_stop_price", True,
            f"{int(holding.stop_loss_price):,} 도달 (현 {int(current_price):,}, {pnl:+.2f}%)",
        )

    if holding.entry_bar_low > 0 and current_price < holding.entry_bar_low:
        fire(
            "A2_stop_bar_low", True,
            f"진입봉 저점 {int(holding.entry_bar_low):,} 이탈",
        )

    if minute_ma_5 is not None and minute_ma_5 > 0 and current_price < minute_ma_5:
        fire(
            "A3_stop_ma", True,
            f"5분 이평 {int(minute_ma_5):,} 이탈",
        )

    # A5 EOD 컷오프 (round 26, P1-2) — 통설: "14:45 이평선 밑 음봉이면 목숨
    # 걸고 팔아라". A3 (이평 이탈) 와 C4 (음봉) 가 시간 게이트로 합쳐진 강제
    # 청산. AND 조건이라 A3/C4 가 따로 발화될 때보다 더 강한 신호.
    if (
        (now.hour, now.minute) >= (EOD_CUTOFF_HH, EOD_CUTOFF_MM)
        and minute_ma_5 is not None and minute_ma_5 > 0
        and current_price < minute_ma_5
        and candle is not None and candle.type == "bearish"
    ):
        fire(
            "A5_eod_ma_break", True,
            f"EOD {now.hour:02d}:{now.minute:02d} 이평 {int(minute_ma_5):,} 밑 음봉",
        )

    elapsed_min = (now - holding.entry_time).total_seconds() / 60.0
    if elapsed_min >= holding.time_stop_minutes and pnl < TIME_STOP_REQUIRED_PROFIT_PCT:
        fire(
            "A4_stop_time", True,
            f"{holding.time_stop_minutes}분 경과, +{TIME_STOP_REQUIRED_PROFIT_PCT}% 미달 (현 {pnl:+.2f}%)",
        )

    # ── B: 익절 ───────────────────────────────────────────────────────────────
    if current_price >= holding.take_profit_1_price:
        fire(
            "B1_take_profit_1", False,
            f"+{TAKE_PROFIT_1_PCT}% 도달 ({int(current_price):,}) — 1/3 청산 권장",
        )

    if current_price >= holding.take_profit_2_price:
        fire(
            "B2_take_profit_2", False,
            f"+{TAKE_PROFIT_2_PCT}% 도달 ({int(current_price):,}) — 1/3 청산 권장",
        )

    # B3 트레일링 — B1 발화 후 활성
    if "B1_take_profit_1" in holding.triggers_fired:
        ts = holding.trailing_stop_price()
        if ts > 0 and current_price <= ts:
            # 트레일링은 멱등 X — 한번 발화 후 추가 하락 시 재발화 가능.
            # 동일 tick 에 중복 추가 방지 위해 events 내 dedup.
            if not any(e.kind == "B3_trailing" for e in events):
                holding.triggers_fired.add("B3_trailing")
                events.append(TriggerEvent(
                    kind="B3_trailing",
                    code=holding.code,
                    is_stop_loss=False,
                    text=(
                        f"{TRIGGER_LABELS['B3_trailing']} — "
                        f"고점 {int(holding.high_since_entry):,} × "
                        f"{1.0 + TRAILING_STOP_PCT/100.0:.3f} = {int(ts):,} "
                        f"이탈 (현 {int(current_price):,}, {pnl:+.2f}%)"
                    ),
                ))

    # ── C: 시그널 청산 ────────────────────────────────────────────────────────
    if (
        vp_5ma_prev is not None and vp_5ma_now is not None
        and crossed_below_balanced(vp_5ma_prev, vp_5ma_now)
        and "C1_vp_below_100" not in holding.triggers_fired
    ):
        holding.triggers_fired.add("C1_vp_below_100")
        events.append(TriggerEvent(
            kind="C1_vp_below_100", code=holding.code, is_stop_loss=False,
            text=f"{TRIGGER_LABELS['C1_vp_below_100']} — VP_5MA {vp_5ma_prev:.0f} → {vp_5ma_now:.0f}",
        ))

    if divergence is not None and divergence.bearish and "C2_bearish_divergence" not in holding.triggers_fired:
        holding.triggers_fired.add("C2_bearish_divergence")
        events.append(TriggerEvent(
            kind="C2_bearish_divergence", code=holding.code, is_stop_loss=False,
            text=(
                f"{TRIGGER_LABELS['C2_bearish_divergence']} — "
                f"가격 {divergence.price_change_pct:+.2f}% / VP_5MA {divergence.vp_5ma_delta:+.0f}"
            ),
        ))

    # C3 자금 고갈 (2분 지속)
    if vol_accel_1m_value is not None and vol_accel_1m_value == vol_accel_1m_value:
        if vol_accel_1m_value < VOL_ACCEL_1M_DRAIN:
            if holding.vol_drain_since is None:
                holding.vol_drain_since = now
            elif (
                (now - holding.vol_drain_since).total_seconds() >= VOL_ACCEL_DRAIN_PERSIST_SECONDS
                and "C3_vol_drain" not in holding.triggers_fired
            ):
                holding.triggers_fired.add("C3_vol_drain")
                events.append(TriggerEvent(
                    kind="C3_vol_drain", code=holding.code, is_stop_loss=False,
                    text=(
                        f"{TRIGGER_LABELS['C3_vol_drain']} — "
                        f"vol_accel_1m {vol_accel_1m_value:.2f} (임계 {VOL_ACCEL_1M_DRAIN})"
                    ),
                ))
        else:
            holding.vol_drain_since = None

    # C4 윗꼬리 음봉
    if (
        candle is not None
        and is_bearish_exit_signal(candle)
        and "C4_bearish_candle" not in holding.triggers_fired
    ):
        holding.triggers_fired.add("C4_bearish_candle")
        events.append(TriggerEvent(
            kind="C4_bearish_candle", code=holding.code, is_stop_loss=False,
            text=f"{TRIGGER_LABELS['C4_bearish_candle']} — 윗꼬리 {candle.upper_wick*100:.0f}% 음봉",
        ))

    # C5 VI 재상승 실패
    if vi_triggered_at is not None:
        holding.vi_triggered_at = vi_triggered_at
    if (
        holding.vi_triggered_at is not None
        and not vi_recovered
        and (now - holding.vi_triggered_at).total_seconds() >= VI_FAILURE_WINDOW_SECONDS
        and "C5_vi_failure" not in holding.triggers_fired
    ):
        holding.triggers_fired.add("C5_vi_failure")
        events.append(TriggerEvent(
            kind="C5_vi_failure", code=holding.code, is_stop_loss=False,
            text=(
                f"{TRIGGER_LABELS['C5_vi_failure']} — "
                f"VI {holding.vi_triggered_at.strftime('%H:%M:%S')} 후 5분 내 회복 X"
            ),
        ))

    return events


# ── 영속화 (data/state/holdings.json) ─────────────────────────────────────────


def _state_path() -> Path:
    settings = load_settings()
    base = settings.data_dir / "state"
    base.mkdir(parents=True, exist_ok=True)
    return base / "holdings.json"


def load_holdings() -> dict[str, Holding]:
    """파일에서 보유 상태 복원. 시계열/카운터는 비어 있음 (워밍업 필요)."""
    path = _state_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}

    holdings: dict[str, Holding] = {}
    for code, h in raw.items():
        try:
            holdings[code] = Holding(
                code=code,
                entry_price=float(h["entry_price"]),
                entry_time=datetime.fromisoformat(h["entry_time"]),
                entry_bar_low=float(h.get("entry_bar_low", 0.0)),
                time_stop_minutes=int(h.get("time_stop_minutes", TIME_STOP_MINUTES_DEFAULT)),
                high_since_entry=float(h.get("high_since_entry", 0.0)),
                triggers_fired=set(h.get("triggers_fired", [])),
            )
        except (KeyError, TypeError, ValueError):
            continue
    return holdings


def save_holdings(holdings: dict[str, Holding]) -> None:
    """atomic write (tmp file + rename)."""
    path = _state_path()
    payload = {
        code: {
            "entry_price": h.entry_price,
            "entry_time": h.entry_time.isoformat(),
            "entry_bar_low": h.entry_bar_low,
            "time_stop_minutes": h.time_stop_minutes,
            "high_since_entry": h.high_since_entry,
            "triggers_fired": sorted(h.triggers_fired),
        }
        for code, h in holdings.items()
    }
    fd, tmp = tempfile.mkstemp(prefix=".holdings_", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
