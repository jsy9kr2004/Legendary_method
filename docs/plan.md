# 개발 계획 (plan.md)

## 프로젝트 비전

한국 주식 매매 의사결정을 보조하는 자동 레포트 시스템. 매수/매도 실행은 사람이 직접 하지만, **무엇을, 언제, 얼마나** 살지에 대한 정량적 근거와 historical 통계를 적시에 제공한다.

세 가지 매매 전략을 순차적으로 지원할 예정:

1. **주도주 매매** (스윙/중기, 수 주~수 개월) — TODO
2. **종배 매매** (오버나잇, 1일) — **현재 개발 중**
3. **스윙 매매** (수 일~수 주) — TODO

본 문서는 종배 매매 모듈(2번)의 v0 개발 계획이다.

---

## 마일스톤

### Milestone 0: 데이터 인프라 (Week 1~2)

**목표:** 일봉 + 종목 메타 + 테마 매핑 데이터를 안정적으로 매일 적재한다.

- [x] ~~pykrx~~ → **KIS Open API 단일 출처**로 통일. requirements 정리. `.env.example` 작성. (2026-05-06)
- [x] KOSPI/KOSDAQ 전종목 일봉 적재 (parquet, single file `data/daily/ohlcv.parquet`). default 1년치, `--years 5`로 backfill. (2026-05-06)
- [x] `python -m src.data.incremental_daily` — 종목별 last_loaded_date 기준 매일 갱신.
- [x] 종목 마스터 (`src/data/master.py`, `update_master.py`). KIS mst zip 파싱, 보통주(주권 'S' prefix) 필터, 우선주 토글. **시총/상장일은 미수집 (TODO)**.
- [x] WICS 섹터 매핑 크롤링 (월 1회) — `src/data/wics_crawler.py` + `src/data/update_wics.py`. wiseindex.com `GetIndexComponents` JSON, 대분류 10개(G10~G55). 35일 신선도 체크, --force 옵션. `data/meta/wics_sectors.parquet` (long, 1:1 매핑). 중분류(WI 28개)는 v1. (2026-05-10)
- [x] 네이버 금융 테마 매핑 크롤링 — `src/data/theme_crawler.py` + `update_themes.py`. 7일 신선도 체크, --force 옵션. `data/meta/naver_themes.parquet` (long format). (2026-05-06)
- [x] KRX 휴장일 — v0 는 weekday 기반 단순화 (`src/calendar_kr.py`). 공휴일은 fetcher 빈응답으로 자연 처리.
- [x] KIS Open API 인프라 (`src/kis/`): 토큰 발급/캐시/갱신, rate limiter (real 20cps / mock 2cps), KISClient.
- [x] 적재 데이터 무결성 체크 (`src/data/integrity_check.py`). 종목수 임계, 가격 이상치(±50%), 주말 적재 검증.

**완료 기준:** 임의 종목/날짜에 대해 일봉 데이터 즉시 조회 가능, 종목별 테마 리스트 조회 가능.

**현재 상태(2026-05-06):** 일봉 / 마스터 / 무결성 라인 완성. 테마/섹터 크롤러는 M2 진입 직전 작업 예정.

### Milestone 1: 장중 데이터 수집 (Week 2~3)

**목표:** KIS API로 장중 거래대금 순위와 종목 시세를 정해진 시점에 수집한다.

- [x] KIS API 인증 및 토큰 관리 — M0 완료
- [x] 거래대금 순위 조회 (1~50위) — `src/data/intraday.py` `fetch_volume_rank` (TR: FHPST01710000). (2026-05-06)
- [x] 종목 현재가/일중 고가/등락률 조회 — `fetch_quote` / `fetch_quotes_bulk` (TR: FHKST01010100). (2026-05-06)
- [x] 4시점 스냅샷 자동 수집 (11:00, 13:00, 14:00, 14:50) — `src/data/snapshot.py` + `src/scheduler.py`. (2026-05-06)
- [x] Rate limit 핸들링 (초당 20회) — M0 완료
- [x] 상한가 진입 감지 (실시간 또는 짧은 주기 폴링) — `src/jongbae/limit_up.py` `detect_new_limit_up`, 스케줄러 60초 폴링. (2026-05-06)

**완료 기준:** 장중 정해진 시점에 거래대금 30위와 각 종목의 시세가 자동으로 DB에 저장됨.

### Milestone 2: 종배 시그널 분석 (Week 3~4)

**목표:** 수집된 데이터로 종배 후보를 식별하고 historical 갭상 통계를 계산한다.

- [x] 주도테마 식별 알고리즘 — `src/jongbae/leading_theme.py` (거래대금 top 30, 테마 카운트 ≥3). (2026-05-06)
- [x] 종배 후보 필터 — `src/jongbae/candidates.py` (주도테마 + 일봉 +20%↑, 우선순위 limit_up/high_pull/normal/excluded). (2026-05-06)
- [x] Historical 유사 사례 매칭 — `src/jongbae/historical.py` Layer 1~3 구현. **Layer 4는 분봉 히스토리 부재로 v1 연기**. (2026-05-06)
- [x] 갭상 확률 / 평균 갭 / 중앙값 / 표준편차 계산 — `historical._gap_metrics`. (2026-05-06)
- [x] 사이징 계산 (균등 / Kelly / Sharpe) — `src/jongbae/sizing.py`. (2026-05-06)
- [x] 표본 부족 시 보수적 처리 — Kelly: n<5 제외, n<10 ×0.3, n<20 ×0.6, n≥20 ×0.8 (Half Kelly), 캡 25%. (2026-05-06)

**완료 기준:** 임의 시점의 시장 데이터에 대해 종배 후보 종목과 통계가 계산됨.

### Milestone 3: 레포트 생성 (Week 4)

**목표:** 정의된 마크다운 템플릿에 따라 레포트를 생성한다.

- [x] 모닝 레포트 (09:30) — `src/report/morning.py`. 시장 국면 지표 + 보유 종목 갭 결과. (2026-05-06)
- [x] 정기 레포트 (11/13/14) — `src/report/periodic.py`. 주도테마 변화 + 신규 상한가. **09:00~09:30 장초반 변화감지 알림 포함** (테마 변화/상한가 있을 때만). (2026-05-06)
- [x] **결정 레포트 (14:50)** ★ — `src/report/decision.py`. 종배 후보 + Historical + 사이징. 4096자 초과 시 종목 블록 분할. (2026-05-06)
- [x] **이벤트 알림 (상한가)** ★ — `src/report/event.py`. 짧은 푸시 포맷 (~300자). (2026-05-06)
- [x] 사후 레포트 (16:00) — `src/report/afterhours.py`. 후보 요약 + 시간외 단일가 + 데이터 적재 상태. (2026-05-06)

**완료 기준:** 모든 시점에서 레포트가 마크다운으로 생성되어 파일로 저장됨.

### Milestone 4: 알림 발송 (Week 4~5)

**목표:** 레포트를 텔레그램/이메일로 자동 발송한다.

- [x] 텔레그램 봇 생성 + 토큰 환경 변수 관리 — `.env` + `Settings.telegram_bot_token/chat_id`. (2026-05-06)
- [x] 텔레그램 발송 모듈 — `src/notify/telegram.py`. 4096자 자동 분할, tenacity 3회 재시도. (2026-05-06)
- [x] 메시지 우선순위 prefix — 레포트 생성기(M3)에서 🎯/🚨/📊/📝/⚠️ prefix 적용. (2026-05-06)
- [x] Gmail SMTP 발송 모듈 — `src/notify/email.py`. STARTTLS, 앱 비밀번호, 재시도 3회. (2026-05-06)
- [x] 발송 실패 시 재시도 로직 — tenacity (텔레그램 1~8초, 이메일 2~16초 지수 백오프). (2026-05-06)
- [x] 에러 알림 (시스템 자체 장애) — `send_error_alert()`. parse_mode=None(plain text)으로 이중 실패 방지. (2026-05-06)

**완료 기준:** 정해진 시점에 자동으로 텔레그램/이메일이 도착함.

### Milestone 5: 운영 안정화 (Week 5~6)

**목표:** 데몬으로 띄워놓고 손 안 가는 상태로 만든다.

- [x] systemd 서비스 등록 (`Restart=always`) — `deploy/jongbae.service` + `deploy/install.sh`. `StartLimitBurst=5/600s` 크래시 루프 방지. (2026-05-06)
- [x] 로그 로테이션 설정 — `deploy/logrotate.conf`. daily, rotate 30, compress, dateext. (2026-05-06)
- [x] Disk 사용량 모니터링 — `src/ops/health.py`. 디스크/일봉/테마/로그 크기 체크. `--send` 시 이상 항목 텔레그램 발송. `--json` cron 통합 지원. `tests/test_health.py` 22개 테스트. (2026-05-06)
- [x] 실수로 휴장일에 발송하지 않도록 캘린더 체크 — `src/scheduler.py` 각 잡 실행 직전 `is_business_day()` 가드. (2026-05-06)
- [ ] 1주일간 dry-run (실제 매매 X, 알림만 받음) 검증 — `deploy/dry-run-guide.md` 체크리스트 작성 완료. 실제 검증은 운영 투입 전 사용자가 수행.

**완료 기준:** 1주일간 사람 개입 없이 정상 동작.

### Milestone 5.5: 주도섹터/주도주 정의 재정립 (Week 6~7)

**배경:** Sonnet이 만든 "거래대금 30위 ≥3종목" 단일 룰은 대형주(하이닉스/삼전) 편향이 심해 단타 주도주 판별로 부적합. 한국 단타 실무 통설(테마별 상승률 + 시총 대비 회전율 + breadth)에 맞춰 재정립한다. R3, R3'(신설) 참고.

