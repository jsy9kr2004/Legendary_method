"""버전 가능한 전략 파라미터 set — 매매법별 진입(게이트+가중) / 청산.

검증 루프가 이 config 들을 walk-forward 로 평가/선택한다. 운영 적용은 OOS 게이트
통과 후 (ritual). 수치는 통설 + 데이터 검증 기반 첫 추정치 (docs §10.5).

⚠ 자동 매매 금지 정책 유지 — 이 config 는 "레포트/평가" 용. 자동 주문 X.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class StrategyConfig:
    """한 매매법의 진입+청산 파라미터.

    method/exit_kind 에 따라 관련 필드만 사용된다 (나머지는 무시).
    """
    label: str                      # 사람이 읽는 이름 (레지스트리 키)
    method: str                     # "breakout" | "pullback" | "current"
    cut: float                      # STRONG 도달 점수 컷
    exit_kind: str                  # "breakout" | "pullback" | "current"

    # ── 공통 ──
    stop_pct: float = -2.0          # 하드 손절 (사용자 baseline)
    cost_pct: float = 0.4           # 왕복 거래비용 (슬리피지 포함, 매매법별 override)
    fwd_min: int = 30               # forward 관찰/청산 윈도우 (분)
    gap_min: int = 10               # 같은 종목 재진입 최소 간격 (분)

    # ── 돌파 진입 게이트 (정의신호 AND 필수) + 부수 가산 ──
    bo_level_lo: float = -2.0       # 레벨 재테스트 존 하한 (고점거리 %)
    bo_level_hi: float = -0.3       # 상한 (정각 추격 회피)
    bo_vol_accel_min: float = 1.5   # 강한 거래량 동반 (필수)
    bo_w_candle: float = 2.0        # 장대양봉 가산
    bo_w_vp: float = 1.5            # 체결강도 가산
    bo_w_volratio: float = 1.0      # 전일대비 거래량 가산
    bo_blowoff_dr: float = 20.0     # 블로우오프 페널티 일봉 임계 (%)
    bo_blowoff_pen: float = 3.0

    # ── 눌림 진입 게이트 (급등 AND 5MA지지 AND 거래량재유입 필수) + 부수 ──
    pb_surge_min: float = 5.0       # 1차 급등 (일봉 %) 필수
    pb_ma5_lo: float = -1.5         # 5MA 지지 밴드 하한 (price_vs_ma5 %)
    pb_ma5_hi: float = 0.5          # 상한
    pb_reentry_min: float = 1.5     # 거래량 재유입 (vol_accel_1m) 필수 = 바운스 트리거
    pb_w_hammer: float = 3.0        # 망치형(아래꼬리) 가산
    pb_hammer_min: float = 0.5      # lower_wick_ratio 임계
    pb_w_divbull: float = 1.5       # 매집(Bullish 다이버전스) 가산
    pb_w_pulled: float = 1.0        # 진짜 눌림(고점서 내려옴) 가산
    pb_w_vp: float = 1.0            # 체결강도 회복 가산

    # ── 청산 ──
    # 돌파: 레벨이탈 빨리 자름 여부 + 트레일링 (느슨할수록 winner 태움)
    bo_level_lost_cut: bool = False     # True=진입가 -1% 재이탈 즉시컷 (v4: False 가 나음)
    bo_trail_arm: float = 2.0
    bo_trail_give: float = 2.0
    bo_ride_vp_death: bool = False      # True=vp_5ma<100(모멘텀死)까지 태움
    # 눌림: 목표 방식
    pb_target_mode: str = "halfway"     # "prevhigh"|"fixed"|"halfway"|"trail"
    pb_target_pct: float = 2.0          # fixed 목표 %
    pb_ma5_break: float = -1.5          # 지지 붕괴 청산

    # ── 국면(breadth) 게이트 (P2-7) ──
    # 진입 시점 시장 폭(상승종목 비율) 이 이 값 미만이면 진입 차단. 0=게이트 없음.
    # 근거: 돌파는 강세장 의존 (5/21 89%↑ 에 엣지 몰빵, 약세일엔 죽음 — docs §10.2-7).
    regime_breadth_min: float = 0.0

    def fingerprint(self) -> str:
        """파라미터 내용 해시 (label 제외) — 변경 추적/중복 방지."""
        d = {k: v for k, v in asdict(self).items() if k != "label"}
        blob = json.dumps(d, sort_keys=True)
        return hashlib.sha1(blob.encode()).hexdigest()[:12]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "StrategyConfig":
        fields = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in fields})


# ── 후보 프리셋 (docs §10.5 검증분에서 도출) ──────────────────────────────────────
# 검증 루프가 이들을 walk-forward 로 비교. live 채택은 OOS 게이트 통과 후.

def candidate_configs() -> list[StrategyConfig]:
    return [
        # 베이스라인 — 현재 단일 STRONG + 현재(단발 시그널) 청산
        StrategyConfig(label="baseline_current", method="current", cut=5.0,
                       exit_kind="current"),
        # 돌파 — 게이트 + 레벨컷제거 청산 (v4/v5 최선)
        StrategyConfig(label="breakout_gated_v5", method="breakout", cut=6.0,
                       exit_kind="breakout", bo_level_lost_cut=False,
                       bo_trail_arm=2.0, bo_trail_give=2.0,
                       cost_pct=0.4),  # 돌파=따라붙기 슬리피지 큼
        # 눌림 — 게이트 + 절반목표 (v4/v5 최선), 지정가라 비용 낮게
        StrategyConfig(label="pullback_gated_v5", method="pullback", cut=7.0,
                       exit_kind="pullback", pb_target_mode="halfway",
                       cost_pct=0.25),  # 눌림=약세매수 지정가 쉬움 → 슬리피지 작음
        # 돌파 + 국면(breadth) 게이트 — 강세장(상승종목 ≥50%)에서만 진입 (P2-7)
        StrategyConfig(label="breakout_regime_v6", method="breakout", cut=6.0,
                       exit_kind="breakout", bo_level_lost_cut=False,
                       regime_breadth_min=0.5, cost_pct=0.4),
    ]
