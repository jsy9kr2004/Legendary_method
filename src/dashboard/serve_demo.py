"""PWA 대시보드 로컬 데모 (M7 Phase 1, round 35 multi-flag).

worker / KIS / 텔레그램 없이 FastAPI 만 띄워서 PWA UI 를 검증할 수 있는 entrypoint.
운영 worker 와 동일한 흐름: session.update_auto_leaders / update_rising_candidates
→ ensure_held_stock → prune_empty → monitored 기반 페이로드 빌드. 사용자가 PWA 에서
[→ 수동] / [× 해제] / [+ 보유] / [✕ 청산] 누른 결과가 다음 tick 에 유지/반영됨.

실행:
    python -m src.dashboard.serve_demo
    # → http://127.0.0.1:8000/

운영용은 `./go start` + DASHBOARD_PWA_ENABLED=1.
"""
from __future__ import annotations

import asyncio
import random
from typing import Any

import uvicorn

from src.config import now_kst
from src.dashboard.api import create_app
from src.dashboard.render import build_trigger_lines
from src.dashboard.state import MonitoredStock, MonitoringSession


# 데모 종목 풀 — 운영의 update_auto_leaders 입력 흉내 (2026-05-29 단저단고 surface).
# leaders + candidates 합쳐서 update_auto_leaders 에 전달.
DEMO_AUTO_LEADERS = [
    {"code": "091340", "name": "대한광통신",
     "themes": ["AI데이터센터"], "sector_role": "leader",
     "surface_sector_name": "AI데이터센터"},
    {"code": "075180", "name": "제룡전기",
     "themes": ["전기/전선"], "sector_role": "leader",
     "surface_sector_name": "전기/전선"},
    {"code": "012200", "name": "계양전기",
     "themes": ["AI"], "sector_role": "candidate",
     "surface_sector_name": "AI데이터센터"},
]
# LEGACY 부상 후보 풀 — LEGACY_RISING_FUNNEL=1 시 demo 에서도 surface (back-out 검증용).
DEMO_RISING_CANDIDATES: list[dict] = []
# 데모 종목별 base_price 사전 — 페이로드 가격 시뮬레이션
BASE_PRICES = {
    "091340": 91300, "075180": 91300, "012200": 8920,
    # 사용자가 수동 추가할 만한 종목 fallback
    "005930": 79000, "000660": 165000,
}


