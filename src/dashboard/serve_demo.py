"""PWA 대시보드 로컬 데모 (M7 Phase 1).

worker / KIS / 텔레그램 없이 FastAPI 만 띄워서 PWA UI 를 검증할 수 있는
가벼운 entrypoint. mock session 에 데모 페이로드를 1초 간격으로 갱신하고,
실제 worker 처럼 매 tick `load_holdings()` 호출해서 보유 종목이 hold 모드로
전환되는 것까지 시뮬레이션.

실행:
    python -m src.dashboard.serve_demo
    # → http://127.0.0.1:8000/

운영용은 `python -m src.scheduler` + `DASHBOARD_PWA_ENABLED=1`.
"""
from __future__ import annotations

import asyncio
import random
from typing import Any

import uvicorn

from src.config import now_kst
from src.dashboard.api import create_app
from src.dashboard.state import MonitoringSession


def _build_demo_payload(
    code: str,
    name: str,
    source: str,
    base_price: int,
    *,
    holding: Any = None,
) -> dict:
    """모니터링 카드 1개에 해당하는 데모 페이로드.

    holding 인자가 있으면 source 를 'hold' 로 override 하고 holding 블록 채움
    — worker.dashboard_tick 의 보유 모드 처리와 동일한 출력 형태.
    """
    now = now_kst()
    drift = random.uniform(-0.5, 0.5)
    price = int(base_price * (1.0 + drift / 100.0))

    is_holding = holding is not None
    effective_source = "hold" if is_holding else source

    holding_block: dict | None = None
    if is_holding:
        elapsed_sec = int((now - holding.entry_time).total_seconds())
        pnl_pct = holding.pnl_pct(price) if price else None
        holding_block = {
            "entry_price": int(holding.entry_price),
            "entry_time": holding.entry_time.isoformat(),
            "elapsed_sec": elapsed_sec,
            "pnl_pct": pnl_pct if pnl_pct == pnl_pct else None,
            "stop_loss_price": int(holding.stop_loss_price),
            "take_profit_1_price": int(holding.take_profit_1_price),
            "take_profit_2_price": int(holding.take_profit_2_price),
            "time_stop_minutes": holding.time_stop_minutes,
            "triggers_fired": list(getattr(holding, "triggers_fired", [])),
        }

    return {
        "code": code,
        "name": name,
        "source": effective_source,
        "themes": ["AI데이터센터", "광케이블"] if source == "auto" else ["AI"],
        "header": {
            "grade": random.choice(["STRONG", "WATCH", "NEUTRAL"]),
            "score": round(random.uniform(-1.0, 7.0), 1),
            "reasons": [
                "+1 거래대금 50위내",
                f"+2 가속 동반 (5m {random.uniform(2,6):.1f} / 1m {random.uniform(2,6):.1f})",
                "+2 장대양봉 (윗꼬리 9%)",
            ],
        },
        "price": {
            "current": price,
            "change_pct": round(drift + 20, 2),
            "is_limit_up": source == "auto",
            "sell_29_pct": int(base_price * 1.29),
        },
        "volume": {
            "rank": random.randint(1, 30),
            "amount": random.randint(50, 1500) * 1_000_000_000,
            "turnover_pct": round(random.uniform(5, 20), 1),
        },
        "accel_5m": {"ratio": round(random.uniform(0.5, 6.0), 1), "bar_value": 5_000_000_000},
        "accel_1m": {"ratio": round(random.uniform(0.5, 6.0), 1), "bar_value": 1_000_000_000},
        "vp": {
            "current": round(random.uniform(80, 160), 0),
            "ma_5": round(random.uniform(80, 160), 0),
            "ma_1": round(random.uniform(80, 160), 0),
            "buy_ratio": round(random.uniform(-30, 60), 1),
        },
        "asking": {
            "bid_total": random.randint(50_000, 500_000),
            "ask_total": random.randint(50_000, 500_000),
            "ratio": round(random.uniform(0.5, 7.0), 1),
            "bid1_price": price - 100,
            "bid1_volume": 850,
            "ask1_price": price,
            "ask1_volume": 120,
        },
        "divergence": None,
        "holding": holding_block,
        "transition": None,
        "grace_remaining_sec": None,
        "trigger_states": None,
        "updated_at": now.isoformat(),
    }


async def _demo_tick_loop(session: MonitoringSession) -> None:
    """1초마다 데모 페이로드 갱신 → WebSocket broadcast 자동 발화.

    매 tick `load_holdings()` 호출 — worker 와 동일 패턴. 보유 종목이면 카드의
    source 가 hold 로 전환되어 보유 컬럼으로 이동. `session.last_prices` 도 함께
    갱신 — `/buy CODE` 가격 자동 보충용.
    """
    # 임포트는 함수 안 — load_holdings 가 settings 로드를 가질 수 있음
    from src.jongbae.exit_triggers import load_holdings

    demo_stocks = [
        ("091340", "대한광통신", "auto", 91300),
        ("012200", "계양전기", "rising", 8920),
        ("075180", "제룡전기", "auto", 91300),
        ("005930", "삼성전자", "manual", 79000),
    ]
    while True:
        try:
            holdings = load_holdings()
        except Exception:  # noqa: BLE001
            holdings = {}
        for code, name, source, base in demo_stocks:
            payload = _build_demo_payload(
                code, name, source, base, holding=holdings.get(code),
            )
            session.last_payloads[code] = payload
            cur = (payload.get("price") or {}).get("current")
            if cur:
                session.last_prices[code] = float(cur)
        session.last_payload_ts = now_kst()
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
