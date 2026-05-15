"""src.dashboard.api 통합 테스트 (M7 Phase 1).

FastAPI TestClient 로 REST + WebSocket 검증. apply_command 재사용 원칙 — PWA
endpoint 가 텔레그램 봇과 동일 effect 를 갖는지 확인.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from src.dashboard.api import create_app
from src.dashboard.state import MonitoringSession


@pytest.fixture
def session() -> MonitoringSession:
    s = MonitoringSession()
    s.paused = True  # 초기엔 off
    return s


@pytest.fixture
def client(session):
    from fastapi.testclient import TestClient

    app = create_app(session, broadcast_interval_sec=0.05)
    return TestClient(app)


def test_health_endpoint(client, session):
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["paused"] is True
    assert data["monitored_count"] == 0
    assert data["last_tick"] is None


def test_snapshot_empty(client):
    r = client.get("/api/snapshot")
    assert r.status_code == 200
    data = r.json()
    assert data["paused"] is True
    assert data["stocks"] == []
    assert data["updated_at"] is None


def test_snapshot_with_payloads(client, session):
    """worker 가 채운 last_payloads 가 그대로 노출됨."""
    now = datetime(2026, 5, 11, 9, 30)
    session.last_payloads["091340"] = {
        "code": "091340", "name": "대한광통신", "source": "auto",
    }
    session.last_payload_ts = now
    r = client.get("/api/snapshot")
    data = r.json()
    assert len(data["stocks"]) == 1
    assert data["stocks"][0]["code"] == "091340"
    assert data["updated_at"].startswith("2026-05-11T09:30")


def test_session_on_off(client, session):
    """POST /api/session 이 apply_command 와 동일 effect."""
    assert session.paused is True
    r = client.post("/api/session", json={"action": "on"})
    assert r.status_code == 200
    assert session.paused is False
    assert "ON" in r.json()["message"]

    r = client.post("/api/session", json={"action": "off"})
    assert r.status_code == 200
    assert session.paused is True


def test_session_invalid_action(client):
    r = client.post("/api/session", json={"action": "toggle"})
    assert r.status_code == 400


def test_watchlist_toggle_add(client, session):
    """POST /api/watchlist toggle → session.monitored 갱신."""
    r = client.post("/api/watchlist", json={"action": "toggle", "code": "005930"})
    assert r.status_code == 200
    assert "005930" in session.monitored

    # 한 번 더 토글 → 제거
    r = client.post("/api/watchlist", json={"action": "toggle", "code": "005930"})
    assert r.status_code == 200
    assert "005930" not in session.monitored


def test_watchlist_clear(client, session):
    session.add_manual("005930", datetime(2026, 5, 11, 9, 30))
    session.add_manual("000660", datetime(2026, 5, 11, 9, 30))
    r = client.post("/api/watchlist", json={"action": "clear"})
    assert r.status_code == 200
    assert "005930" not in session.monitored
    assert "000660" not in session.monitored


def test_watchlist_invalid_code(client):
    r = client.post("/api/watchlist", json={"action": "toggle", "code": "12345"})
    assert r.status_code == 400  # 6자리 X
    r = client.post("/api/watchlist", json={"action": "toggle", "code": "ABCDEF"})
    assert r.status_code == 400


def test_holdings_buy_sell(client, session, tmp_path, monkeypatch):
    """POST /api/holdings buy/sell — telegram_bot 의 _apply_buy/_apply_sell 재사용."""
    # holdings.json 영속화 우회 — tmp 경로로
    holdings_path = tmp_path / "holdings.json"
    monkeypatch.setattr(
        "src.jongbae.exit_triggers._state_path", lambda: holdings_path,
    )
    now = datetime(2026, 5, 11, 9, 30)
    session.last_prices["091340"] = 91300.0
    session.add_manual("091340", now)

    # buy with explicit price
    r = client.post("/api/holdings", json={
        "action": "buy", "code": "091340", "price": 91300, "time_stop_minutes": 15,
    })
    assert r.status_code == 200
    assert "보유 모드" in r.json()["message"]
    assert holdings_path.exists()
    data = json.loads(holdings_path.read_text())
    assert "091340" in data
    assert data["091340"]["entry_price"] == 91300.0
    assert data["091340"]["time_stop_minutes"] == 15

    # sell
    r = client.post("/api/holdings", json={"action": "sell", "code": "091340"})
    assert r.status_code == 200
    assert "감시 모드" in r.json()["message"]
    data = json.loads(holdings_path.read_text())
    assert "091340" not in data


def test_holdings_buy_price_autofill(client, session, tmp_path, monkeypatch):
    """price 생략 시 session.last_prices 에서 자동 보충 (round 20 정책)."""
    holdings_path = tmp_path / "holdings.json"
    monkeypatch.setattr(
        "src.jongbae.exit_triggers._state_path", lambda: holdings_path,
    )
    session.last_prices["091340"] = 91300.0
    session.add_manual("091340", datetime(2026, 5, 11, 9, 30))

    r = client.post("/api/holdings", json={"action": "buy", "code": "091340"})
    assert r.status_code == 200
    data = json.loads(holdings_path.read_text())
    assert data["091340"]["entry_price"] == 91300.0


def test_holdings_buy_price_fallback_to_payload(
    client, session, tmp_path, monkeypatch,
):
    """price 생략 + last_prices 비어 있어도 last_payloads.current 로 fallback (M7).

    데모 환경 또는 워밍업 중 last_prices 가 채워지기 전이라도 PWA 카드 데이터
    (session.last_payloads) 가 있으면 그 current price 로 자동 보충.
    """
    holdings_path = tmp_path / "holdings.json"
    monkeypatch.setattr(
        "src.jongbae.exit_triggers._state_path", lambda: holdings_path,
    )
    session.add_manual("091340", datetime(2026, 5, 11, 9, 30))
    # last_prices 비어 있음, last_payloads 에만 current price 있음
    session.last_payloads["091340"] = {
        "code": "091340",
        "name": "대한광통신",
        "price": {"current": 91300},
    }

    r = client.post("/api/holdings", json={"action": "buy", "code": "091340"})
    assert r.status_code == 200, r.json()
    assert "보유 모드" in r.json()["message"]
    data = json.loads(holdings_path.read_text())
    assert data["091340"]["entry_price"] == 91300.0


def test_holdings_buy_price_missing_everywhere(
    client, session, tmp_path, monkeypatch,
):
    """last_prices / last_payloads 모두 비어 있으면 명시 안내. 위험한 0 등록은 X."""
    holdings_path = tmp_path / "holdings.json"
    monkeypatch.setattr(
        "src.jongbae.exit_triggers._state_path", lambda: holdings_path,
    )
    session.add_manual("091340", datetime(2026, 5, 11, 9, 30))
    # 둘 다 빈 상태

    r = client.post("/api/holdings", json={"action": "buy", "code": "091340"})
    assert r.status_code == 200
    assert "최근 시세 미확보" in r.json()["message"]
    assert not holdings_path.exists()


def test_holdings_invalid_action(client):
    r = client.post("/api/holdings", json={"action": "trade", "code": "091340"})
    assert r.status_code == 400


def test_holdings_invalid_code(client):
    r = client.post("/api/holdings", json={"action": "buy", "code": "12"})
    assert r.status_code == 400


def test_invalid_json_body(client):
    r = client.post(
        "/api/session", content="not json", headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 400


def test_websocket_snapshot_on_connect(client, session):
    """WebSocket 첫 메시지 = 전체 snapshot."""
    session.last_payloads["091340"] = {"code": "091340", "name": "대한광통신", "source": "auto"}
    session.last_payload_ts = datetime(2026, 5, 11, 9, 30)
    with client.websocket_connect("/ws/monitor") as ws:
        msg = json.loads(ws.receive_text())
        assert msg["type"] == "snapshot"
        assert len(msg["payload"]["stocks"]) == 1
        assert msg["payload"]["stocks"][0]["code"] == "091340"


def test_websocket_tick_on_payload_change(client, session):
    """payload_ts 갱신 시 tick broadcast."""
    session.last_payloads["091340"] = {"code": "091340", "name": "대한광통신", "source": "auto"}
    session.last_payload_ts = datetime(2026, 5, 11, 9, 30)
    with client.websocket_connect("/ws/monitor") as ws:
        # snapshot 수신
        snap = json.loads(ws.receive_text())
        assert snap["type"] == "snapshot"

        # 페이로드 변경 시뮬레이션
        session.last_payloads["091340"] = {
            "code": "091340", "name": "대한광통신", "source": "auto", "_v": 2,
        }
        session.last_payload_ts = datetime(2026, 5, 11, 9, 30, 3)

        # broadcast_interval_sec=0.05 라 곧 tick 도착
        msg = json.loads(ws.receive_text())
        assert msg["type"] == "tick"
        assert msg["payload"]["stocks"][0].get("_v") == 2


def test_static_index_route(client):
    """`/` → index.html 응답."""
    r = client.get("/")
    assert r.status_code == 200
    # 단순 HTML 응답
    body = r.text
    assert "<!doctype html>" in body.lower()
    assert "모니터링" in body
