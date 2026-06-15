"""종배 레포트 웹사이트 FastAPI 앱.

라우트 (레포트별 개별 페이지 — 사용자 선택):
    GET /                      → 최신 일자로 redirect (없으면 빈 안내)
    GET /d/{date}              → 해당 일자 레포트 인덱스 (결정/사후 링크)
    GET /d/{date}/decision     → 결정 레포트 (구조화 카드 + 원문)
    GET /d/{date}/afterhours   → 사후 레포트 (마크다운 렌더)
    GET /archive               → 전체 일자 목록
    GET /api/d/{date}/status   → 라이브 폴링용 mtime (인증 필요)
    GET /healthz               → 헬스체크 (인증 제외)
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.reportweb import data as D
from src.reportweb.auth import (
    COOKIE_MAX_AGE,
    COOKIE_NAME,
    CookieAuthMiddleware,
    check_password,
    get_password,
    token_for,
)
from src.reportweb.render import build_decision_context, md_to_html

_HERE = Path(__file__).resolve().parent
_TEMPLATES = _HERE / "templates"
_STATIC = _HERE / "static"


def create_app(data_dir: Path) -> FastAPI:
    """레포트 웹 앱 생성. REPORTWEB_PASSWORD 미설정 시 ValueError (기동 차단)."""
    password = get_password()  # fail-loud

    app = FastAPI(title="종배 레포트", docs_url=None, redoc_url=None)
    app.add_middleware(CookieAuthMiddleware, password=password)
    app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")
    templates = Jinja2Templates(directory=str(_TEMPLATES))

    def _safe_next(nxt: str) -> str:
        """오픈 redirect 방지 — 사이트 내부 경로만 허용."""
        return nxt if (nxt.startswith("/") and not nxt.startswith("//")) else "/"

    def _not_found(request: Request, msg: str) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "error.html",
            {"message": msg, "dates": D.list_dates(data_dir)},
            status_code=404,
        )

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        return JSONResponse({"ok": True})

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request, next: str = "/"):
        return templates.TemplateResponse(
            request, "login.html", {"next": _safe_next(next), "error": False}
        )

    @app.post("/login")
    async def login_submit(request: Request):
        # urlencoded 바디 직접 파싱 (python-multipart 의존성 회피).
        from urllib.parse import parse_qs
        body = (await request.body()).decode("utf-8")
        form = parse_qs(body)
        nxt = _safe_next(form.get("next", ["/"])[0])
        if check_password(form.get("password", [""])[0], password):
            resp = RedirectResponse(url=nxt, status_code=302)
            resp.set_cookie(
                COOKIE_NAME, token_for(password),
                max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax", path="/",
            )
            return resp
        return templates.TemplateResponse(
            request, "login.html", {"next": nxt, "error": True}, status_code=401
        )

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        latest = D.latest_date(data_dir)
        if latest is None:
            return templates.TemplateResponse(
                request, "error.html",
                {"message": "아직 저장된 레포트가 없습니다.", "dates": []},
            )
        return RedirectResponse(url=f"/d/{latest.isoformat()}")

    @app.get("/archive", response_class=HTMLResponse)
    def archive(request: Request):
        dates = D.list_dates(data_dir)
        items = [
            {"date": d.isoformat(), "labels": [
                {"key": k, "name": D.REPORT_LABELS[k]} for k in D.available_labels(data_dir, d)
            ]}
            for d in dates
        ]
        return templates.TemplateResponse(
            request, "archive.html", {"items": items}
        )

    @app.get("/d/{date_str}", response_class=HTMLResponse)
    def day_index(request: Request, date_str: str):
        d = D.parse_date(date_str)
        if d is None:
            return _not_found(request, "잘못된 날짜 형식입니다.")
        labels = D.available_labels(data_dir, d)
        if not labels:
            return _not_found(request, f"{date_str} 레포트가 없습니다.")
        # 결정이 있으면 결정으로 바로, 아니면 첫 가용 레포트로.
        target = "decision" if "decision" in labels else labels[0]
        return RedirectResponse(url=f"/d/{date_str}/{target}")

    @app.get("/d/{date_str}/decision", response_class=HTMLResponse)
    def decision(request: Request, date_str: str):
        d = D.parse_date(date_str)
        if d is None:
            return _not_found(request, "잘못된 날짜 형식입니다.")
        payload = D.load_decision_payload(data_dir, d)
        raw_md = D.load_markdown(data_dir, d, "decision")
        if payload is None and raw_md is None:
            return _not_found(request, f"{date_str} 결정 레포트가 없습니다.")
        ctx = build_decision_context(payload) if payload else {
            "report_time": None, "regime": None, "leading_themes": [],
            "cards": [], "n_candidates": 0,
        }
        return templates.TemplateResponse(
            request, "decision.html",
            {
                "page_date": date_str,
                "active": "decision",
                "labels": _nav_labels(data_dir, d),
                "ctx": ctx,
                "raw_html": md_to_html(raw_md) if raw_md else None,
                "live": D.is_market_window(),
            },
        )

    @app.get("/d/{date_str}/afterhours", response_class=HTMLResponse)
    def afterhours(request: Request, date_str: str):
        d = D.parse_date(date_str)
        if d is None:
            return _not_found(request, "잘못된 날짜 형식입니다.")
        raw_md = D.load_markdown(data_dir, d, "afterhours")
        if raw_md is None:
            return _not_found(request, f"{date_str} 사후 레포트가 없습니다.")
        return templates.TemplateResponse(
            request, "afterhours.html",
            {
                "page_date": date_str,
                "active": "afterhours",
                "labels": _nav_labels(data_dir, d),
                "raw_html": md_to_html(raw_md),
                "live": D.is_market_window(),
            },
        )

    @app.get("/api/d/{date_str}/status")
    def status(date_str: str):
        d = D.parse_date(date_str)
        if d is None:
            return JSONResponse({"error": "bad date"}, status_code=400)
        return JSONResponse({
            "decision": D.report_mtime(data_dir, d, "decision"),
            "afterhours": D.report_mtime(data_dir, d, "afterhours"),
            "market_window": D.is_market_window(),
        })

    return app


def _nav_labels(data_dir: Path, d) -> list[dict]:
    """탭 네비게이션용 — 해당 일자 가용 레포트 (없는 건 비활성 회색)."""
    avail = set(D.available_labels(data_dir, d))
    return [
        {"key": k, "name": name, "enabled": k in avail}
        for k, name in D.REPORT_LABELS.items()
    ]
