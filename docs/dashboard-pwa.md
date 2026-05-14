# PWA 대시보드 (M7) 사양

이 문서는 M6 실시간 모니터링을 텔레그램 외에 **아이패드 한 화면에 카드 그리드로 보여주는 PWA 대시보드**의 사양과 정책을 정의한다. 코드 작성 전 반드시 본 문서와 `docs/monitoring-guide.md` 를 확인할 것.

---

## 1. 배경

### 1.1 문제

텔레그램은 한 화면에 동시 표시 가능한 메시지 갯수에 한계가 있다. 종목 6~10개를 모니터링하면 아이패드 가로 화면에서도 스크롤이 필요하고, 카드 갱신 시 메시지가 위로 밀려나며 시선 이동이 잦다. M6 의 1~3초 갱신 워크플로우를 **한 화면에 다 보이는** 형태로 보강할 필요.

### 1.2 목표

- 아이패드(가로) 1 화면에 종목 카드 6~10개 그리드
- 외부(WAN) 접근 가능, 실시간 갱신 (1~3초)
- M6 와 **동일한 데이터 소스 / 동일한 holdings.json** — 채널만 추가, 정책 동일
- 기존 텔레그램 봇은 이벤트 푸시(상한가 진입 / 14:50 결정 / 16:00 사후)용으로 점진 축소

### 1.3 비목표 (영구 제외)

- **거래소 주문 input** — KIS 실주문 코드 영구 작성 X. CLAUDE.md "자동 매매 절대 금지" 정책 M7 에 동일 적용
- **자체 클라우드 백엔드** — Supabase/Firebase 등 외부 클라우드로 증권 정보 송신 X. 집 데스크탑에서 직접 서빙
- **아이패드 → 거래 명령** — 거래는 별도 스마트폰 HTS/MTS 에서 직접 (사용자 워크플로우)

---

## 2. 아키텍처

```
[KIS API]
   ↓
[기존 M6 worker (src/dashboard/worker.py)]
   ↓ asyncio.Queue (tick payload broadcast)
[FastAPI 서버 (src/dashboard/api.py 신규)]
   ↓ WebSocket /ws/monitor
[Tailscale (본인 디바이스 한정)]
   ↓ HTTPS
[iPad PWA (src/dashboard/static/index.html)]

(병행) Telegram bot (src/notify/telegram_bot.py) — 이벤트 푸시 + 명령 백워드 호환
```

### 2.1 통신: asyncio.Queue (Redis 미도입)

- worker / FastAPI 같은 프로세스 안에서 `asyncio.Queue` 또는 `set[WebSocket]` broadcast
- Redis pub/sub 은 분리 깔끔하나 의존성 추가 비용 큼. v0 미도입
- 향후 분리 필요 시 `MessageBus` 인터페이스만 둬서 교체 가능하게 설계

### 2.2 외부 접근: Tailscale (Cloudflare Tunnel 미도입)

- 본인 디바이스(아이패드 + 폰)만 접근하면 됨 → Tailscale 가 단순
- 도메인 / Cloudflare Access SSO 불필요
- HTTPS: Tailscale MagicDNS or `tailscale serve` TLS

### 2.3 인증

- Tailscale 자체 인증으로 충분 (디바이스 한정)
- 별도 토큰/Basic auth 불필요. 단 FastAPI 는 `localhost` + Tailscale 인터페이스에만 bind (`0.0.0.0` 금지)

---

## 3. 보유 토글 정책 (중요)

### 3.1 PWA 버튼 = M6 `/buy`·`/sell` 명령과 동일 effect

PWA 카드 상단의 보유 토글 버튼은 **텔레그램 봇의 `/buy` / `/sell` / `/clear` 명령과 동일한 함수를 호출**한다.

- 클릭 → POST `/api/holdings` → `telegram_bot.py` 의 `_handle_buy` / `_handle_sell` 핸들러 그대로 재사용
- → `holdings.json` atomic write
- → worker 가 다음 tick 에서 [보유] 모드 카드로 전환
- → broadcast 통해 텔레그램 + PWA 양쪽 동기화

**이중 구현 X** — PWA 가 텔레그램과 다른 코드 경로를 갖지 않는다. 채널만 늘리고 로직은 한 군데.

### 3.2 buy 토글 — 가격 입력

- 기본: 현재가 자동 보충 (round 20 동작)
- 옵션: 실제 체결가 수동 입력 (modal input)
- 옵션: TIME_STOP_MIN (시간 손절 분 단위) 입력

수동 입력이 더 정확함. KIS 체결가는 호가 정상 잡혔다는 가정 (장 마감 후/장 시간 외엔 안내 메시지).

