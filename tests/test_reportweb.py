"""종배 레포트 웹사이트 테스트. 실제 파일 I/O 는 tmp_path, 외부 네트워크 없음."""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from src.reportweb import data as D
from src.reportweb.render import build_decision_context, candidate_card, md_to_html


# ── fixture: 합성 레포트 데이터 ──────────────────────────────────────────────

def _write_fixture(root):
    """tmp DATA_DIR 에 결정 JSON/MD + 사후 MD 작성."""
    (root / "reports" / "2026-06-15").mkdir(parents=True)
    (root / "reports" / "2026-06-14").mkdir(parents=True)
    (root / "decisions").mkdir(parents=True)

    # 결정 마크다운 (원문) + 사후
    (root / "reports" / "2026-06-15" / "14_50_decision.md").write_text(
        "# 결정 레포트\n\n| 종목 | Kelly |\n|---|---|\n| 가 | 5% |\n", encoding="utf-8"
    )
    (root / "reports" / "2026-06-15" / "16_00_afterhours.md").write_text(
        "# 사후 레포트\n\n오늘 갭상 결과.\n", encoding="utf-8"
    )
    # 6/14 는 결정만 (사후 없음 — 탭 비활성 검증용)
    (root / "reports" / "2026-06-14" / "14_50_decision.md").write_text("# 결정", encoding="utf-8")

    payload = {
        "report_date": "2026-06-15",
        "report_time": "14:50:00",
        "market": {"kospi_above_ma200": True},
        "leading_themes": [{"theme": "전력설비", "count": 5}, {"theme": "조선", "count": 3}],
        "candidates": [{
            "code": "033100", "name": "제룡전기", "rank_in_report": 1, "rank": 3,
            "is_top3": True, "is_limit_up": True, "themes": ["전력설비", "전선"],
            "daily_return": 29.9, "price": 91300, "prev_close": 70200,
            "intraday_high": 91300, "intraday_high_pct": 30.0, "intraday_low": 72000,
            "market_cap": 8500, "trading_value": 125_000_000_000, "volume": 1_400_000,
            "volume_rank": 3, "turnover": 0.41, "turnover_rank": 7,
            "sizing_layer": "layer2",
            "layers": {"layer2": {"n": 42, "p": 0.67, "avg_gap": 2.1, "median_gap": 1.8}},
            "sizing": {"kelly": 0.12, "sharpe": 0.09, "equal": 0.20,
                       "kelly_bucket": 0.08, "kelly_bucket_rel": 0.04},
            "sizing_bucket": "1~10위", "sample_sufficient": True,
            "historical_aux": {"n_ret10": 10, "n_gap_up": 6, "ratio": 0.6},
            "historical_aux_matrix": {
                "('year', 0)": {"n": 150, "n_gap_up": 83, "ratio": 0.553},
                "('month', 0)": {"n": 13, "n_gap_up": 7, "ratio": 0.583},
            },
            "candle_aux": {"consec_up_days": 2, "big_candle_count": 1,
                           "big_threshold": 10.0, "today_is_nth_big": 1},
            "r4v2_check": {"close_within_10pct_high": True, "is_52w_high": True},
            "intraday_signals": {
                "ccnl_strength": {"ccnl_strength": 120.44, "buy_ratio": None},
                "asking_price": {"bid_ask_ratio": 1.72, "bid1_price": 91200,
                                 "bid1_volume": 620, "ask1_price": 91300, "ask1_volume": 1315,
                                 "bid_total_volume": 12127, "ask_total_volume": 7035},
                "investor_flow": {"foreign_net_buy": 43000, "foreign_net_buy_value": 9_810_000_000,
                                  "institution_net_buy": 331000, "institution_net_buy_value": 75_000_000_000,
                                  "program_net_buy": 162458, "program_net_buy_value": 37_000_000_000},
                "investor_nday_avg": {"n_days": 7, "foreign_net_buy_avg": -289571,
                                      "institution_net_buy_avg": 296714, "program_net_buy_avg": -198731},
            },
            "nxt_tradable": True,
            "trends": {
                "trading_value": [{"date": "2026-06-13", "value": 100_000_000_000, "rank": 5},
                                  {"date": "2026-06-15", "value": 125_000_000_000, "rank": 3}],
                "turnover": [{"date": "2026-06-15", "value": 0.41, "rank": 7}],
                "supply": [{"date": "2026-06-15", "foreign": 43000, "institution": 331000, "program": 162458}],
            },
        }],
    }
    (root / "decisions" / "2026-06-15.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


@pytest.fixture
def data_dir(tmp_path):
    _write_fixture(tmp_path)
    return tmp_path


# ── data 레이어 ──────────────────────────────────────────────────────────────

def test_list_dates_desc(data_dir):
    dates = D.list_dates(data_dir)
    assert [d.isoformat() for d in dates] == ["2026-06-15", "2026-06-14"]


def test_available_labels(data_dir):
    from datetime import date
    assert D.available_labels(data_dir, date(2026, 6, 15)) == ["decision", "afterhours"]
    assert D.available_labels(data_dir, date(2026, 6, 14)) == ["decision"]  # 사후 없음


def test_parse_date():
    assert D.parse_date("2026-06-15") is not None
    assert D.parse_date("nope") is None
    assert D.parse_date("9999-99-99") is None


def test_load_decision_payload(data_dir):
    from datetime import date
    p = D.load_decision_payload(data_dir, date(2026, 6, 15))
    assert p["report_time"] == "14:50:00"
    assert len(p["candidates"]) == 1


def test_is_market_window():
    assert D.is_market_window(datetime(2026, 6, 15, 10, 0)) is True   # 월 10:00
    assert D.is_market_window(datetime(2026, 6, 15, 17, 0)) is False  # 장후
    assert D.is_market_window(datetime(2026, 6, 13, 10, 0)) is False  # 토


# ── render ───────────────────────────────────────────────────────────────────

def test_candidate_card_formatting():
    c = {
        "code": "033100", "name": "제룡전기", "rank_in_report": 1, "is_top3": True,
        "is_limit_up": True, "themes": ["a", "b", "c", "d"], "daily_return": 29.9,
        "price": 91300, "trading_value": 125_000_000_000, "turnover": 0.41,
        "sizing_layer": "layer2", "layers": {"layer2": {"n": 42, "p": 0.67, "avg_gap": 2.1}},
        "sizing": {"kelly": 0.12, "sharpe": 0.09, "equal": 0.20, "kelly_bucket": 0.08},
        "sample_sufficient": True, "historical_aux": {"n_ret10": 10, "n_gap_up": 6, "ratio": 0.6},
    }
    v = candidate_card(c)
    # 요약 5필드
    assert v["ret_str"] == "+29.90%" and v["ret_pos"] is True
    assert v["value_str"] == "1,250억"
    assert v["top3"] is True and v["limit_up"] is True
    # 상세
    d = v["d"]
    assert d["price_str"] == "91,300"
    assert d["kelly"] == "12.0%" and d["equal"] == "20.0%"
    assert d["sizing_layer"] == "L2 상한가"
    assert d["aux_ratio"] == "60%"
    assert d["themes"] == ["a", "b", "c", "d"]  # 상세에선 전체 노출
    picked = [l for l in d["layers"] if l["picked"]]
    assert picked and picked[0]["label"] == "L2 상한가" and picked[0]["p"] == "67%"


def test_candidate_card_missing_fields_safe():
    """필드 누락/None 이어도 죽지 않고 — 표시로 폴백."""
    v = candidate_card({"code": "000000"})
    assert v["ret_str"] == "—"
    d = v["d"]
    assert d["kelly"] == "—"
    assert d["layers"] == [] and d["matrix"] is None


def test_build_decision_context_regime():
    ctx = build_decision_context({"market": {"kospi_above_ma200": True}, "candidates": []})
    assert ctx["regime"]["cls"] == "bull"
    ctx2 = build_decision_context({"market": {"kospi_above_ma200": False}, "candidates": []})
    assert ctx2["regime"]["cls"] == "bear"
    ctx3 = build_decision_context({"candidates": []})  # market 없음 (과거 JSON)
    assert ctx3["regime"] is None


def test_md_to_html_tables():
    html = md_to_html("| a | b |\n|---|---|\n| 1 | 2 |")
    assert "<table>" in html


# ── app (라우트 + 인증) ──────────────────────────────────────────────────────

def _make_app(data_dir):
    from src.reportweb.app import create_app
    return create_app(data_dir)


@pytest.fixture
def anon(data_dir, monkeypatch):
    """미인증 클라이언트 (쿠키 없음)."""
    monkeypatch.setenv("REPORTWEB_PASSWORD", "secret")
    from fastapi.testclient import TestClient
    return TestClient(_make_app(data_dir))


@pytest.fixture
def client(data_dir, monkeypatch):
    """인증 쿠키가 세팅된 클라이언트."""
    monkeypatch.setenv("REPORTWEB_PASSWORD", "secret")
    from fastapi.testclient import TestClient
    from src.reportweb.auth import COOKIE_NAME, token_for
    c = TestClient(_make_app(data_dir))
    c.cookies.set(COOKIE_NAME, token_for("secret"))
    return c


def test_create_app_requires_password(data_dir, monkeypatch):
    monkeypatch.delenv("REPORTWEB_PASSWORD", raising=False)
    from src.reportweb.app import create_app
    with pytest.raises(ValueError):
        create_app(data_dir)


def test_unauthed_redirects_to_login(anon):
    # 페이지는 /login 으로 redirect, API 는 401, 제외 경로는 통과.
    r = anon.get("/d/2026-06-15/decision", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith("/login")
    assert anon.get("/api/d/2026-06-15/status", follow_redirects=False).status_code == 401
    assert anon.get("/healthz").status_code == 200
    assert anon.get("/login").status_code == 200  # 로그인 페이지 자체는 접근 가능


def test_login_flow(anon):
    # 비번 틀리면 401 + 에러 메시지, 맞으면 302 + 쿠키 발급 → 이후 접근 가능.
    bad = anon.post("/login", data={"password": "wrong", "next": "/"}, follow_redirects=False)
    assert bad.status_code == 401 and "틀렸" in bad.text

    ok = anon.post("/login", data={"password": "secret", "next": "/d/2026-06-15/decision"},
                   follow_redirects=False)
    assert ok.status_code == 302 and ok.headers["location"] == "/d/2026-06-15/decision"
    from src.reportweb.auth import COOKIE_NAME
    assert COOKIE_NAME in ok.cookies  # Set-Cookie 발급
    # 쿠키 보유 상태(anon 클라이언트가 자동 보관)로 보호 페이지 접근 OK
    assert anon.get("/d/2026-06-15/decision").status_code == 200


def test_login_only_password_field(anon):
    """로그인 페이지는 비번 입력 1개 — 아이디 필드 없음."""
    html = anon.get("/login").text
    assert html.count('type="password"') == 1
    assert 'name="username"' not in html and 'type="text"' not in html


def test_decision_page(client):
    r = client.get("/d/2026-06-15/decision")
    assert r.status_code == 200
    for n in ["제룡전기", "종배 후보", "Kelly", "강세장", "전력설비", "전체 레포트 원문",
              "상세 보기", "갭상 매트릭스", "체결강도"]:
        assert n in r.text
    assert "TOP3" not in r.text  # 배지 제거 (노란 테두리로 대체)


def test_afterhours_page_and_missing(client):
    assert client.get("/d/2026-06-15/afterhours").status_code == 200
    assert client.get("/d/2026-06-14/afterhours").status_code == 404  # 사후 없음


def test_status_and_archive_and_index(client):
    st = client.get("/api/d/2026-06-15/status").json()
    assert st["decision"] is not None and st["afterhours"] is not None
    assert client.get("/archive").status_code == 200
    ix = client.get("/", follow_redirects=False)
    assert ix.status_code in (302, 307) and ix.headers["location"] == "/d/2026-06-15"


def test_bad_date_404(client):
    assert client.get("/d/not-a-date/decision").status_code == 404