def _build_demo_payload(monitored: MonitoredStock, holding: Any = None) -> dict:
    """monitored entry 를 받아 데모 페이로드 빌드. 운영 build_monitor_payload 의 mock 버전."""
    from src.dashboard.render import build_monitor_payload

    code = monitored.code
    base_price = BASE_PRICES.get(code, 50000)
    drift = random.uniform(-0.5, 0.5)
    price = int(base_price * (1.0 + drift / 100.0))

    vp_5ma_demo = round(random.uniform(80, 160), 0)
    vp_1ma_demo = round(random.uniform(80, 160), 0)
    accel_1m_demo = round(random.uniform(0.2, 4.0), 1)
    trigger_states = {
        "E1_vp_below_100": vp_5ma_demo < 100,
        "E2_bearish_divergence": False,
        "E3_vol_drain": accel_1m_demo < 0.5,
        "E4_bearish_candle": False,
    }
    if holding is not None:
        trigger_states["E5_vi_failure"] = False

    snapshot_row = {
        "price": price,
        "prev_close": base_price,
        "daily_return": round(drift + (30.0 if monitored.is_auto else 15.0), 2),
        "is_limit_up": monitored.is_auto,
        "turnover": round(random.uniform(5, 20), 1),
        "trading_value": random.randint(50, 1500) * 1_000_000_000,
        "rank": random.randint(1, 30),
    }
    ccnl = {
        "ccnl_strength": round(random.uniform(80, 160), 0),
        "buy_ratio": round(random.uniform(-30, 60), 1),
    }
    asking = {
        "bid_total_volume": random.randint(50_000, 500_000),
        "ask_total_volume": random.randint(50_000, 500_000),
        "bid_ask_ratio": round(random.uniform(0.5, 7.0), 1),
        "bid1_price": price - 100, "bid1_volume": 850,
        "ask1_price": price, "ask1_volume": 120,
    }
    # round 36: 수급 mock — KIS inquire-investor 의 시나리오. 외인/기관/프로그램
    # 양수/음수 랜덤. 운영 fetcher 가 채우는 키 구조와 동일.
    foreign_q = random.randint(-50_000, 100_000)
    inst_q = random.randint(-30_000, 60_000)
    program_q = random.randint(-20_000, 80_000)
    investor = {
        "foreign_net_buy": foreign_q,
        "institution_net_buy": inst_q,
        "individual_net_buy": -(foreign_q + inst_q),
        "program_net_buy": program_q,
        "foreign_net_buy_value": foreign_q * price,
        "institution_net_buy_value": inst_q * price,
    }
    # round 36 후속: 수급 Δ mock — KIS 갱신 주기(추정 5분)를 흉내내기보다
    # "라인 표시 검증" 목적이라 매 tick random 값. 실 운영에선 worker 가
    # session.update_investor_delta 로 누적값 변화 추적 → 자연스러운 elapsed.
    investor_delta = {
        "foreign_value": random.randint(-300_000_000, 500_000_000),
        "institution_value": random.randint(-200_000_000, 300_000_000),
        "program_qty": random.randint(-20_000, 30_000),
        "elapsed_sec": random.randint(15, 280),
    }

    # 단저단고 v10b mock — 운영 worker.analyze_minute_bars 가 채우는 필드.
    # build_monitor_payload 가 monitored.mr_* 를 읽으므로 여기서 세팅.
    mr_score = round(random.uniform(-1.0, 3.0), 1)
    if mr_score >= 2.0:
        monitored.mr_grade = "STRONG"
    elif mr_score >= 1.0:
        monitored.mr_grade = "WATCH"
    else:
        monitored.mr_grade = "NEUTRAL"
    monitored.mr_score = mr_score
    sig_b = (mr_score >= 2.0 and random.random() < 0.3)
    sig_s = (mr_score < 0 and random.random() < 0.2)
    monitored.mr_sigB = sig_b
    monitored.mr_sigS = sig_s
    monitored.mr_reason = "atr_low +1.0 / at_support +0.6 / touch_high +0.4" if mr_score >= 1.0 else None
    # 단저단고 히스토리 mock — sigB/sigS 발화 시 push.
    if sig_b:
        monitored.push_mr_event(now_kst(), "단저", mr_score, monitored.mr_reason)
    if sig_s:
        monitored.push_mr_event(now_kst(), "단고", mr_score, monitored.mr_reason)

    return build_monitor_payload(
        monitored=monitored,
        snapshot_row=snapshot_row,
        accel_ratio=round(random.uniform(0.5, 6.0), 1),
        recent_bar_value=5_000_000_000,
        ccnl=ccnl,
        asking=asking,
        investor=investor,
        investor_delta=investor_delta,
        now=now_kst(),
        accel_ratio_1m=accel_1m_demo,
        last_bar_value=1_000_000_000,
        vp_1ma=vp_1ma_demo,
        vp_5ma=vp_5ma_demo,
        holding=holding,
        trigger_states=trigger_states,
        divergence=None,
    )


async def _demo_tick_loop(session: MonitoringSession) -> None:
    """1초마다 데모 페이로드 갱신. 운영 worker.dashboard_tick 흐름과 동일.

    1) update_auto_leaders / update_rising_candidates — flag 갱신
    2) holdings.json load → 보유 종목 surface
    3) prune_empty — flag 없고 보유 없으면 monitored 제거
    4) monitored 의 모든 종목 페이로드 빌드 → last_payloads 갱신
    5) last_prices 동기화 (가격 자동 보충용)
    """
    from src.scalping.exit.triggers import load_holdings

    while True:
        now = now_kst()
        try:
            holdings = load_holdings()
        except Exception:  # noqa: BLE001
            holdings = {}

        # 운영 worker 흐름 흉내
        session.update_auto_leaders(DEMO_AUTO_LEADERS, now)
        session.update_rising_candidates(DEMO_RISING_CANDIDATES, now)
        for h_code in holdings.keys():
            if h_code not in session.monitored:
                h_name = BASE_PRICES.get(h_code) and h_code  # name 알 수 없으면 code
                session.ensure_held_stock(h_code, h_name or h_code, now)
        session.prune_empty(set(holdings.keys()))

        # 모든 monitored 종목 페이로드 빌드
        for code, m in list(session.monitored.items()):
            payload = _build_demo_payload(m, holding=holdings.get(code))
            session.last_payloads[code] = payload
            cur = (payload.get("price") or {}).get("current")
            if cur:
                session.last_prices[code] = float(cur)

        # last_payloads 정리 — monitored 에서 빠진 종목 페이로드도 제거
        for stale in list(session.last_payloads.keys()):
            if stale not in session.monitored:
                session.last_payloads.pop(stale, None)

        session.last_payload_ts = now
        await asyncio.sleep(1.0)


def main() -> None:
    session = MonitoringSession()
    session.paused = False
    app = create_app(session, broadcast_interval_sec=0.5)

    @app.on_event("startup")
    async def _start_demo() -> None:
        asyncio.create_task(_demo_tick_loop(session))

    print("PWA 데모 — http://127.0.0.1:8000/")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info", access_log=False)


if __name__ == "__main__":
    main()