### 3.3 sell 토글 — 가격 입력 X

- 청산은 단순 [보유 → 감시] 전환. 가격 입력 불필요
- 실제 매도 체결은 사용자가 HTS/MTS 에서 직접

### 3.4 정책 명시 (CLAUDE.md "자동 매매 절대 금지" 정합)

| input 종류 | 허용 여부 |
|---|---|
| 보유 등록 (holdings.json) | ✅ — 모니터링 메타 데이터 |
| 보유 해제 (holdings.json) | ✅ — 모니터링 메타 데이터 |
| 종목 추가 / 제거 (감시 리스트) | ✅ — `/list` / 6자리 코드 토글과 동일 |
| `/on` / `/off` 토글 | ✅ — round 18 동일 |
| **KIS 매수 주문** | ❌ **영구 미작성** |
| **KIS 매도 주문** | ❌ **영구 미작성** |

---

## 4. 카드 페이로드 구조

`render.py` 의 텔레그램 텍스트 렌더와 별도로, **구조화된 JSON 페이로드**를 생성한다. PWA 가 직접 텍스트 파싱하지 않게.

```python
# src/dashboard/render.py 에 추가
def build_monitor_payload(stock: MonitoredStock, ...) -> dict:
    return {
        "code": "091340",
        "name": "대한광통신",
        "source": "auto" | "rising" | "manual" | "hold",
        "header": {
            "grade": "STRONG" | "WATCH" | "NEUTRAL" | "AVOID" | None,
            "score": 6.5,
            "reasons": ["+1 거래대금 50위내", "+2 가속 동반 (5m 5.0 / 1m 5.0)", ...],
        },
        "theme": "AI데이터센터 / 광케이블",
        "price": {
            "current": 91300, "change_pct": 30.0, "is_limit_up": True,
            "sell_29_pct": 90500,
        },
        "volume": {"rank": 1, "amount_billion": 1247, "turnover_pct": 18.3},
        "accel_5m": {"ratio": 5.5, "marker": "⚡", ...},
        "accel_1m": {"ratio": 5.5, "marker": "⚡", ...},
        "vp": {"current": 142, "ma5": 138, "ma1": 135},
        "asking": {"bid": 320000, "ask": 45000, "ratio": 7.1},
        "holding": None | {
            "buy_price": 89000, "elapsed_sec": 1820,
            "pnl_pct": 2.6,
            "triggers": {"A1": False, "A2": True, ...},
        },
        "updated_at": "2026-05-14T10:23:45+09:00",
        "stale": False,  # 마지막 tick 으로부터 10s 초과 시 True
    }
```

WebSocket 메시지:

```json
{"type": "tick", "payload": {"stocks": [...]}, "ts": "..."}
{"type": "alert", "payload": {"event": "limit_up", "code": "091340"}, "ts": "..."}
{"type": "session", "payload": {"on": true, "active_count": 3}, "ts": "..."}
```

---

## 5. 화면 레이아웃

### 5.1 아이패드 가로 (1180~1366px) — 메인 타겟

```
┌────────────────────────────────────────────────────────┐
│ [세션 상태] /on  3종목  09:23:45            [⚙ 설정]   │
├─────────────────┬─────────────────┬────────────────────┤
│  자동 (1)       │  부상 (2)       │  보유 (1)          │
├─────────────────┼─────────────────┼────────────────────┤
│ [카드 1]        │ [카드 2]        │ [카드 5 — 보유]    │
│ [카드 ... ]     │ [카드 3]        │                    │
│                 │ [카드 4]        │                    │
└─────────────────┴─────────────────┴────────────────────┘
                  [수동 등록 +]
```

- 그룹 컬럼: 자동(주도주) / 부상(RISING) / 보유(HOLD) / 수동(MANUAL)
- 카드 클릭 → 상세 펼침 (R14 사유 전체, R15 트리거 상세, 시계열 미니차트)
- 카드 상단 우측: `[+ 보유 등록]` / `[✕ 청산]` 토글 버튼

### 5.2 폰 세로 — 보조 화면

- 단일 컬럼 카드 리스트 (자동 → 부상 → 보유 → 수동 순)
- 보유 카드를 상단 고정

---

## 6. 정책 정합 — CLAUDE.md 와의 매핑