- [ ] **시총 데이터 적재** — KIS mst part2 추가 필드 파싱. `src/data/master.py` 확장. 회전율(거래대금/시총) 계산용 필수
- [ ] **ETF/ETN/리츠/스팩/펀드 필터** — KIS 종목분류 코드 + 종목명 패턴(`KODEX`/`TIGER`/`KBSTAR`/`ARIRANG` 등) + 코드 패턴(`1XXXXX` 펀드, `5XXXXX` 스팩). `src/data/master.py` `is_tradable_for_jongbae()`. 기존 `100030` 펀드 누락 이슈 같이 해소
- [ ] **주도섹터 정의 변경** — 단순 "30위 내 ≥3종목" → 테마별 (a) breadth(테마 내 +5%/+10% 종목 수) + (b) 평균 상승률(동일가중) + (c) 회전율 합계 z-score 합산. `src/jongbae/leading_theme.py` `score_themes()`. 임계값/가중치는 운영 튜닝 항목
- [ ] **주도주 정의 변경** — 주도섹터 내 **회전율 1위** = 주도주. 거래대금 절대값 X (대형주 자동 배제). 상승률/거래대금은 표시만. `identify_early_morning_leaders` 시그니처 변경
- [ ] **주도주 교체 상태 머신** — `NORMAL` / `TRANSITION` (교체 가능성) / `GRACE` (실제 교체 후 5분 유예). `src/dashboard/state.py`. 임계값 R3' 참고
- [ ] **plan/jongbae-strategy/data-infra/report-spec/CLAUDE 메타 갱신** ← 본 작업 시작 전 선행

**완료 기준:** 데모 모드(2025-05-04 제룡전기 케이스)에서 새 정의로 주도섹터="전기/전선", 주도주="제룡전기"가 정상 식별. 하이닉스/삼전이 (단타용) 주도주에 안 잡힘.

### Milestone 6: 실시간 모니터링 대시보드 (Week 7~8)

**목표 (round 18):** 사용자 `/on`/`/off` 토글 (24h 허용). 평일 09:00 자동 ON 은 편의용, 10:30 자동 OFF 폐지. 봇 명령 polling thread 는 데몬 시작 시 1회 띄워 24h 상시 가동. 주도주 + 사용자 관심 종목을 1~2초 간격 텔레그램 메시지 편집 갱신.

**[Fetcher / 기본 인프라]**

