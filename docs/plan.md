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
- [ ] WICS 섹터 매핑 크롤링 (월 1회) — 미착수
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
- [ ] **무결성 체크 알림 채널** — 현재 stderr/exit code 만, 텔레그램은 M4 이후 통합
- [ ] **R5 Layer 4 (고점 도달 시각 매칭)** — 분봉 히스토리 부재로 v0 미구현. 매일 분봉 적재 후 v1에서 구현
- [x] **종배 시그널 통합 파이프라인** — `src/pipeline.py` `run_pipeline()`. demo 모드 (--demo), 저장 (--save), 발송 (--send). `src/demo_fixtures.py` 제룡전기 2025-05-04 mock. `tests/test_pipeline.py` 13개 E2E 테스트. (2026-05-06)
- [ ] **09:00~09:30 장 초반 고주파 모니터링** — 장 개시 직후 30초~1분 간격으로 거래대금 상위 + 주도섹터 변화 감지. 변화 있을 때만 텔레그램 알림. 9:30 이후엔 정규 11/13/14 스냅샷 주기로 복귀. (M3/M4 완료 후 스케줄러에 추가)
- [ ] **KRX 정밀 휴장일 캘린더** — v0 는 weekday 기반. 정밀화는 KIS 인덱스 OHLCV 또는 정적 테이블
- [ ] **`change_rate` 적재 시 NaN** — 분석 단계에서 `groupby('code')['close'].pct_change()` 로 계산
- [ ] **모의투자(mock) 일봉 endpoint 동작 검증 미완** — 현재 real 모드로만 검증됨

---

## 진행 상황 추적

각 마일스톤의 완료 여부는 본 문서 상단 체크박스로 관리한다. 매주 한 번 진행 상황 리뷰.