| CLAUDE.md 원칙 | M7 적용 |
|---|---|
| 자동 매매 절대 금지 | KIS 주문 input X (3.4 표) |
| 터미널 친화적 (GUI 의존 X) | CLI 기반 / PWA 는 **선택적 채널** — 텔레그램 단독 운영도 OK |
| API 키 절대 커밋 X | FastAPI 토큰도 `.env` |
| Asia/Seoul 시간 | `updated_at` ISO8601 + KST offset 명시 |
| fail-loud | WS 끊김 / stale 10s+ → 텔레그램 에러 알림 |
| 메시지 1~2초 send X | WS 는 푸시 X. polling/edit 동일 철학 |
| KIS rate limit | worker 는 그대로 (M6), PWA 는 worker 캐시만 읽음 → 추가 호출 X |
| KIS mock 모드 | FastAPI 도 `KIS_API_MODE=mock` 에서 demo fixture 페이로드 송출 |
| editMessageText 정책 | PWA 도 같은 정책 — 카드는 갱신, 푸시 X. Web Notifications 은 opt-in 강제 |

---

## 7. 확정 사항 (2026-05-14)

| # | 항목 | 확정 | 비고 |
|---|---|---|---|
| 1 | 외부 접근 방식 | **Tailscale** | 본인 디바이스 한정 (데스크탑/아이패드/폰). 도메인·Cloudflare 불필요 |
| 2 | 인증 강화 | **Tailscale only** | 디바이스 인증으로 충분. 별도 토큰/SSO 없음. 디바이스 늘면 재검토 |
| 3 | 차트 라이브러리 | **텍스트 sparkline → 후반 lightweight-charts** | Phase 1~3 까진 의존성 0. Phase 4 후반 lightweight-charts CDN 추가 |
| 4 | 분봉 영속화 범위 | **모니터링 종목만** | `data/intraday_series/YYYY-MM-DD/CODE.parquet`. 디스크 부담 ↓, 복기 도구와 공유 |
| 5 | 텔레그램 봇 위상 | **동시 운영** | 이벤트 푸시 + PWA 미접속 시 fallback. 점진 축소는 운영 6개월 후 재검토 |

상기 항목 변경 시 본 표를 갱신하고 `docs/plan.md` M7 도 동기화.

---

## 8. 시너지 — 분봉 영속화 (복기 도구와 공유)

`docs/plan.md` 의 "복기 도구 (post-mortem replay)" (L269~) 는 분봉/VP 영속화 인프라가 블로커. M7 PWA 미니차트도 같은 시계열 데이터를 필요로 함 → **둘이 공유**:

```
data/intraday_series/YYYY-MM-DD/CODE.parquet
  cols: ts, price, vp, vp_5ma, vp_1ma, vol_5m_sum, vol_1m_sum,
        turnover_pct, accel_5m, accel_1m, candle_body_pct,
        upper_wick_pct, lower_wick_pct
```

- worker tick 시 메모리 deque → 1~5분 주기 flush
- KIS 추가 호출 X (이미 worker 가 받아온 데이터)
- M7 미니차트 + 복기 도구 + 향후 백테스트 모두 동일 소스 사용

이 영속화는 M5.5(시총 적재)와도 인접해서 한 트랙으로 묶을 수 있다.

---

## 9. 의존성

`requirements.txt` 추가 예정:

```
fastapi>=0.110
uvicorn[standard]>=0.27
websockets>=12.0
# 정적 파일 서빙은 fastapi.staticfiles 로 충분 (Tailwind/Vanilla JS CDN)
```

PWA 측은 빌드 도구 없이 CDN 만 사용:

- Tailwind CSS (CDN)
- Vanilla JS + WebSocket native
- (Phase 4) lightweight-charts (CDN)

빌드 도구(npm/webpack)는 v0 도입 X — "터미널 친화적" 원칙.

---

## 10. 운영

- systemd 서비스 — 기존 `deploy/jongbae.service` 가 FastAPI 도 같이 띄움 (단일 프로세스)
- 로그 — `loguru` 통일, FastAPI access log 도 같은 채널
- 헬스체크 — `/api/health` endpoint (worker tick 마지막 시각 / KIS 토큰 / holdings.json 존재 여부)
- 정전/재시작 — systemd `Restart=always`. holdings.json 은 이미 영속, 시계열은 메모리 손실 후 worker 가 새로 채움

---

## 정정 이력

| 일자 | 변경 | 사유 |
|---|---|---|
| 2026-05-14 | 초안 작성 | M6 카드를 텔레그램 + PWA 두 채널로 확장. KIS 주문 input 영구 X 정책 명시 |
| 2026-05-14 | §7 결정 5항목 확정 | Tailscale / Tailscale only / sparkline→lightweight-charts / 모니터링 종목만 / 텔레그램 동시 운영 |