- [ ] **분봉 fetcher** — KIS API `FHKST03010200`. 5분봉/1분봉 OHLC + 거래대금 시계열. `src/data/intraday.py` `fetch_minute_bars()` (R11 가속 / R12 봉패턴 공용)
- [ ] **체결강도 fetcher** — KIS API `inquire-ccnl`. 매수체결/매도체결 비율 → R10 VP. `fetch_ccnl_strength()`
- [ ] **호가잔량 fetcher** — KIS API `inquire-asking-price-exp-ccn`. 매수/매도 호가 잔량 (R10 보조 강등). `fetch_asking_price()`
- [x] **투자자별 순매수 fetcher** — KIS API `inquire-investor`. 외국인/기관/프로그램 순매수. `fetch_investor_flow()`. round 22 정정으로 카드에서 제거 → round 36 응답 list 파싱 안전화 (`out[0]` → 시간 필드 max 행 채택) + 모두 0 응답 DEBUG 로그 + 카드/PWA/14:50 결정 레포트 라인 부활 (R14 점수 합산 X, 표시만). 진단 스크립트 `scripts/diag_investor_flow.py` 동봉. (2026-05-17)
- [ ] **거래대금 가속배율 계산** — 현재 5분봉 거래대금 / 직전 30분 평균 (R3' 주도주 교체용). `src/jongbae/momentum.py`. 양수→치고 올라옴 / 음수→자금 이탈
- [ ] **상태 머신** (M5.5와 공유) — 종목 추가/제거/유예기 카운트다운

**[R10~R15 매수 점수/매도 트리거 신규]**

- [ ] **R10 체결강도(VP)** — `src/jongbae/volume_power.py`. VP + 5MA/20MA. KIS `inquire-ccnl` `체결강도` 필드 직접 사용. 메모리 deque 시계열
- [ ] **R11 다중 윈도우 거래대금 가속** — `momentum.py` 확장. `vol_accel_1m` (분모 5분), `vol_accel_5m` (분모 20분). 기존 30분 분모 가속배율은 유지
- [ ] **R12 봉 패턴 분석** — `src/jongbae/candle.py`. 5분봉 OHLC → bullish/bearish/doji + upper_wick/lower_wick 비율
- [ ] **R12.5 위치/맥락** — 당일 고점 / 시초 / 전일 종가 거리 %. VI 발동은 v0 휴리스틱(분봉 ±10% 1분 내), v1 정밀
- [ ] **R13 가격-체결강도 다이버전스** — `src/jongbae/divergence.py`. bearish/bullish 자동 감지
- [ ] **R14 매수 점수 grader** — `src/jongbae/grader.py`. score 계산 + 등급(STRONG/WATCH/NEUTRAL/AVOID) + 사유 텍스트 + 필수조건 체크
- [ ] **R15 매도 트리거 + 상태 머신** — `src/jongbae/exit_triggers.py`. 감시/보유 모드, 트리거 A/B/C, 멱등성(B1/B2 1회만)
- [ ] **보유 상태 영속화** — `data/state/holdings.json` atomic write. `/buy`/`/sell` 명령 시 갱신, worker 재시작 시 로드
- [ ] **R10/R12 메모리 시계열** — `src/dashboard/state.py` 확장. `intraday_series[code]` deque 구조 (data-infra.md 참조)

**[Telegram 봇 / 메시지 인프라]**

- [x] **Telegram 양방향 봇** — long polling으로 incoming 메시지 수신 (24h 상시, round 18). 명령어: `/on`(=`/start`), `/off`(=`/pause`), `/list`, `/clear`, 6자리 숫자(토글), **`/buy CODE [PRICE] [TIME_STOP_MIN]`** (round 20 — 가격 생략 시 최근 시세 자동 보충), **`/sell CODE`**, **`/status CODE`**. `src/notify/telegram_bot.py`
- [ ] **메시지 편집 인프라** — `editMessageText`로 종목당 메시지 1개 유지 갱신. 종목 1~2개=2초, 3~5개=3초, 6~10개=5초 동적 간격. `src/notify/telegram.py` 확장
- [x] **알림 통합 — 카드 외 푸시 폐기 + 부상 후보 TTL 폐지 (round 19)** — round 17 정책 실코드 반영. `worker._send_alert` 함수 + 호출 5곳 제거 (RISING 신규 / 강한 부상 / 자금 이탈 / 1분봉 부상·급감 / 호가 역전 / step_tracker TRANSITION·REPLACEMENT). 카드 재배치(reposition) 로직 제거 — alert 가 없으니 카드가 위로 밀려나지 않음. `MonitoredStock.expires_at` + `prune_expired` 제거, RISING 동기화는 풀-이탈 즉시 제거로 전환 (시간 만료 없이 자연 교체). `step_tracker` 반환형 `None` 로 변경, TRANSITION/GRACE 는 `render_monitor_message(transition_info=...)` 로 a1 카드 헤더에 통합 표시. 5분봉/1분봉 가속 라인에 strong_rise/exit_signal/one_min_rise/one_min_exit 임계 도달 시 ⚡/⚠ 마크 강조. (2026-05-14)
- [x] **부상 후보 다단계 funnel — R14 매수 점수 기반 재정의 (round 21)** — `identify_rising_candidates` 가 회전율 상위 5 → 15 로 확장(Stage 1). worker 에 `_evaluate_rising_funnel` 신설 — Stage 2 (minute_bars + vol_accel + is_weak_candle) → Stage 3 (ccnl + VP) → Stage 4 (asking + investor + `calculate_buy_score`) 깔때기. tick_cache 로 통과 종목 fetch 결과 보관해 카드 렌더에서 재사용. `MonitoredStock.buy_score/buy_grade/buy_reasons` 필드 추가, render 헤더에 등급 이모지 + 점수 + 사유 한 줄 표시. 흥아해운류는 Stage 2 모멘텀 임계에서 drop (회귀 테스트 `test_rising_funnel_filters_heunga_haewoon` 통과). 평균 ~33 KIS req/3sec tick (한도 60의 55%). worker docstring 5초 → 3초 정정. (2026-05-14)
- [x] **감시/보유 카드 렌더러 통합 (round 22)** — `render_monitor_message` 에 `holding / trigger_states / vp_5ma / vp_1ma / divergence` 인자 추가. 보유 모드: `[보유]` prefix (source emoji 중복 제거), 합쳐진 시각/가격 라인 (`시각 (+경과초) 현재가(오늘%)/매수가(손익%)`), 청산 시그널 섹션 (C1~C5 각각 ❌/✅ + 현재 수치). worker.dashboard_tick 매 tick `load_holdings()` + `evaluate_triggers()` 호출. `MonitoringSession.vp_series` 신설로 종목별 VP 시계열 메모리 유지, `ma_1/ma_5/ma_20` 산출. 체결강도 라인에 5MA + 1MA 동시 표시. 외국인/기관/프로그램 라인 제거 (데이터 신뢰도 낮음). 초보자용 가이드 `docs/monitoring-guide.md` 신규 작성. (2026-05-14)
- [x] **자동 운영 시간 (round 18 정책 변경)** — 평일 09:00 자동 ON 유지. 10:30 자동 OFF **폐지** — `/off` 로만 종료. 사용자가 임의 시점에 `/on`/`/off` 토글 (24h 허용). 휴장일/주말 `/on` 도 허용하되 KIS 시세 변동 X 로 카드는 정적 유지
- [ ] **장 시간 외 안내** — 시간 외 사용자 입력 시 "장 시간 외입니다" 안내 한 줄
- [ ] **임계값 설정** — `src/jongbae/config_thresholds.py`. R10~R15 임계값 일괄 관리. 운영 중 사용자 피드백으로 튜닝

**[테스트]**

- [ ] **상태 전이 / 명령 파싱 / 임계 트리거 / rate limit 핸들링** (기존)
- [x] **R14 회귀 — 흥아해운 시나리오** — `tests/test_grader.py`. 입력(거래대금 1316억 1위, 회전율 +19.4%, vol_accel_5m=0.8, vol_accel_1m=0.4, 호가 5.3배, 윗꼬리 음봉, VP=95, VP_5MA=98) → 점수 ≤ -3, 등급 🔴 AVOID
- [x] **R14 회귀 — STRONG 케이스** — 제룡전기 상한가 모멘텀(VP=142, vol_accel_5m=1.6, 장대양봉) → 점수 ≥ 5
- [x] **R15 트리거 멱등성** — B1 익절 1차는 1회만 발화, A1 손절선은 매 tick 발화 가능
- [x] **R12 봉 패턴 경계** — 윗꼬리 30%/40%/50% 경계, doji
- [ ] **추가 회귀 케이스 5~10건** (사용자 과거 사례 입력 필요 — TODO, ritual 1 참조)

**[round 23~30 — 통설 검색 기반 R14/R15 보강 (2026-05-14)]**

- [x] **R14a VWAP 시그널** (round 23, P0-1) — `momentum.compute_vwap` + `price_vs_vwap_pct`. `GraderSnapshot.price_vs_vwap_pct`. ±0.3% 임계. test_grader 7 + test_momentum 9 케이스
- [x] **R14b 5/20 이평 시그널** (round 24, P0-2) — `momentum.compute_minute_ma` + `price_vs_ma_pct`. `GraderSnapshot.price_vs_ma5_pct/ma20_pct`. 정/역배열 ±1. test 8 + 9
- [x] **R14c 상한가 진입 시간 가산** (round 25, P1-1) — `GraderSnapshot.limit_up_hit_time: dt.time | None`. 09:30 이전 +1 / 10:30 이전 +0.5. test 7
- [x] **R15 A5 EOD 컷오프** (round 26, P1-2) — 14:45 이후 가격<MA AND 음봉 → 강제 청산. test 6
- [x] **R13 다이버전스 ±2 → ±1 강등** (round 27, P2-1) — 통설 외 약신호. test 2
- [x] **R14d 거래량 비율 검증** (round 28, P2-2) — 전일 대비 1~3배 +0.5 / 10배↑ -1. test 8
- [x] **R29 거래원 분석 KIS API 가용성 조사** (round 29, P3-1) — fetch_investor_flow 가용성 확인, R14 가산은 검증 후 결정 (`data-infra.md` "투자자별 순매수 R14 추가 가능성" 섹션)
- [x] **R7' 종배 청산 시초가 룰** (round 30, P3-2) — 신규 모듈 `src/jongbae/jongbae_exit.py`. ≤+1% 전량 / +1%~+6% 익절 / ≥+6% 40% 분할. test 13
- [x] **wiring: worker → grader** (round 32) — funnel 에서 VWAP/MA5/MA20 자동 계산 + `volume_ratio_vs_prev_day` (`_prev_day_volume` 헬퍼 + daily_ohlcv 인자) + `limit_up_hit_time` (`session.limit_up_hit_times` dict 경유). scheduler 의 상한가 감지 2 지점에서 시각 저장
- [x] **wiring: scheduler → jongbae_exit** (round 32) — `_send_jongbae_open_exit_recommendation` 09:01 cron + `Dispatcher.send_jongbae_open_exit`. holdings.json 비면 no-op
- [x] **ritual 2 자동화: paper_trade 기록기** (round 32) — `src/jongbae/paper_trade.py` 신규. `PaperTradeRecord` dataclass + `record_decision/record_open_result/load_records/compute_summary` (Spearman ρ 자체 구현). atomic write. test 15
- [x] **ritual 3 자동화: 통설 가중치 invariant** (round 32) — `test_grader.py::test_invariant_consensus_weights_dominate_positive/negative` + `_divergence_weight_capped_at_one` 3 케이스
- [ ] **wiring: 14:50 결정 → paper_trade.record_decision** — 결정 레포트에서 STRONG/WATCH 자동 저장 (호출 한 줄)
- [ ] **wiring: 09:30 모닝 → paper_trade.record_open_result** — 보유 종목 + 14:50 후보들 시초가/오전고가 추가 (호출 한 줄)
- [x] **카드 일관성 + funnel 데이터 부재 복구** (round 33) — 사용자 보고: "체결강도 안 나옴 / 30분 동안 부상 후보 0건 / 보유 카드에 WATCH·STRONG 안 보임 / C3 ❌ 인데 0.0배라 혼동". fix: ①`render.py` 체결강도 라인 `if ccnl:` 가드 폐지 — 데이터 부재 시 `⚪ 체결강도: — (데이터 없음)` placeholder 라인 항상 출력. ②`worker.dashboard_tick` 종목 루프 안에 `GraderSnapshot` + `calculate_buy_score` 매 tick 호출 — AUTO/MANUAL/HOLD/RISING 전부에 buy_score/grade/reasons 채움. 카드 헤더 등급 라벨 일관성. 입력은 이미 fetch 한 값 재사용 — KIS 추가 호출 0. ③`_evaluate_rising_funnel` Stage 3 — KIS `cttr` 빈 응답(NaN/None) hard-fail 폐지. 명시적으로 100 미만일 때만 drop, NaN 은 Stage 4 풀스코어로 통과 (VP 가산점 0). ④`render.py` C3 라벨에 보유 모드 한정 `(2분 지속)` 명시 — 감시 모드는 instantaneous 라 기존 라벨. 발화 룰과 라벨 일치. ⑤funnel/leader 단계별 통과 종목 수 로깅 — 사용자가 "왜 안 나오는지" 진단 가능. tests: render 4 신규 + worker 2 신규, 기존 회귀 안전 (test_rising_funnel_filters_heunga_haewoon 유지). 764 pass.
- [x] **체결강도 KIS 필드명 정정** (round 34) — round 33 의 "체결강도 데이터 부재" 근본 원인 추적. 사용자 운영 보고에서 모든 종목 체결강도 NaN. KIS 공식 샘플(koreainvestment/open-trading-api `chk_inquire_ccnl.py` COLUMN_MAPPING) 확인 → 우리 코드의 `cttr` / `seln_cntg_smtn` / `shnu_cntg_smtn` 필드명은 추정 오류, 응답에 존재 X. 실제 `inquire-ccnl` (FHKST01010300) 응답 7개 필드: `stck_cntg_hour` / `stck_prpr` / `prdy_vrss` / `prdy_vrss_sign` / `cntg_vol` / **`tday_rltv` (당일 체결강도)** / `prdy_ctrt`. fix: ①`fetch_ccnl_strength` 의 `cttr` → `tday_rltv` 정정. ②응답 구조도 `output1` 단일 dict 추정에서 → `output` 의 30 체결 행 list 중 `stck_cntg_hour` 최대 행 (최신 체결) 채택. ③매수/매도 누적 체결량은 본 API 에 없어 `buy_volume`/`sell_volume` 키 제거, `buy_ratio` 는 NaN 반환. ④NaN 응답 진단 DEBUG 로그 추가 — KIS 가 빈 값을 반환하는 종목/시점 추적 가능. tests: `test_intraday_realtime.py` 6 케이스 재작성 (실제 KIS 응답 스키마 mock + 최신 행 선택 + output1 legacy fallback + 빈 응답 NaN). 766 pass.
- [x] **등급 라벨 50위 밖 종목에도 적용** (round 35) — 사용자 보고: "보유 / 수동 모니터링 어느 경우든 등급(WATCH/STRONG) 안 뜸". 진단: round 33 의 `if snap_row is not None:` 가드가 거래대금 50위 밖 종목을 grade 계산에서 skip. 보유/수동 종목이 50위 밖이면 (자주 발생 — 사용자가 직접 들여온 종목) `monitored.buy_grade=None` → 카드 라벨 X. fix: ①`worker.dashboard_tick` 종목 루프 안 grade 계산 가드 폐지. snap_row 가 None 이어도 bars/ccnl/asking 같은 다른 fetch 결과로 부분 점수 계산 — grader 입력은 모두 NaN-safe. ②가격은 `snap_row.price > bars 마지막 close > 0` 순으로 fallback — 50위 밖 종목도 VWAP/MA/divergence 계산 가능. ③`intraday_high` 도 bars 의 max(high) 로 fallback. ④rank 0/None 이면 회전율 가산 (+1) 만 skip, 나머지 시그널은 정상 평가. tests: `test_grade_assigned_to_manual_stock_outside_top50` + `test_grade_assigned_to_holding_stock_outside_top50` — 둘 다 snap 에 없는 종목이지만 bars 모멘텀으로 grade 채워짐. 768 pass.

**완료 기준 (round 18):** 24h 봇 명령 polling 상시 가동. 사용자 `/on` 시점부터 `/off` 까지 주도주 1~2개 + 사용자 임의 종목 모니터링 + 보유 종목 손절/익절 카드 표시. 평일 09:00 자동 ON, 10:30 자동 OFF 폐지. 카드 외 별도 푸시 알림 X (round 17). 푸시는 M6 외부 이벤트(상한가 진입, 자동 주도주 첫 추가, 정기 레포트)만.

**정책 확인 (CLAUDE.md `자동 매매 절대 금지`):** R15 매도 트리거는 카드 표시 전용. 텔레그램 별도 푸시 X, KIS 실주문 자동 등록 X. 손절 자동화 / 분할 익절 자동화는 영구 미지원.

### Milestone 7: PWA 대시보드 (Week 8~)

**배경:** 텔레그램은 한 화면에 동시 표시 가능한 메시지 갯수에 한계가 있어 종목 6~10개를 아이패드 한 화면에 한눈에 보기 어렵다. M6 카드를 그대로 재현하되 화면 1장에 다 보이는 PWA 대시보드를 추가. 텔레그램 봇은 이벤트 푸시(상한가 진입/14:50 결정/16:00 사후) + 명령 백워드 호환용으로 점진 축소. 상세 사양은 `docs/dashboard-pwa.md`.

**핵심 정책 (CLAUDE.md `자동 매매 절대 금지` 유지):**

- PWA → 서버 input: **holdings.json 토글 / 종목 추가 제거 / `/on`·`/off` 만 허용** (텔레그램 봇 명령과 동일 effect)
- PWA → 서버 input: **KIS 거래소 주문 영구 X**. KIS 실주문 코드 작성 X
- 서버 → PWA: 모니터링 카드 + 시계열 push only
- M6 카드 렌더 (`render.py`) + holdings.json + worker 와 데이터·로직 공유. 텔레그램·PWA 는 채널만 추가, 핸들러는 한 군데
- 데이터 외부 클라우드 송신 X — 집 데스크탑에서 직접 서빙

**[Phase 1: 로컬 MVP]**

- [x] **FastAPI 서버 셋업** — `src/dashboard/api.py` `create_app(session, broadcast_interval_sec=1.0)`. WS `/ws/monitor` (snapshot on connect + payload_ts 변경 시 tick broadcast), REST `/api/health` `/api/snapshot` `/api/holdings` (buy/sell) `/api/session` (on/off) `/api/watchlist` (toggle/clear). 정적 `/static/*` + `/` → index.html. `tests/test_dashboard_api.py` 16 케이스. (2026-05-14)
- [x] **카드 JSON 페이로드 생성** — `src/dashboard/render.py` `build_monitor_payload()`. NaN/Inf → None sanitize, DivergenceState.bearish/bullish → kind 문자열, LeaderState enum → value. `MonitoringSession.last_payloads` 필드에 worker tick 마다 갱신, stale 종목 자동 정리. (2026-05-14)
- [x] **trigger_lines 페이로드 + 청산 시그널 카드 표시** — `build_trigger_lines()` 헬퍼로 텔레그램/PWA 공용. C1~C5 텍스트 줄 list (현재 VP/가속 수치 포함). PWA 가 코드명 (`C1_vp_below_100`) 만 보여주던 문제 해결, 텔레그램과 동일 인지 정보 (2026-05-15)
- [x] **수동 전환 / 해제 버튼** — 자동/부상 카드 `[→ 수동]`, 수동 카드 `[× 해제]`, 보유 카드 `[✕ 청산]`. 모두 기존 `apply_command` (`toggle_code` / `sell`) 핸들러 재사용 (2026-05-15)
- [x] **그룹 컬럼 폐지** — 카드에 source 라벨 + 좌측 보더 색상 이미 있어 중복. 단일 그리드 + source priority sort (보유 → 자동 → 부상 → 수동) 후 점수 내림차순 (2026-05-15)
- [x] **WebSocket broadcast** — `session.last_payload_ts` 변경 감지 polling 1초 (worker tick 3초 + 1초 lag). 변경 시 전체 snapshot 송신 (diff 미구현, 페이로드 작아 OK)
- [x] **REST 핸들러 = telegram_bot 핸들러 재사용** — `Command(kind=...)` 직접 생성 후 `apply_command(cmd, session, now_kst())` 호출. `_apply_buy` / `_apply_sell` / `add_manual` / `set_on/set_off` / `remove_manual_all` 한 군데서만 — 이중 구현 X
- [x] **정적 HTML** — `src/dashboard/static/{index.html,app.js,manifest.json,icon.svg}`. Vanilla JS + Tailwind CDN. 종목별 카드 그리드 + 그룹 4컬럼 (보유/자동/부상/수동) + 보유 등록 모달 + 6자리 코드 추가 input + /on /off 버튼 + WS 자동 재연결 지수 백오프
- [x] **scheduler 통합** — `src/scheduler.py` `run()` 에 `DASHBOARD_PWA_ENABLED=1` 환경변수 가드로 uvicorn 별도 daemon thread 시작. `DASHBOARD_PWA_HOST` (기본 127.0.0.1) / `DASHBOARD_PWA_PORT` (기본 8000). shutdown 시 `pwa_server.should_exit=True` graceful. 실패 시 텔레그램 단독 fallback (fail-loud) (2026-05-14)
- [x] **로컬 검증 (demo)** — `python -m src.dashboard.serve_demo` 신규. mock session 에 1초 간격 데모 페이로드 갱신, FastAPI 만 띄워 PWA UI 검증. 실제 KIS/텔레그램 없이 카드 그리드 + WS broadcast + REST endpoints 동작 확인 완료 (2026-05-14). 실제 KIS 연결 검증은 사용자 데스크탑에서 `DASHBOARD_PWA_ENABLED=1 ./go serve` 로 수행

**[Phase 2: 외부 접근]**

- [ ] **Tailscale 설정** — 데스크탑 + 아이패드 + 폰 한정. 도메인/Cloudflare 불필요. FastAPI 는 `127.0.0.1` + Tailscale 인터페이스만 bind (`0.0.0.0` 금지)
- [ ] **HTTPS** — Tailscale MagicDNS / `tailscale serve` TLS
- [ ] **PWA manifest + Service Worker** — 아이패드 홈화면 풀스크린 등록. offline cache 는 정적 자산만 (데이터는 WS)
- [x] **WebSocket 자동 재연결** — 지수 백오프 1s/2s/4s/8s/cap 30s (`app.js`). 재연결 시 snapshot 재수신
- [x] **stale 표시** — `state.lastSnapshot.updated_at` 기준 1초 주기 self-check, 10s 초과 시 헤더 `⚠ stale (Ns)` 호박색 (2026-05-15)

**[Phase 3: 보유 토글 버튼 UI]**

- [ ] **카드 상단 토글 버튼** — 보유 X 시 `[+ 보유 등록]`, 보유 시 `[✕ 청산 처리]`. 클릭 시 confirm dialog
- [ ] **보유 등록 modal** — 가격 input (기본 현재가 자동 보충 / 수동 override) + TIME_STOP_MIN 옵션 input
- [ ] **즉시 카드 모드 전환** — POST `/api/holdings` 응답 후 worker tick 기다리지 않고 optimistic UI. 서버 broadcast 도착 시 reconcile
- [ ] **텔레그램 동기화 검증** — PWA 에서 보유 등록 → 텔레그램 카드도 [보유] 모드 전환되는지 확인
- [x] **장 시간 외 가드** — `_apply_buy` 가 KRX 정규장(평일 09:00~15:30) 외 등록 시 안내 한 줄 추가 ("⏸ 장 시간 외 — 다음 정규장부터 시그널 평가 시작"). 등록은 진행. `_is_regular_session()` 헬퍼 + 3 테스트 케이스 (2026-05-15)
- [ ] **수동 종목 추가** — 6자리 코드 입력 input → POST `/api/watchlist` → 텔레그램 `6자리 숫자` 토글과 동일 핸들러

**[Phase 4: UX 개선 — 선택]**

- [ ] **종목 그룹 컬럼** — 자동(주도주) / 부상(RISING) / 보유(HOLD) / 수동(MANUAL) 4 컬럼. 가로 화면 그리드
- [ ] **카드 클릭 → 상세 펼침** — R14 사유 전체, R15 트리거 A/B/C 상세, 시계열 미니차트
- [ ] **시계열 미니차트** — 가격 / VP / 회전율 / accel sparkline. 초안: 텍스트 sparkline (의존성 0). 정밀: lightweight-charts CDN (Phase 4 후반)
- [ ] **음소거된 푸시 (opt-in)** — 새 STRONG 등급 진입 시 Web Notifications. 기본 OFF
- [ ] **R15 트리거 강조** — 청산 시그널 발화 시 카드 빨간 펄스
- [ ] **세션 토글 UI** — `/on` / `/off` 버튼 상단 고정. 현재 세션 상태(ON/OFF + 활성 종목 수) 항상 표시

**[Phase 5: 운영 안정성]**

- [ ] **WebSocket 끊김 → 텔레그램 에러 알림** (fail-loud). 5분 이상 클라이언트 0명일 때만 1회 (스팸 방지)
- [ ] **systemd 통합** — `deploy/jongbae.service` 가 FastAPI 도 같이 띄움 (단일 프로세스)
- [ ] **`/api/health` endpoint** — worker 마지막 tick 시각 / KIS 토큰 유효성 / holdings.json 존재 여부. cron 또는 외부 monitor 용
- [ ] **인증** — Tailscale 자체 인증으로 충분. 별도 토큰 X. 본 정책은 디바이스가 늘어나면 재검토

**[데이터 영속화 — 복기 도구와 공유]**

- [ ] **분봉 시계열 parquet 적재** — `data/intraday_series/YYYY-MM-DD/CODE.parquet`. worker tick 시 메모리 deque → 1~5분 주기 flush. M7 미니차트 + 복기 도구(L269~) + 향후 백테스트 공통 소스
- [ ] **시총 데이터 적재 (M5.5 선행 활용)** — 회전율 정확도 향상. PWA 회전율 표시 일관성

**완료 기준 (M7 v0):** 아이패드 홈화면 풀스크린에서 종목 6~10개 카드 + 보유 토글 버튼 동작. 텔레그램과 동일 데이터, 같은 1~3초 갱신. PWA 에서 보유 등록 시 텔레그램 카드도 동기화. `/on` / `/off` 양방향 동작. KIS 주문 input 부재 확인.

**확정 사항 (2026-05-14):**

1. **외부 접근**: Tailscale (본인 디바이스 한정)
2. **인증**: Tailscale only (별도 토큰/SSO 없음)
3. **차트 라이브러리**: 텍스트 sparkline → Phase 4 후반 lightweight-charts CDN
4. **분봉 영속화 범위**: 모니터링 종목만 (`data/intraday_series/YYYY-MM-DD/CODE.parquet`)
5. **텔레그램 봇 위상**: 동시 운영 (이벤트 푸시 + PWA fallback)
6. **트랙 순서**: M5.5 / 분봉 영속화와 **분리**. **Phase 1 MVP 먼저** (M6 메모리 deque 그대로 사용) → 아이패드 dogfooding → M5.5(시총)·분봉 영속화는 후속 트랙. 복기 도구 컨셉 정해질 때 분봉 영속화 같이 진행

상세는 `docs/dashboard-pwa.md` §7.

---

## v0 범위 정의 (명시적)

### 포함

- KOSPI + KOSDAQ 전종목 대상
- 4시점 스냅샷 + 상한가 진입 이벤트
- 네이버 테마 기반 주도테마 식별
- 일봉 +20%↑ 종배 후보 추출
- 4-Layer historical 매칭
- 균등/Kelly/Sharpe 사이징 표시
- 텔레그램 + 이메일 알림

### 제외 (TODO/v1+)

- **NXT 청산 로직** (08:00~08:50 프리장 활용) → v1
- **백테스트 엔진** → 데이터 6개월 적재 후
- **주도주 매매 모듈** → 종배 안정화 후
- **스윙 매매 모듈** → 종배 안정화 후
- **자동 매매 실행** → 영구 제외 (사람이 직접 함)
- **현차 같은 종목 제외 룰** → 정량화 미정
- **시총/거래대금 절대 임계값** → 적용 X, 레포트에 표시만
- **장중 정밀 분봉 데이터** → 데이터 확보 어려움, v0는 시점별 스냅샷만

---

## 미해결 / 추후 결정 항목

레포트 상단에 항상 노출시켜 Zeta가 직관 판단할 부분:

1. **대세상승장 판정** — 자동 + 사용자 직관 병행
   - KOSPI 200일 이평 위/아래
   - KOSPI 60일 수익률
   - VKOSPI 변동성 지수
   - 직전 20거래일 음봉 비율

2. **주도테마 임계값** — 거래대금 30위 내 동일 테마 ≥3개 (튜닝 가능)

3. **사이징 방법 선택** — Kelly가 좋은지 Sharpe가 좋은지는 데이터 누적 후 검증

4. **상한가 진입 시각 컷오프** — 14:00 이전만 인정할지 등

5. **지속적 데이터 누적** — 6개월~1년 후 백테스트로 룰 검증

---

## Phase 1 — tick-level 시그널 로깅 (round 38, 2026-05-18 시작)

매직 넘버 튜닝 인프라의 1단계. 사용자(Zeta) 비전: "수익률·승률 높이는 매직 넘버를
데이터로 찾는다, 당분간 과도하더라도 최대한 많이 남긴다". 자세한 정정 이력은
`docs/jongbae-strategy.md` row 38.

- [x] `src/data/tick_log.py` — TickLogRow dataclass (40+ 컬럼) + append jsonl + TradeEvent
- [x] `src/data/tick_log_compact.py` — jsonl → parquet 변환 CLI
- [x] `worker.dashboard_tick` 매 tick 호출 (monitored 풀 + Stage 0 통과 비-monitored 합집합)
- [x] `notify/telegram_bot.py` /buy /sell 핸들러에 trade event append
- [x] `.gitignore` data/tick_logs/ + data/trades/
- [x] 테스트 12 신규 (`test_tick_log.py`)
- [x] scheduler 16:15 자동 jsonl → parquet 변환 cron (`scheduler._compact_tick_logs_today`) — round 39
- [x] Phase 2 분석 도구 — `python -m src.analysis.replay CODE DATE` / `python -m src.analysis.regret DATE` (round 39, `test_analysis.py` 7 신규)

## Phase 2 운영 — 매매일지 (round 40, 2026-05-18 시작)

사용자(Zeta) 비전 (round 40): "매매일지를 Claude Code 새 세션에서 차트 + 시스템
데이터로 받아보고 싶다. 단순 자동 레포트가 아니라 다각도 평가 + 튜닝 포인트까지".

- [x] `docs/trading-journal.md` — 매매일지 작성 가이드 (입력 / 워크플로우 / 출력
  형식 / 톤 / 단기·장기 튜닝 포인트 / 운전수 가설 시그니처 후보)
- [x] `CLAUDE.md` "매매일지 요청 처리" 섹션 — 사용자 트리거 키워드 ("매매일지 작성
  해줘", "오늘 매매 평가") → `docs/trading-journal.md` 자동 참조 워크플로우 박음
- [ ] `data/journal/YYYY-MM-DD.md` 디렉토리 운영 — 매매일지 누적 (사용자가 Claude
  출력 검토 + 보완 후 저장)
- [ ] 매매일지 N건(20+) 누적 후 메타 분석 — 반복 등장하는 튜닝 후보 추출 → ritual
  통과 시 R14 가중치 / R15 임계 정식 변경

## Phase 2 확장 — sensitivity backtest (데이터 1~3개월 후)

- [ ] R14 가중치 sensitivity — 기존 매매일지의 시그널을 다른 가중치로 재평가 시
  매수 결정이 어떻게 달라지나? 그 결과는 어땠나?
- [ ] R15 트리거 임계 sensitivity — 트리거 발화 시점을 가중치별로 시뮬레이션 →
  사용자 매도 시점과 비교
- [ ] funnel 통과 종목 vs 탈락 종목의 다음날 결과 분포 — Stage 0~4 컷오프 재검토

## Phase 3 (장기) — 종목별 파라미터 DB

운전수 가설 (`memory/project_long_term_vision.md`): 한국 증시는 종목마다 운전수
운용법이 다름 (양봉 누적형 / 개미털기형 등). 종목별 R14 가중치 / R15 임계가 조금씩
달라야 한다는 가설. 데이터 충분 (1년+) + Phase 2 메타 분석 통과 후 진입.

- [ ] 운전수 가설 시그니처 정량화 — 종목별 (a) 갭상 빈도, (b) +30% 도달 시각 분포,
  (c) 분봉 자기상관, (d) 거래대금 spike 후 회복 시간 등 marker 계산 + 군집화
- [ ] 같은 시그니처 군집의 가중치 sensitivity — 군집별 글로벌 가중치 도입 (개별
  종목 fitting 은 과적합 위험으로 보류)
- [ ] `data/params/CODE.json` 종목별 R14 가중치 / R15 임계 override (1년+ 데이터
  + 군집 패턴 안정화 후)
- [ ] grader / exit_triggers 가 `params/CODE.json` fallback 로직 추가

## 기술 부채 / TODO 메모

코드 작성하면서 발견되는 것 누적:

- [x] KIS API 토큰 만료 (24시간) 자동 갱신 — `src/kis/auth.py` 만료 5분전 갱신
- [ ] 수정주가 vs 원주가 일관성 (분할/배당 시) — daily fetcher 는 `adjusted=True` 일관 사용
- [ ] 종목 코드 변경 (액면분할, 합병) 처리
- [ ] 테마 매핑 변경 시 historical 데이터 재계산 필요한지
- [ ] 텔레그램 메시지 길이 제한 (4096자) 분할 발송
- [ ] **종목 마스터 시가총액 / 상장일 미수집** — KIS mst part2 추가 필드 파싱 필요. 현재 0 / None
- [ ] **`100030` 등 1XXXXX 주권형 펀드/리츠** — KIS 그룹코드 'S' 에 포함되어 보통주 필터로 안 걸러짐. 종목명 패턴 또는 part2 필드 분기 필요
- [ ] **WICS / 네이버 테마 크롤러** — M2 진입 직전 작업
- [x] **무결성 체크 알림 채널** — `python -m src.data.integrity_check --send` 옵션 추가. FAIL/WARN 항목 텔레그램 에러 알림으로 발송 (Dispatcher.telegram_error). cron 통합 가능. (2026-05-10)
- [ ] **R5 Layer 4 (고점 도달 시각 매칭)** — 분봉 히스토리 부재로 v0 미구현. 매일 분봉 적재 후 v1에서 구현
- [x] **종배 시그널 통합 파이프라인** — `src/pipeline.py` `run_pipeline()`. demo 모드 (--demo), 저장 (--save), 발송 (--send). `src/demo_fixtures.py` 제룡전기 2025-05-04 mock. `tests/test_pipeline.py` 13개 E2E 테스트. (2026-05-06)
- [x] **09:00~10:00 장 초반 고주파 모니터링** — `src/scheduler.py` `_early_morning_check`. 1시간 동안 60초 간격. 주도섹터(테마) 변화 + 주도주 변화 감지. (2026-05-06)
  - 고주파용 주도주 정의 (사용자 명시, pre-limit-up): 주도섹터 내 **거래대금 상위** OR **상승률 상위** 종목. 한 테마에 여러 주도주, 한 종목이 여러 테마에 걸칠 수 있음 (1:1 매핑 X). 구현은 `identify_early_morning_leaders()`.
  - 정통 주도주 정의 (post-limit-up, ★ 결정 레포트용): 주도테마 내 first-mover 상한가 종목. 구현은 `identify_leading_stocks()`.
- [x] **KRX 정밀 휴장일 캘린더** — `src/calendar_kr.py` 에 `_KRX_HOLIDAYS` 정적 set (2024~2026 큐레이션). 법정공휴일 + 근로자의 날 + 12/31 KRX 임시휴장 모두 반영. `is_holiday()` 함수 추가, `is_business_day()` 가 weekday + 휴장일 둘 다 체크. 매년 12월 KRX 발표 시 갱신 필요. (2026-05-10)
- [x] **`change_rate` 적재 시 NaN** — `src/data/storage.py` `compute_change_rate(df)` helper 추가. 종목별 close pct_change ×100 으로 채움. 호출자가 명시적으로 사용. (2026-05-10)
- [ ] **모의투자(mock) 일봉 endpoint 동작 검증 미완** — 현재 real 모드로만 검증됨
- [x] ~~morning/afterhours `market_stats` 빈 객체~~ → `src/data/index.py` `compute_market_stats()` 구현. KOSPI/KOSDAQ 현재가 + 200일 이평 + 60일 수익률 자동 채움. `_send_morning`/`_send_afterhours` 가 KIS client 받아 호출. (2026-05-10)
- [x] ~~WICS 섹터 매핑 크롤러~~ — 완료 (M0 체크리스트 참조). 중분류(WI 28개)는 v1로 미룸.
- [ ] **수정주가 일관성** — daily fetcher `adjusted=True` 일관 사용 검증
- [ ] **종목 코드 변경(액면분할/합병) 처리** — historical 통계 단절 회피
- [x] **고주파 monitoring tick 실효 갱신 주기 측정·최적화 (round 40, 2026-05-18)** — 2026-05-18 운영 로그 측정: 3,807 tick **100% 가 2초 interval 초과** (정규장 평균 12.9초, 최대 19.9초, funnel 단계 평균 7.3초). 보틀넥 = funnel 의 4×N KIS 호출 직렬. fix: ①`src/dashboard/parallel_fetch.py` 신설 — `fetch_stock_bundle` (한 종목 4 API 직렬 + 예외 격리) + `fetch_bundles_parallel` (종목 N개 ThreadPoolExecutor fan-out, max_workers=12). ②`dashboard_tick` 에서 funnel 후보 ∪ monitored ∪ holdings 합집합을 batch fetch 1회로 처리, tick_cache prefill. funnel(`_evaluate_rising_funnel`)은 fetch 제거 → R14 score 계산만 (CPU only). ③KIS rate limit 은 듀얼 키 합산 ~40 req/s, `src/kis/rate_limit.py:36` lock 으로 동시 호출 자연 throttle. httpx.Client 도 thread-safe. ④계측 라벨 재설계: `[tick] total=X snap=Y fetch=Z (Nfetched종목) score=A monitored=B log=C`. fetch 시간 분리 측정 가능. ⑤캐시 정책: 단타 시그널 (체결강도/거래대금/봉형태) 의 fresh 정책 유지 — tick 안 1회용 buffer 만, tick 간 cache X. tests: `test_parallel_fetch.py` 7건 신규 (예외 격리 / 응답 매핑 / 호출 횟수 4×N 동일) + `test_dashboard_worker.py` 회귀 (`_evaluate_rising_funnel` 시그니처 client 제거, `_patch_bundles` 헬퍼 도입). 859 pass. **운영 검증 필요**: 데몬 재기동 후 정규장 1시간 `[tick]` 로그로 total 평균 ≤ 2,000ms 확인.

- [ ] **monitoring_interval_seconds 일관성** — `config_thresholds.monitoring_interval_seconds(n_codes)` (2→2s, 5→3s, 10→5s) 가 정의돼 있지만 scheduler 가 미사용. round 40 의 fetch 병렬화 후에도 종목 수에 따른 interval 동적 조정 필요한지 운영 측정 후 결정.

- [ ] **R4 v2 결정 레포트 룰 코드 적용 (round 41, 문서 확정 / 코드 미완)** — 사용자(Zeta) 5/19 사후 검증 후 새 룰 확정: `(a) 거래대금 50위 단일종목 + (b) 일봉 상승 + (c) 종가 고가-10% 이내 + (d) 52주 신고가 + (e) 10% ≤ ret ≤ 27% + (f) Layer 표본 ≥5`. 문서는 `docs/jongbae-strategy.md` R4 v2 + 정정 이력 round 41 에 박음. 코드 적용 TODO: ①`src/jongbae/candidates.py` `MIN_DAILY_RETURN=20.0` → 10/27 범위 + 52주 신고가 + R3 의존 분리. R3 후보 universe (`leading_theme_codes` 인자) 우회하는 `extract_candidates_v2(snapshot_df, daily_ohlcv)` 추가 또는 기존 함수 재설계. ②`src/pipeline.py:118` 호출부 — leading_themes 는 그대로 식별 후 결정 레포트 헤더용으로만 쓰고, candidates universe 는 v2 룰로 갈음. ③`src/jongbae/historical.py` 에 `historical_ret10_gap_stats(code, daily_ohlcv, lookback=250)` 추가 — 카드/레포트 보조 표시용 (점수화/컷 X). ④`src/report/decision._candidate_block` 에 historical 보조 라인 추가 (`📊 1년 ret≥10: N회 / 갭상 K회 (X%)`). ⑤레포트 본문 "v2 룰 적용" 푸터 한 줄. ⑥`stocks.parquet.market_cap` 결손은 별도 (TODO 366 의 시총 미수집과 같은 항목). ⑦누적 backtest — 1~3개월 v2 결과 데이터를 `data/journal/` 또는 `data/decisions/` 에 적재 후 갭상 확률 재측정. R4 v2 본문의 "5일 검증" 한계 보강. **본 round 작업은 문서만** — 코드 작업은 시간 확보 후 별도 PR. 5일 backtest 결과 사용자 발화로 확정된 룰이라 직접 코드 박는 것 위험 없음.

- [x] **holdings.json 일일 자동 reset (round 40 후속, 2026-05-19)** — 사용자(Zeta) 5/18 매매일지 분석 중 발견: "005930 같은 경우 매매한 적도 없는데 매매되어있다고 표기 — 데모 때 누른 게 남아있거나 오전 reset 미동작". 단타 정책상 매일 빈 상태로 시작이 기본. fix: ①`src/jongbae/exit_triggers.maybe_reset_holdings(now)` 신규 — idempotent. `data/state/last_reset.txt` 로 마지막 reset 일자 추적, 같은 날 두 번째 호출은 skip (장중 재기동 시 보유 안전). 휴장일/주말 skip. 기존 holdings 는 `data/state/holdings.archive/YYYY-MM-DD.json` archive 백업 후 빈 dict 저장. ②`src/scheduler.py` 양쪽 wiring — `run()` 시작 직후 + 기존 `_reset_state` (08:30 CronTrigger 평일) 양쪽에서 호출. 데몬 첫 가동 시 무조건 보장 + 24h 가동 데몬은 cron 으로 정시 발화. ③테스트 `tests/test_exit_triggers.py` 4 신규 (첫 호출 archive+clear, 같은날 두번째 skip, 휴장일 skip, holdings 비었어도 last_reset 갱신). 863 pass. **사용자 의도 명시 반영**: 장중 코드 업데이트/재기동을 자주 함 — 그때마다 reset 되면 보유 종목 손실. idempotent 설계로 회피.
- [x] **상한가 폴링 HTTP 5xx 격리 + 전 fetcher 일괄 적용 (2026-05-19)** — 사용자 보고: `Server error '500 Internal Server Error' for url '.../inquire-price?FID_INPUT_ISCD=229200'` 가 텔레그램 "⚠️ [에러] 시스템 장애 감지 / 컨텍스트: 상한가 폴링" 으로 발사됨. 원인: `src/data/intraday.py` `fetch_quote()` / `fetch_volume_rank()` 가 `KISApiError` (rt_cd != "0") 만 잡고 `httpx.HTTPStatusError` 는 미처리. KIS 서버 5xx 발생 → tenacity 3회 재시도 후 reraise → `fetch_quote` 통과 → `fetch_quotes_bulk` 통과 → `detect_new_limit_up` 통과 → `_poll_limit_up` 의 `@_business_day_only` 가 `except Exception` 으로 잡아 텔레그램 에러 알림 발사. **단일 종목 500 이 폴링 사이클 전체를 죽이고 푸시 폭주**. fix 1차: `src/data/intraday.py` 양 함수 `except httpx.HTTPError` 추가 (HTTPStatusError + TransportError + TimeoutException 의 부모) — 종목 단위 격리, `logger.warning` + None / 빈 DF 반환. fix 2차 (일괄 적용): 동일 패턴이 있는 모든 fetcher 에 같은 격리 추가 — `src/data/intraday_realtime.py` 의 `fetch_minute_bars` / `fetch_ccnl_strength` / `fetch_asking_price` / `fetch_investor_flow` (모니터링 tick), `src/data/index.py` 의 `fetch_index_quote` / `fetch_index_daily` / `fetch_index_daily_range` (모닝·결정·사후 레포트), `src/data/daily.py` 의 `_fetch_chunk` 호출부 (일봉 청크 — 종목 단위 break). `src/data/incremental_daily.py` / `init_daily.py` 는 이미 `except Exception` 후위가 있어 이중 보호되므로 제외. fix 3차 (safety net + 비주식 코드 필터): 사용자 후속 보고 — 12:17~13:00 사이 13건 5xx 가 사후 레포트 [알려진 이슈] 에 누적 (`086960`, `0167A0`, `0148J0`, `233740` 등 — letter 가 섞인 신주인수권/derivative + ETF 혼재). 두 가지 추가 처치: ①`src/scheduler.py` `_business_day_only` 데코레이터에 `except httpx.HTTPError` 먼저 → `logger.warning` 만 남기고 `record_error` / `telegram_error` 둘 다 skip. 미래 fetcher 회귀로 httpx 가 새도 데코레이터 레벨에서 노이즈 차단. KISApiError / KeyError 등 진짜 시스템 오류는 기존대로 fail-loud. ②`_collect_snapshot` 의 `fetch_volume_rank` 호출에 `master_df=_dashboard_master_df` 전달 → ETF/ETN/리츠/스팩/신주인수권 (letter 코드) 1차 필터. + `_watch_codes` 갱신 시 `c.isdigit() and len(c)==6` 으로 2차 방어 — letter 코드는 inquire-price 가 500 반환하니 원천 차단. tests: `test_intraday.py` 3건 + `test_intraday_realtime.py` 4건 + `test_index.py` 2건 + `test_daily.py` 1건 + `test_scheduler.py` 3건 = 총 13건 신규 회귀. 876 pass (theme_crawler bs4/lxml 환경 7건 제외). **운영 주의**: 데몬 재시작 (`./go stop && ./go start`) 안 하면 fix 무력화 — 기존 polling thread 가 이전 코드 보유.
- [x] **결정 레포트 후보 OHLCV 필드 보강 fetch_quote (2026-05-19)** — 사용자 보고: 14:50 결정 레포트 진원생명과학(011000) 표시 깨짐 — `일봉 +29.97% (0 → 1,366)` (prev_close=0), `일중 고점 0`, `거래대금 0.0억`, Layer 3 사례 없음 (close_position 계산 깨짐). 원인 진단: KIS `volume-rank` (FHPST01710000) 응답이 일부 종목에서 `stck_prdy_clpr` / `stck_hgpr` / `stck_lwpr` / `acml_tr_pbmn` 필드를 비워서 줌 → `_to_int` 가 0 으로 default → 후보 dict 의 OHLCV 가 0. `prdy_ctrt` (등락률) 와 `stck_prpr` (현재가) 는 정상이라 일부 필드만 표시되는 형태. fix: `src/scheduler.py` `_enrich_candidates_with_quote(candidates, client)` helper 신규. `_send_decision_report` 가 `accepted_candidates` 후 후보 dict 리스트를 fetch_quote 로 보강 — 누락(0/NaN) 필드만 fetch_quote 결과로 덮어쓰고 snapshot 의 rank/turnover/market_cap/themes 는 보존. `intraday_high_pct` 도 재계산. 후보 N=1~5 이라 추가 호출 비용 무시. tests: `test_scheduler.py` 3건 신규 (fills_missing / preserves_existing / handles_fetch_quote_none). 879 pass. **함께 명시**: ①호가 `매도 0주` 는 상한가 종목 정상 (매도자 부재), ②`외국인 0 / 기관 0` 은 round 36 `fetch_investor_flow` 응답 list 첫 행 빈값 케이스 — 별개 데이터 이슈, 동일 round 내 처리 X.
- [ ] **report ↔ dashboard 형식 잔여 비일관성** — 2026-05-18 1차로 `_fmt_billion` / `_fmt_pct` 출력 자릿수는 통일했으나 (1) None/NaN 표시 (`render._fmt_*` = "—" vs `report.fmt_pct` = "N/A"), (2) PWA payload 내부 키 `volume_block` 이름이 실제 거래량(주) 미포함이라 misleading — 향후 `trading_value_block` 또는 `amount_block` 재명명 (frontend `app.js` / types 동기), (3) `grader.py` 사유 라인 "거래대금 50위내" / "거래량 12배" 가 카드 한 줄에 같이 등장해 사용자 단위 혼란 가능 — 라벨에 "전일거래량" 명시 검토. 큰 회귀 위험 있어 별도 라운드로 분리.
- [ ] **테마 매핑 변경 시 historical 재계산** — 네이버 테마 월 1회 갱신 시 사례 변동 영향 분석
- [x] **사후 레포트 채널: 이메일 → 텔레그램** — `Dispatcher.send_afterhours()` 추가, `_send_afterhours` 가 호출. (2026-05-12)
- [x] **사후 레포트 candidates 비어있던 placeholder** — `save_decision_candidates` / `load_decision_candidates` (`{DATA_DIR}/decisions/YYYY-MM-DD.json`). 14:50 결정에서 저장 → 16:00 사후에서 재로딩. (2026-05-12)
- [x] **사후 레포트 시간외 단일가 placeholder** — `src/data/afterhours_quotes.py`. KIS 현재가 endpoint(FHKST01010100)로 후보 종목들 시간외 가격 조회. 실패 graceful skip. (2026-05-12)
- [x] **사후 레포트 errors 비어있던 placeholder** — `src/ops/error_log.py` 일자별 JSONL 채널. `_business_day_only` 데코레이터 + 부분 실패 (market_stats / 시간외 / 스냅샷 빈 응답) 모두 기록. 사후 발송 직전 `read_errors` 로 그날 누적분 채움. (2026-05-12)
- [x] **결정 레포트 상단 시장 국면 한 줄** — KOSPI / 200ma 위·아래 / 60일 수익률. 약세장이면 "강세장 가정 무너짐" 경고. (2026-05-12)
- [x] **결정 레포트 후보별 14:50 시그널 (호가/체결/외국인·기관)** — `fetch_asking_price` / `fetch_ccnl_strength` / `fetch_investor_flow` 재사용. 표시만 (Kelly에 반영 X — 자작 가중합 금지). (2026-05-12)
- [x] **Historical layer3_strong_mkt (시장 국면 매칭)** — `market_regime_timeline(kospi_daily, ma_window=200)` 으로 사례 풀 각 날짜에 ma200 위/아래 부여. layer3 사례 중 오늘과 같은 regime 만 매칭. pick_sizing_layer 가 자동 선택 → Kelly 반영. (2026-05-12)
- [x] **Historical layer3_high_vol (거래량 비율 매칭)** — `_compute_returns` 에 volume_ratio (당일 / 직전 20일 평균). layer3 사례 중 오늘 ±0.5배 범위만 매칭. Kelly 자동 반영. (2026-05-12)
- [x] **KOSPI/KOSDAQ 일봉 영구 적재** — `src/data/index_storage.py` + `src/data/index.py` (fetch_index_daily_range 페이지네이션, init/update_index_daily). `python -m src.data.update_index --init` 으로 N년치 백필. 스케줄러 16:10 incremental cron. `compute_market_stats` 적재본 우선 사용 → ma200 매칭 사용 가능 날짜 영구 확장. `./go init-index` / `update-index` CLI. health check `check_index_daily` 추가. (2026-05-12)
- [ ] **회전율 매칭 layer** — 시총 미적재로 보류. 종목 마스터 part2 적재 후 layer3_high_turnover 추가 가능.
- [x] **fetch_volume_rank turnover: KIS 자체 회전율 우선 사용** — `src/data/intraday.py`. KIS 거래대금 순위 응답에 `tr_pbmn_tnrt`(거래대금회전율, %) 필드가 이미 있음. master_df.market_cap 이 0(미적재) 이어도 회전율이 정상으로 잡혀 fallback("거래대금 절대값 → 항상 대형주") 함정 회피. M6 자동 모니터링이 진짜 회전율 1위(계양전기 등) 잡기 시작. (2026-05-12)
- [ ] **fetch_quote / fetch_quotes_bulk 도 hts_avls 사용** — 단일 종목 현재가 응답엔 `tr_pbmn_tnrt` 없음 대신 `hts_avls`(시가총액, 억원) 있음. fetch_quote 에서 market_cap 채우면 사용자 수동등록 종목도 회전율 정상 계산.
- [ ] **종목 마스터 market_cap 적재** — KIS mst part2 char[172:181] 슬라이스가 모두 0 으로 파싱됨. mst 포맷 검증 또는 pykrx/KIS inquire-price 로 별도 backfill 필요. M6 핵심 path 는 위 [x] 로 우회 완료지만, historical 분석/리포트 단가 추산 등에는 여전히 필요.
- [x] **parquet 손상 graceful degradation** — `src/data/storage.py` `_safe_read_parquet()` helper. 모든 read 함수(일봉/마스터/테마/WICS/스냅샷/지수)가 손상 시 ERROR 로그 + 빈 DF 리턴. scheduler가 _dashboard_start 등에서 throw로 마비되는 것 방지. 트리거: 2026-05-12 새벽 `data/daily/ohlcv.parquet` footer 손상 (in-place 덮어쓰기 사고 추정). (2026-05-12)
- [ ] **parquet atomic write** — `write_daily_ohlcv` / `write_index_daily` / `write_stock_master` 등 모두 `to_parquet(path)` in-place. 쓰는 도중 프로세스 죽으면 footer 손상. tmp 파일 → fsync → os.replace 패턴으로 변경 필요. 재발 방지.
- [ ] **일봉 ohlcv.parquet 재백필** — 2026-05-12 footer 손상으로 corrupted 파일(`data/daily/ohlcv.parquet`, 12MB, 05-12 02:01) 그대로 남아있음. 현재는 graceful degradation으로 빈 DF 취급되어 모니터링은 동작하지만 historical 매칭/모닝 갭 분석은 무력화. `mv data/daily/ohlcv.parquet data/daily/ohlcv.parquet.corrupted-20260512 && ./go init --years 5` 필요.
- [ ] **fail-loud 텔레그램 알림 (parquet 손상)** — 현재는 logger.error로만 남김. corruption 발견 시 텔레그램 에러 채널 1회 발송(중복 억제 포함) 추가 가능.
- [ ] **복기 도구 (post-mortem replay) — 새 세션에서 진행** (2026-05-14 컨셉만 박아둠)
  - **목적**: 사용자가 단타 초보로서 "이 때 들어갔어야 / 빠져나왔어야 / 어떤 지표를 봤어야"를 차분히 학습. 그날 매매 끝난 후 종목 + 날짜를 입력하면 분봉 타임라인 + 변곡점 + what-if + 놓친 시그널 분석을 생성.
  - **핵심 기능 4가지**:
    1. 타임라인 (분봉 단위 가격/VP/회전율/accel + R14 점수 + R15 트리거 상태)
    2. 변곡점 자동 추출 — 가격 +5%/-3% 5분 윈도우 + 그 직전 5분 지표 변화 ("VP가 가격을 5분 선행")
    3. What-if 시나리오 — "X시점 진입했다면 / Y시점 청산했다면" 자동 계산
    4. 놓친 시그널 분석 — 매도 트리거가 발화 안 한 이유 (임계 미달 폭) / 발화했으나 카드만 떠 있었던 시점
  - **데이터 의존성 (블로커)**: 현재 분봉/VP가 메모리만이라 복기 자체 불가능. 영속화 인프라 결정 필요. 옵션:
    - (A) KIS 에서 그날 끝난 후 분봉 재호출 (디스크 0, KIS 호출 비용 발생)
    - (B) 처음엔 모든 종목 저장, 6개월 후 보존 정책
    - (C) 1분봉 대신 5분봉만 (디스크 1/5, 정밀도 ↓)
    - (D) 1시간 간격 거친 스냅샷만 paper_trade 에 추가 (복기 정밀도 매우 낮음, 작업 가벼움)
    - (E) 사용자가 직접 결정 — 새 세션에서 결정 후 진행
  - **추가 컨셉 후보** (사용자가 새 세션에서 선택):
    - 종목별 복기 외에 **시장 전체 분위기 복기** (테마 회전, breadth 변화)
    - 여러 날짜 **패턴 비교** ("월요일 vs 금요일 단타 차이", "강세장 vs 약세장")
    - **AI 자동 코멘트** ("오늘 STRONG 5개 중 4개 갭상 — 강세장 가정 유지 가능")
  - **발송 채널**: 마크다운 파일 (`data/replay/YYYY-MM-DD-CODE.md`) 권장 / 텔레그램은 4096자 제한 + 텍스트 차트 가독성 떨어짐 / CLI 즉시 출력 옵션도
  - **시각화**: short_trend_sparkline 패턴(▁▂▃▄▅▆▇█) 텍스트 sparkline 권장. ASCII 라인차트 또는 matplotlib 은 v0 "터미널 친화" 원칙(CLAUDE.md) 위반 우려
  - **이미 있는 기반**: paper_trade.py (14:50 + 다음날 09:30 결과 골격) — 복기 도구가 이걸 확장하거나 별도 모듈로 분리 가능

---

## R14/R15 가중치 검증 ritual (round 23~30 후 도입)

배경: R14 매수 점수 가중치는 "한국 단타 통설 조합"이긴 하나 **검증 데이터 없는 추정치**.
백테스트가 분봉 히스토리 부재로 v0에서 불가하므로, **검증 가능한 대안 3단**을 ritual로
박아 둔다. 가중치 변경 시 매번 통과시켜야 함.

### ritual 1: 회귀 케이스 누적 (지속)

- 매주 1~2개씩 known-good / known-bad 케이스를 `tests/test_grader.py` 회귀에 추가
- 입력 출처: ①사용자 경험 (제룡전기 STRONG / 흥아해운 AVOID 같은) ②14:50 결정 레포트에서 STRONG 받았다가 다음날 갭하락한 케이스 ③돌이켜 보니 진입했어야 했는데 점수 낮았던 케이스
- 6개월 누적 목표 30~50개. 가중치 변경 시 **회귀 통과율 90% 이상** 가드레일
- 신규 케이스 발견 시 즉시 docs/jongbae-strategy.md "검증 가능한 사용자 발화" 섹션에도 기록

### ritual 2: paper-trade 일일 검증 (round 32 자동화 완료)

- 14:50 결정 레포트의 STRONG/WATCH 종목을 `data/paper_trade/YYYY-MM-DD.json` 에 자동 기록
  - 필드: 종목코드/등급/점수/사유 reasons[]/14:50 가격
- 다음날 09:30 자동 추가 기록: 시초가/오전 고가/오전 종가/`JongbaeExitDecision`
- 1개월 (≈20거래일 × 평균 3종목 = 60샘플) 누적 후 mini-stat 자동 산출:
  - **점수 ↔ 갭상 확률 상관계수** (Spearman ρ ≥ 0.3 가드레일)
  - **STRONG 등급의 평균 시초가 수익률** > 0%
  - **AVOID 권고된 종목 표본 추출 검증** (false positive 비율)
- 구현 완료: `src/jongbae/paper_trade.py` (`PaperTradeRecord`, `record_decision`, `record_open_result`, `load_records`, `compute_summary`).
  남은 wiring: 14:50 결정 레포트 + 09:30 모닝 레포트에서 호출 한 줄 (다음 라운드).

### ritual 3: 통설 제약 가드레일 (round 32 자동화 완료)

가중치 변경 PR 마다 다음 invariant 가 깨지지 않는지 자동 검증:

```
sum(통설 가중치) ≥ sum(비통설 가중치) × 2

통설(R3/R10/R11/R12/R14a/R14b/R14c/R14d): 회전율/VP/가속/봉/VWAP/이평/상한가시간/거래량비율
비통설(R13 다이버전스): ±1 강등됨
```

- 구현: `tests/test_grader.py::test_invariant_consensus_weights_dominate_positive/negative` + `_divergence_weight_capped_at_one`. 3 케이스.
- 통설 양/음수 합산이 비통설의 2배 이상. R13 가중치를 통설 합산의 50% 이상으로 키우면 테스트 깨짐 → 의식적 결정 강제.

### gate criteria — "가중치 추정치 → 운영 가중치" 전환 기준

다음 모두 통과 시에만 가중치를 "검증됨" 으로 docs 에 표기 (현재 모두 "추정치"):

- [ ] 회귀 케이스 ≥ 30 (ritual 1)
- [ ] paper-trade 누적 ≥ 60 샘플, Spearman ρ ≥ 0.3 (ritual 2)
- [ ] 통설 가드레일 invariant 통과 (ritual 3) — round 31 자동화 TODO

미통과 시 폴백: 단순 룰 `VP < 100 AND vol_accel_1m < 0.5 → AVOID` 로 회귀 (`docs/jongbae-strategy.md` R14 본문 명시).

---

## 진행 상황 추적

각 마일스톤의 완료 여부는 본 문서 상단 체크박스로 관리한다. 매주 한 번 진행 상황 리뷰.

### 세션 요약

**2026-05-12 — 결정/사후 레포트 강화 + 지수 일봉 영구 적재**

종배 매매 의사결정 보조에 필요한 정보 밀도와 historical 매칭 정밀도를 끌어올린 세션. 한국 단타 통설(상한가 잔량/체결강도/외국인·기관/시장 국면/회전율) 중 코드베이스에서 즉시 활용 가능한 항목을 결정 레포트와 historical 통계에 반영.

- **사후 레포트 (16:00)** — 채널 이메일→텔레그램, 14:50 candidates 영속화(`{DATA_DIR}/decisions/YYYY-MM-DD.json`)→사후 재로딩, KIS 현재가 endpoint로 시간외 단일가, 일자별 JSONL 에러 집계 채널(`src/ops/error_log.py`).
- **결정 레포트 (14:50)** — 상단 시장 국면 한 줄(KOSPI/200ma/60일 수익률), 후보별 호가·체결·외국인/기관 시그널(M6 fetcher 재사용, 표시만/Kelly 반영 X). 약세장 시 "강세장 가정 무너짐" 경고.
- **Historical 매칭 layer 2종** — `layer3_strong_mkt`(KOSPI ma200 regime 매칭), `layer3_high_vol`(거래량 비율 ±0.5배 매칭). `pick_sizing_layer`가 좁은 layer 우선 → Kelly 자동 재계산.
- **KOSPI/KOSDAQ 일봉 영구 적재** — `src/data/index_storage.py` parquet, `fetch_index_daily_range` 페이지네이션, `init/update_index_daily` CLI(`./go init-index`/`update-index`), 16:10 cron 통합, health check 추가. ma200 매칭 사용 가능 날짜를 ~52일→백필 N년-200일로 영구 확장.

총 7 커밋, 540 tests, plan.md 기술 부채 14항목 [x] 완료.

다음 후보 (우선순위):
1. 시총 적재(M5.5 선행) → 회전율 layer 가능
2. 분봉 적재 시작(Layer 4 / 상한가 도달 시각 매칭 v1)
3. 수정주가 일관성 검증
4. 1주일 dry-run (M5)
