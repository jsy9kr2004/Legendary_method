"""FastAPI + WebSocket 대시보드 (M7 Phase 1).

worker 와 같은 프로세스 안에서 동작. `MonitoringSession` 을 공유 — worker tick
이 채운 `session.last_payloads` 를 WebSocket 으로 broadcast, REST 핸들러는
`telegram_bot.apply_command` 를 재사용해 holdings/watchlist/session 토글 처리.

정책 (CLAUDE.md `자동 매매 절대 금지` 정합):
    - REST input 은 모니터링 메타 데이터만 허용 — buy/sell 은 `holdings.json`
      atomic write 트리거이며 KIS 거래소 주문 호출 X.
    - WebSocket 은 server → client push only. 텔레그램 `editMessageText` 와
      동일 철학 (카드 갱신만, 푸시 X).
    - bind 는 호출자가 `127.0.0.1` + Tailscale 인터페이스로 제한해야 함
      (`uvicorn.run(host="127.0.0.1")` + 외부는 Tailscale 가 처리).

스키마는 `docs/dashboard-pwa.md` §4·§5 와 동기화.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.config import now_kst
from src.dashboard.state import MonitoringSession
from src.notify.telegram_bot import Command, apply_command


def _build_snapshot(session: MonitoringSession) -> dict[str, Any]:
    """현재 세션 → WebSocket/REST 응답용 dict."""
    ts = session.last_payload_ts
    return {
        "paused": session.paused,
        "stocks": list(session.last_payloads.values()),
        "updated_at": ts.isoformat() if ts else None,
    }


def _stale_seconds(session: MonitoringSession, now: datetime) -> float | None:
    if session.last_payload_ts is None:
        return None
    return (now - session.last_payload_ts).total_seconds()


def create_app(
    session: MonitoringSession,
    *,
    static_dir: Path | None = None,
    broadcast_interval_sec: float = 1.0,
) -> FastAPI:
    """FastAPI 앱 생성.

    Args:
        session: worker 와 공유되는 모니터링 세션.
        static_dir: 정적 파일 디렉토리 (없으면 `src/dashboard/static`).
        broadcast_interval_sec: WebSocket 변경 감지 polling 주기. worker tick
            은 2초라 1초 polling 이면 약간 lag 후 즉시 push (지터 ≤ 1s).
    """
    app = FastAPI(title="Jongbae Dashboard", docs_url=None, redoc_url=None)
    app.state.session = session
    app.state.broadcast_interval_sec = broadcast_interval_sec

    if static_dir is None:
        static_dir = Path(__file__).parent / "static"

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        now = now_kst()
        return {
            "ok": True,
            "paused": session.paused,
            "monitored_count": len(session.monitored),
            "last_tick": (
                session.last_payload_ts.isoformat()
                if session.last_payload_ts else None
            ),
            "stale_sec": _stale_seconds(session, now),
            "now": now.isoformat(),
        }

    @app.get("/api/snapshot")
    def snapshot() -> dict[str, Any]:
        return _build_snapshot(session)

    @app.post("/api/holdings")
    async def holdings_action(request: Request) -> JSONResponse:
        """보유 토글 — telegram_bot 의 /buy /sell 명령과 동일 effect.

        Body: {"action": "buy"|"sell", "code": "091340",
               "price": 91300 (buy 옵션), "time_stop_minutes": 10 (buy 옵션)}
        """
        body = await _parse_json(request)
        action = body.get("action")
        code = body.get("code")
        if not isinstance(code, str) or len(code) != 6 or not code.isdigit():
            raise HTTPException(400, "code must be 6-digit string")
        if action == "buy":
            cmd = Command(
                kind="buy",
                code=code,
                price=_optional_float(body.get("price")),
                time_stop_minutes=_optional_int(body.get("time_stop_minutes")),
            )
        elif action == "sell":
            cmd = Command(kind="sell", code=code)
        else:
            raise HTTPException(400, "action must be 'buy' or 'sell'")
        message = apply_command(cmd, session, now_kst())
        return JSONResponse({"ok": True, "message": message})

    @app.post("/api/session")
    async def session_action(request: Request) -> JSONResponse:
        """세션 ON/OFF — /on /off 명령과 동일 effect."""
        body = await _parse_json(request)
        action = body.get("action")
        if action == "on":
            cmd = Command(kind="on")
        elif action == "off":
            cmd = Command(kind="off")
        else:
            raise HTTPException(400, "action must be 'on' or 'off'")
        message = apply_command(cmd, session, now_kst())
        return JSONResponse({"ok": True, "message": message})

    @app.post("/api/watchlist")
    async def watchlist_action(request: Request) -> JSONResponse:
        """감시 종목 토글 — 6자리 코드 입력 / /clear 와 동일 effect.

        Body: {"action": "toggle"|"clear", "code": "091340" (toggle 시 필수)}
        """
        body = await _parse_json(request)
        action = body.get("action")
        if action == "toggle":
            code = body.get("code")
            if not isinstance(code, str) or len(code) != 6 or not code.isdigit():
                raise HTTPException(400, "code must be 6-digit string")
            cmd = Command(kind="toggle_code", code=code)
        elif action == "clear":
            cmd = Command(kind="clear")
        else:
            raise HTTPException(400, "action must be 'toggle' or 'clear'")
        message = apply_command(cmd, session, now_kst())
        return JSONResponse({"ok": True, "message": message})

    @app.websocket("/ws/monitor")
    async def ws_monitor(ws: WebSocket) -> None:
        """모니터링 변경분을 push. 첫 메시지는 전체 snapshot.

        worker tick (2초) 이 session.last_payload_ts 를 갱신할 때마다 1회 push.
        sleep 단위는 broadcast_interval_sec (기본 1초) — worker tick 직후 약간
        lag 후 broadcast 가 일관성 있게 동작.
        """
        await ws.accept()
        try:
            # snapshot on connect
            await ws.send_text(json.dumps({
                "type": "snapshot",
                "payload": _build_snapshot(session),
                "ts": now_kst().isoformat(),
            }, ensure_ascii=False))
            last_seen_ts = session.last_payload_ts
            while True:
                await asyncio.sleep(app.state.broadcast_interval_sec)
                current_ts = session.last_payload_ts
                if current_ts == last_seen_ts:
                    continue
                last_seen_ts = current_ts
                await ws.send_text(json.dumps({
                    "type": "tick",
                    "payload": _build_snapshot(session),
                    "ts": current_ts.isoformat() if current_ts else None,
                }, ensure_ascii=False))
        except WebSocketDisconnect:
            return

    # 정적 파일 — `/` → index.html, `/static/*` → assets.
    # 정적 디렉토리 없으면 mount 스킵 (테스트 환경 호환).
    if static_dir.exists():
        app.mount(
            "/static",
            StaticFiles(directory=str(static_dir)),
            name="static",
        )

        index_path = static_dir / "index.html"

        @app.get("/")
        def root() -> FileResponse:
            if not index_path.exists():
                raise HTTPException(404, "index.html not found")
            return FileResponse(str(index_path))

    return app


async def _parse_json(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, "invalid JSON body") from exc
    if not isinstance(body, dict):
        raise HTTPException(400, "body must be JSON object")
    return body


def _optional_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _optional_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
