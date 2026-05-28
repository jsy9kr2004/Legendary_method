# CLAUDE.md

이 프로젝트에서 Claude Code가 코딩을 도울 때 참고할 컨텍스트.

## 프로젝트 한 문장 요약

한국 주식 매매 의사결정 보조 시스템. **핵심은 두 축이다 (둘 다 운영 중)**:

1. **주도주 단타 (09:00~10:30) — 단저단고 모니터링** ★
   주도섹터 Top 3 의 주도주/후보 + 사용자 종목을 텔레그램 카드 / PWA 대시보드 에
   1~3초 단위로 갱신. 분봉 단저단고(intraday mean reversion) 시그널 발화 시
   STRONG 푸시 알림. 사용자가 카드 보고 직접 매수 결정 (HTS 클릭).
2. **종배 (장 마감 전 14:50) — 결정 레포트** ★
   주도섹터 + 일봉 +20%↑ 종목 후보 + 4-Layer 갭상 통계 + Kelly/Sharpe/Equal 사이징
   을 14:50 텔레그램 발송. 사용자가 매수 → 다음날 시초가 매도.

매수/매도 실주문은 **사람이 직접** 한다 (자동 매매 영구 X — `docs/dashboard-pwa.md` §6).
"단타" = "주도주 단타" = "단저단고 모니터링" 모두 같은 것 (사용자가 자주 혼용).
"종배" 시스템은 `src/overnight/`, "단타" 시스템은 `src/scalping/` 에 분리.

스윙 매매 (수 일~수 주) 는 v0 범위 X.

## 사용자 (Zeta) 컨텍스트

- 10년차 개발자, 팀 리드, CS 석사
- 강한 시스템/C 백그라운드, vim/tmux 워크플로우
- 한국어 모국어, 답변은 한국어로
- 가정 서버: Windows 10 + WSL2 Ubuntu, RTX 2080 Super
- 별도 quant 프로젝트 운영 중 (`jsy9kr2004/Quant`)
- 자동화/zero-intervention 솔루션 선호

## 코드 스타일 원칙

1. **터미널 친화적**: 모든 도구는 CLI에서 실행 가능해야 함. GUI 의존 X
2. **모듈 분리**: 데이터 수집 / 분석 / 레포트 / 알림은 명확히 분리
3. **incremental update**: 매일 전체 재계산이 아니라 어제 데이터에 오늘만 append
4. **fail-loud**: 데이터 문제, API 실패는 조용히 넘어가지 말고 텔레그램 에러 알림
5. **type hint**: 모든 public 함수에 type hint
6. **docstring**: 한국어 OK. 매매 룰 관련 함수는 반드시 정량 정의 명시

## Claude Code 작업 룰

이 프로젝트에서 Claude Code(또는 다른 AI 코딩 어시스턴트)가 작업할 때 따라야 할 규칙.

### 작업 시작 전

- 새 파일/모듈 만들기 전에 반드시 `docs/plan.md`의 현재 마일스톤과 체크리스트 확인
- **시스템 확인**: 작업이 단타(주도주 매매)인지 종배인지 먼저 명확화. 단타 = `docs/scalping-strategy.md` (Buy.Score, Exit.Triggers, Monitor 등) / 종배 = `docs/eod-strategy.md` (Eod.Market, Eod.Pick, Eod.GapStats 등)
- 데이터 모듈 작업이면 `docs/data-infra.md` 재확인
- 레포트 모듈 작업이면 `docs/report-spec.md` 재확인 (종배 14:50 레포트)
- 정정 이력 확인 — 같은 실수 반복 방지. 단타 정정 이력은 `docs/scalping-strategy.md` 하단, 종배 정정 이력은 `docs/eod-strategy.md` 하단

### 작업 중

- 데이터 다운로드, API 대량 호출 등 시간 오래 걸리는 작업은 **백그라운드 실행 + 진행률 출력** (tqdm 또는 loguru로)
- 새 의존성 추가 시 반드시 `requirements.txt` 같이 업데이트
- API 키, 토큰, 비밀번호는 **절대 코드에 하드코딩 X**. `.env` + `python-dotenv` 사용
- 파일 경로는 `pathlib.Path` + 환경변수 `DATA_DIR` 기반으로 (`os.path` 보다 선호)
- 시간 처리는 항상 `pytz` Asia/Seoul. naive datetime 사용 X
- 외부 API 호출에는 반드시 retry + timeout (`tenacity` 권장)

### 작업 완료 후

- `docs/plan.md`의 해당 체크박스 업데이트 (`[ ]` → `[x]`)
- 새로 발견한 기술 부채/TODO는 `docs/plan.md` 하단의 "기술 부채 / TODO 메모" 섹션에 기록
- 매매 룰에서 정정/보강된 부분이 있으면 해당 시스템 파일 "정정 이력" 표에 행 추가 (단타 → scalping-strategy.md / 종배 → eod-strategy.md)
- 새 도메인 용어가 등장하면 본 파일(`CLAUDE.md`)의 "핵심 도메인 용어" 표에 추가

### 커밋 메시지 포맷

```
[milestone-N] 간략 요약

상세 변경 내용 (필요시)
- 변경 1
- 변경 2

Refs: docs/scalping-strategy.md Buy.Score (단타) 또는 docs/eod-strategy.md Eod.Pick (종배), docs/plan.md M1
```

예시:
- `[milestone-0] pykrx 일봉 5년치 초기 적재 스크립트`
- `[milestone-2] 4-Layer historical 갭상 통계 계산`
- `[milestone-3] 14:50 결정 레포트 생성기`

### 테스트 작성

- 매매 룰 함수는 반드시 unit test 작성 (`tests/` 디렉토리)
- Historical 매칭, 사이징 같은 핵심 로직은 known input/output으로 검증
- 검증 사례는 해당 시스템 파일의 "검증 가능한 사용자 발화" 활용 — 단타 (`docs/scalping-strategy.md`: 5/19 메이슨캐피탈, 5/20 주성엔지니어링 등) / 종배 (`docs/eod-strategy.md`: 5/4 제룡전기 등)
- 데이터 fetcher는 mock 응답으로 테스트 (실제 API 호출 X)

### 안전 제일

- **자동 매매 절대 금지**: 본 프로그램은 레포트만 생성한다. 어떤 경우에도 매수/매도 주문 코드 작성 X
- 데이터 삭제/덮어쓰기 작업은 백업 후 진행
- 운영 중 데몬 변경 시 dry-run → 1일 검증 → 적용
- KIS API 모의투자(`KIS_API_MODE=mock`) 환경에서 먼저 테스트

### 의문 사항 해결 우선순위

1. 본 파일(`CLAUDE.md`) 및 `docs/` 문서 확인
2. 정정 이력 확인 (이전에 같은 질문 있었는지)
3. 그래도 모호하면 코드 짜기 전에 Zeta에게 질문
4. 임의로 가정해서 진행하지 X (특히 매매 룰)

## 명명 체계 (2026-05-21 마이그레이션) ★

옛 R 번호 → 의미있는 이름. 시스템 prefix 로 단타/종배 명확 분리.

### 단타 (Scalping) — `src/scalping/`

| 옛 | 새 (긴 / 약식) | 의미 |
|---|---|---|
| R3' | `Theme.Leader` / LDR | 단타 주도주 (주도섹터 내 회전율 1위) |
| R9 | `Monitor` / M | M6 카드 시스템 |
| R10 | `Buy.VP` / VP | Volume Power 체결강도 |
| R11 | `Buy.Accel` / ACC | 거래대금 가속 |
| R12 | `Buy.Candle` / CDL | 봉 패턴 |
| R12.5 | `Buy.Position` / POS | 위치/맥락 |
| R13 | `Buy.Div` / DIV | 다이버전스 |
| R14 | `Buy.Score` / B | 매수 점수 + 등급 (**2026-05-29 카드 표시 폐기, tick_log 로깅만 유지**) |
| R15 | `Exit.Triggers` / E | 매도 트리거 + 상태 머신 (**2026-05-29 카드 표시 폐기, tick_log 로깅만 유지**) |
| — | `MR` (mean reversion) | **단저단고 v10b — 카드 메인 시그널 (2026-05-29 운영 전환)**. `src/scalping/signals/mean_reversion.py` + `weighted_score.py` |

**Exit 하위 알파벳** (각 글자가 단어 의미):
- **A**1~A5 = **A**uto-stop (자동 손절 — 가격/봉/이평/시간/EOD)
- **P**1~P3 = **P**rofit-take (익절 — 1차/2차/트레일링). 옛 B1~B3 에서 변경
- **E**1~E5 = **E**xit-signal (시그널 청산 — VP/Divergence/자금이탈/봉/VI). 옛 C1~C5 에서 변경

### 종배 (Eod = End-of-day) — `src/overnight/`

| 옛 | 새 (긴 / 약식) | 의미 |
|---|---|---|
| R1 | `Eod.Market` / MKT | 시장 강세 게이트 |
| R4 | `Eod.Pick` / PICK | 종배 후보 추출 (v2) |
| R5 | `Eod.GapStats` / GAP | 4-Layer 갭상 통계 |
| R6 | `Eod.Sizing` / SIZ | Kelly/Sharpe/Equal |
| R7 | `Eod.Exit` / OXE | 다음날 시초가 매도 |
| R8 | `Eod.Exec` / EXE | 매매 실행 (사람) |

### 공통 — `src/common/`

| 옛 | 새 | 의미 |
|---|---|---|
| R2 | `Universe` | 종목 universe (KOSPI+KOSDAQ - ETF/etc) |
| R3 | `Theme` | 주도섹터 식별 (네이버 테마 z-score) |

### tick_log 컬럼명 (2026-05-21 마이그레이션)

옛: `trigger_b1_take_profit_1`, `trigger_c1_vp_below_100`...
새: `trigger_p1_take_profit_1`, `trigger_e1_vp_below_100`...

5/18 ~ 5/20 기존 parquet 파일도 일괄 변환 (마이그레이션 스크립트 적용).

---

## 핵심 도메인 용어

| 용어 | 정의 |
|---|---|
| 종배 (jongbae, Eod) | 종가 베팅. 장 마감 직전 매수 → 다음날 시초 매도 |
| 주도테마(주도섹터) | (v0) 거래대금 30위 내 같은 테마 ≥3종목. (v1, M5.5) 테마별 breadth + 동일가중 평균상승률 + 회전율 합계의 z-score 합산 상위 N개 |
| 주도주 (정통, 결정 레포트용) | 주도테마 내 first-mover 상한가 도달 종목. `identify_leading_stocks()` |
| 주도주 (고주파, 옛 09:00~10:30 모니터링용 — 2026-05-29 폐기) | 주도테마 내 **회전율 1위** 종목. pre-limit-up 진입 후보. `identify_early_morning_leaders()`. **2026-05-29 단저단고 surface 룰로 대체** |
| 주도주 (단저단고 surface 룰, 2026-05-29~) | 주도섹터 내 **거래대금 1위 ∩ 회전율 1위** (다르면 공동 주도주 2종목). `select_leaders_and_candidates()` (`src/common/theme.py`) |
| 주도주 후보 (단저단고 surface 룰) | 주도섹터 내 **거래대금 2위 == 회전율 2위** (같은 종목일 때만, 다르면 후보 없음). 공동 주도주 케이스에선 평가 X |
| 거래량 (volume) | 누적 체결주식수 (주). KIS `acml_vol`. ★ universe 정렬 기준으로 쓰면 ETF/저가주 편향 — 종배 universe엔 부적합 |
| 거래대금 (trading_value) | 누적 체결금액 (원). KIS `acml_tr_pbmn`. ★ 종배/주도섹터 universe의 정답 정렬축. KIS volume-rank 사용 시 `FID_BLNG_CLS_CODE="3"` |
| 회전율 (turnover) | 거래대금 ÷ 시가총액. 시총 정규화로 대형주 편향 제거. 단타 자금 유입의 진짜 강도 |
| breadth (테마 폭) | 테마 구성종목 중 +5%↑/+10%↑ 종목 수 |
| 가속배율 | 현재 5분봉 거래대금 ÷ 직전 30분 평균 5분봉 거래대금. 양수→치고 올라옴, 음수→자금 이탈 |
| 분봉 거래대금 임계 | 분봉(1~5분) 거래대금 20억 이상 (실무 기준봉 임계, i-whale) |
| TRANSITION | 주도주 교체 가능성 상태 — a2 가속 ≥ 5배 + 분봉거래대금 ≥ 20억 + a2 회전율 ≥ a1 × 0.6 |
| GRACE | 실제 교체 후 5분 유예기 — a1, a2 함께 표시 (엎치락뒤치락 대비) |
| 부상 후보 (RISING) | (round 21) "매수 점수 부상 후보". 4단계 funnel + Buy.Score ≥ 2.0. **2026-05-29 카드 surface 폐기** — `LEGACY_RISING_FUNNEL=1` env 시만 부활 (back-out 용) |
| 단저단고 (intraday mean reversion) | 분봉 swing low/high + oversold/overbought 시그널. mr_sigB=매수 트리거, mr_sigS=매도 트리거. mr_grade=v10b weighted score 등급 (STRONG≥2 / WATCH≥1 / NEUTRAL). 2026-05-29 단타 카드 메인 시그널 |
| 단저단고 히스토리 | 카드 옛 청산 시그널 자리에 sigB/sigS 발화 이력 최대 3개 (시간 + kind + score + reason). MonitoredStock.mr_history FIFO. 2026-05-29 신규 |
| 일봉 +20%↑ | 종가 기준 전일 대비 수익률 +20% 이상 |
| 갭상 (gap up) | 다음날 시가가 전일 종가보다 높은 것 |
| 상한가 | KOSPI/KOSDAQ +30%. 종배의 1순위 진입 시점 |
| 체결강도 (VP, Volume Power) | 능동 매수체결량 / 능동 매도체결량 × 100. 100=균형. Buy.VP. 호가 잔량을 메인에서 강등하고 VP가 메인 |
| VP_5MA / VP_20MA | 체결강도 5분/20분 이동평균. 장중 메모리 시계열 |
| vol_accel_1m / vol_accel_5m | Buy.Accel 분당 거래대금 가속. 1m=최근1분/직전5분평균, 5m=최근5분/직전20분평균. Theme.Leader 30분 분모와는 별개 용도 |
| 매수 점수 / 등급 | Buy.Score. 점수 합산 → 🟢STRONG(≥5)/🟡WATCH(≥2)/⚫NEUTRAL(≥-1)/🔴AVOID. 개별 시그널 색상 부여 폐기 후 도입 |
| Bearish/Bullish Divergence | Buy.Div. 가격 변화와 VP_5MA 변화의 부호 반대. Bearish=고점 신호, Bullish=매집 신호 |
| 감시 모드 / 보유 모드 | Exit.Triggers. 모니터링 종목의 두 상태. `/buy CODE PRICE`로 보유 전환, `/sell`로 복귀. TRANSITION/GRACE(주도주 교체)와는 다른 축 |
| Exit.Triggers 매도 트리거 A/P/E | **A**=Auto-stop 손절(가격/봉저점/이평/시간/EOD) / **P**=Profit-take 익절(1차/2차/트레일링) / **E**=Exit-signal 시그널(VP이탈/Bearish/자금고갈/윗꼬리음봉/VI). OR 조건. (2026-05-21 명명 변경: B→P, C→E) |
| 흥아해운 케이스 | 모멘텀 죽음 + 호가만 5.3배라 가짜 매수 신호 발생. Buy.Score 회귀 테스트 입력 |

## 절대 헷갈리지 말 것

- **거래량(volume) ≠ 거래대금(trading_value)** ★ — 2026-05-19 사용자가 발견한 critical 버그
  - 거래량 = `acml_vol` (주식 수, 주). KODEX 200선물인버스2X 같은 저가 고회전 ETF가 늘 1위.
  - 거래대금 = `acml_tr_pbmn` (체결금액, 원). 보통 삼성전자/SK하이닉스가 1위.
  - 종배/주도섹터 universe는 **반드시 거래대금** 기준. 거래량으로 잡으면 ETF/인버스가 점령.
  - KIS volume-rank API `FID_BLNG_CLS_CODE`: `"0"`=평균거래량(틀림), `"3"`=거래금액순(정답).
    `src/data/intraday._VOLUME_RANK_BLNG_CLS_TRADING_VALUE` 상수 + 회귀 테스트로 박아둠.
  - 5/12~5/18 0종목 현상 + round 41 backtest 5일 검증 결과 모두 이 버그로 무효화됨 → 재검증 필요.
  - 회전율(turnover) = 거래대금 / 시총. 분모가 시총이라 거래량/거래대금 혼동과 또 별개 축.
- "음봉 시작" = **하루 일봉이 음봉인 날** (분봉 아님)
- "-20~30%" 표현은 빠진 폭이 아니라 **일봉 상승률 +20~30%**를 의미함 (대화록 다회 확인됨)
- 한국 장전 시간외 (08:30~08:40)는 **어제 종가 고정**이라 갭 익절 불가
- NXT (대체거래소)는 08:00~08:50 진짜 프리장이지만 **v0에서는 TODO**
- 종배 결정 레포트는 **장마감 전 14:50**에 발송해야 의미 있음 (장마감 후 X)
- 주도주는 **두 정의가 공존** — 결정 레포트(상한가 도달)와 모니터링(회전율 1위)은 다른 함수, 헷갈리지 말 것
- "거래대금 1위 = 주도주"는 **함정** — 항상 하이닉스/삼전이 1위. 단타용은 **회전율 1위**
- **검증되지 않은 자작 종합 스코어 X** — 한국 단타 통설(상승률/회전율/breadth/가속배율) 그대로 따른다. AI가 임의로 가중합/공식 만들지 X
- ETF/ETN/리츠/스팩/펀드는 **주도주 후보에서 제외** — 단타 대상 아님 (M5.5)
- **Buy.Score / Exit.Triggers 카드 표시 X (2026-05-29~)** — 단타 모니터링 카드에서 헤더 등급 라벨 / 사유 라인 / `─ 청산 시그널 ─` C 그룹 라인 모두 폐기. tick_log 의 `buy_score`/`buy_grade`/`buy_reasons`/`trigger_a*/p*/e*` 컬럼은 사후 비교/매매일지 용으로 계속 기록. Back-out: `LEGACY_RISING_FUNNEL=1` env 토글
- **단저단고 surface 룰 (2026-05-29~)**: `select_leaders_and_candidates()` 의 leaders + candidates + manual + holdings 만 카드 surface. 권외 종목 mr_sigB 발화는 tick_log 만 (카드 X). Top 섹터 < 3 인 약세장은 강제 padding X (섹터 수만큼만)
- 메시지 1~2초마다 send 하면 푸시 폭주 → **`editMessageText`로 메시지 1개 갱신**, 푸시는 신규 종목 진입/임계 초과 시점만
- 주도주 교체는 **2단계 상태 머신** (TRANSITION → GRACE 5분 유예) — 순간 이벤트 X, 엎치락뒤치락 대비
- 주도섹터 1:1 주도주 매핑 X — 한 섹터 여러 후보, 한 종목 여러 섹터 가능

## 매매 전략별 상태

```
1. 주도주 단타 (09:00~10:30, 분봉 단저단고) → **운영 중 ★ (2026-05-29 단저단고 패러다임 전환)**
2. 종배 매매 (오버나잇, 14:50 결정 레포트)  → **운영 중 ★**
3. 스윙 매매 (수 일~수 주)                  → v0 범위 X
```

위 1, 2 가 본 프로젝트의 두 핵심 산물. 매일 09:00 카드 자동 ON + 14:50 종배 레포트.

## 단저단고 모니터링 정량 룰 (2026-05-29~)

전체 시그널 정의는 [`docs/scalping-redesign-2026-05-27.md`](docs/scalping-redesign-2026-05-27.md). surface 3단계:

**Step (1) — 거래대금 30위 풀 추출** ★ (2026-05-29 사용자 후속 정정)
  `intersect_universe()` (`src/common/universe.py`) — RANK_MAX=30, turnover_rank_max=10000
  (사실상 거래대금 30위 단일 적용). 원래 30위 ∩ 회전율 30위 였으나 대형주
  (시총 큰 종목) 가 회전율 30위 안에 못 들어 자동 surface 제외되는 문제 발견.
  운전수 가설 (per-stock weight) 검증 데이터 1개월 누적 후 회전율 조건 재도입 검토.

**Step (2) — 주도섹터 식별 (Top 3)**
  Step (1) 풀로 `score_leading_sectors()` 호출 — 테마별 breadth + 동일가중
  평균상승률 + 회전율 합계의 z-score 합산 상위 3개 = 주도섹터 1·2·3위.

**Step (3) — 주도섹터별 주도주 + 후보 식별**
  `select_leaders_and_candidates()` (`src/common/theme.py`):
  - **주도주** = 섹터 내 **거래대금 1위 ∩ 회전율 1위** (같은 종목)
    - 다르면 공동 주도주 2종목 (이 경우 후보 평가 X)
  - **주도주 후보** = 섹터 내 **거래대금 2위 == 회전율 2위** (같은 종목일 때만)
    - 다르면 후보 없음
  - Top 섹터 < 3 약세장은 강제 padding X (섹터 수만큼만)

**Step (4) — surface universe**
  자동 = leaders ∪ candidates (최대 6종) ∪ 사용자 수동 ∪ 보유.

**시그널** — `mean_reversion.py`:
  - mr_sigB = 매수 트리거 (swing low + oversold; STOCH ≤ 30 OR Z ≤ -1.0)
  - mr_sigS = 매도 트리거 (swing high + overbought + 음봉 패턴)
  - mr_grade STRONG (score ≥ 2.0) / WATCH (≥ 1.0) / NEUTRAL — v10b weighted score

**카드 표시**: 헤더 4종 (⭐주도주 / 🌟주도주 후보 / 🔵수동 / 💎보유) + 테마 1개
  (surface 된 주도섹터) + 단저단고 라인 + 보조 (가격/가속/체결강도/호가/수급) +
  단저단고 히스토리 (최대 3).

**STRONG 푸시 알림 (2026-05-29 신규)**: `MR_STRONG_ALERT=1` (default) 인 경우, 자동
  surface 6종 + 수동 + 보유 종목의 mr_grade==STRONG + (sigB OR sigS) 시 별도
  텔레그램 메시지 send. `/on`/`/off` 와 무관 (paused 일 때도 push). 종목별로 같은
  kind 연속 발화는 1회만 push (kind 전환 또는 STRONG 영역 벗어났다 재진입 시만 재 push).

**자동 청산 X**: 사용자가 카드 보고 직접 매매. CLAUDE.md 자동 매매 금지 정책 유지.

**Back-out**: `LEGACY_RISING_FUNNEL=1` env 토글로 옛 Buy.Score funnel surface 부활.

### ⚠ 단저단고 시스템 한계 (2026-05-29 정직 명시)

정통 train/val split backtest 결과 **net 음수** (지정가 -0.55%, 시장가 더 나쁨):
- 분봉 단저단고 swing 평균 폭 (+4~5%) 이 매매 timing miss + 비용 합산 흡수 어려움
- AUC 0.879 (단저) / 0.887 (단고) = 시그널 정확도 좋음
- Per-Stock weight 효과 +0.08~0.50%p (운전수 가설 ✓)
- 그러나 **절대 net 양수 도달은 어려움** — 분봉 단위 잔파동 매매의 본질 한계

**정량 진단 + 약점 분석 전체**: [`docs/scalping-weakness-analysis-2026-05-29.md`](docs/scalping-weakness-analysis-2026-05-29.md)
- AUC vs Precision @top 0.5% gap (88→11.5%)
- 진짜 단저 64건 (+3.6%) vs 가짜 단저 492건 (0%, MAE -2.19%)
- 봉 close 지연 +0.95% 슬리피지
- MFE 캐치율 14.6%
- B/C 옵션 backtest (B 효과 0%p, C 효과 +0.08%p 일관)

운영 정책:
- **사용자 직관 매매 보조 도구** — 카드 보면서 신호 참고 + 사용자가 종합 판단
- **자동 매매 영구 X** (CLAUDE.md 자동 매매 금지 정책 유지)
- 강한 추세 종목 (예: 5/28 삼성전기 +13%) 은 단저단고 보다 buy-and-hold 가 우월 — 종배/스윙 시스템이 답
- 단저단고는 박스권/잔파동 종목 + 사용자 직관 매매 + Per-Stock stop 조합으로 보조

종목별 sweet spot:
- 삼성전기 (강한 추세 큰 swing): EOD 보유 시 +5.7% (per-stock stop -2.5%)
- 삼성전자 (sigS 잘 발화): 100% 승률 +0.95% (stop -4%)
- 현대차 (잔파동 큼): 모든 룰에서 net 음수 — 단저단고 부적합 종목

매매일지 토론 시점 누적 데이터 (1개월+) 로 weight 재학습 + 한계 검토.

## 종배 매매 정량 룰 (간략)

전체 정의는 [`docs/scalping-strategy.md`](docs/scalping-strategy.md). 핵심만:

- **국면 필터**: KOSPI 200일 이평 위 (대세상승장) — Zeta가 직관 판단도 가능
- **유니버스**: KOSPI + KOSDAQ 전종목 (ETF/ETN/리츠/스팩/펀드 제외)
- **주도섹터 식별 (M5.5 v1)**: 거래대금 50위 → 테마별 breadth + 동일가중 평균상승률 + 회전율 합계 z-score
- **주도주 정의**:
  - (정통/결정 레포트) 주도섹터 내 first-mover 상한가 도달 종목
  - (고주파/09:00~10:30 모니터링) 주도섹터 내 회전율 1위
- **테마 분류**: 코드 내부는 **네이버 금융 테마**, 레포트엔 WICS도 병기
- **종배 후보**: 주도섹터 + 일봉 +20% 이상 마감 (상한가 우선)
- **갭상 확률**: 4-Layer 분석 (전체 / 상한가만 / +종가 위치 매칭 / +고점 시각 매칭)
- **사이징**: 균등 / Kelly / Sharpe 3가지 모두 표시 → Zeta가 선택
- **청산**: 다음날 9:00 KRX 시초 매도 (NXT 청산은 v1)
- **실시간 모니터링 (M6)**: 평일 09:00 자동 ON + 사용자 `/on`/`/off` 24h 토글 (round 18). 주도주 + 사용자 종목 1~2초 갱신, 텔레그램 메시지 편집 방식. polling thread 는 데몬 시작 시 1회 띄움 후 24h 상시

## 데이터 인프라 (중요)

| 데이터 | 소스 | 갱신 주기 |
|---|---|---|
| 일봉 OHLCV | pykrx (네이버 크롤링) | 매일 16:00 cron |
| 종목 마스터 | pykrx + KRX | 매일 |
| WICS 섹터 | wiseindex 크롤링 | 월 1회 |
| 네이버 테마 | 네이버 금융 크롤링 | 월 1회 |
| 장중 거래대금 순위 | KIS API | 11:00, 13:00, 14:00, 14:50 |
| 장중 종목 시세 | KIS API | 위 시점 + 상한가 진입 이벤트 |

**분봉 히스토리는 사실상 확보 불가** (키움도 1년 한정). 따라서 백테스트는 v0에서는 안 한다. 매일 데이터 적재하면서 6개월~1년 후 미니 백테스트 가능해진다.

## 알림 채널 정책

| 시점 | 채널 | 내용 |
|---|---|---|
| **사용자 `/on` ~ `/off`** | **텔레그램 (메시지 편집) + PWA "단저단고 모니터링"** | **실시간 단저단고 모니터링 — 주도주(거래대금∩회전율 1위)/주도주 후보(2위)/사용자 종목 1~2초 갱신. 평일 09:00 자동 ON, /off 로만 종료. PWA 는 한 화면 카드 그리드 + 보유 토글 버튼 (holdings.json input only, KIS 주문 X)** ★ |
| **단저단고 STRONG 발화** | **텔레그램 (별도 send, /on/off 무관)** | **자동 6종 + 수동 + 보유 종목의 mr_grade==STRONG + sigB/sigS 발화 시 1회 push** (kind 전환/STRONG 재진입 시만 재 push). `MR_STRONG_ALERT=1` default. 사용자가 자리 비울 때 놓치는 시점 대비 ★ |
| 09:30 모닝 | 텔레그램 | 시장 국면 + 어제 보유 종목 갭 분석 |
| 11:00 / 13:00 / 14:00 | 텔레그램 | 주도테마 변화, 신규 상한가 |
| 14:50 결정 | 텔레그램 | **최종 종배 후보 + 사이징** ★ |
| 상한가 진입 | 텔레그램 | 즉시 푸시 (이벤트 트리거) ★ |
| 모니터링 카드 내부 (단저단고 시그널/히스토리/주도주 surface 변경) | 텔레그램 (편집) | **별도 푸시 X** — 카드 색상/이모지/단저단고 라인 1줄 통합 표시 |
| 16:00 사후 | 텔레그램 | 상세 마크다운 레포트 (4096자 초과 시 자동 분할) |

★ 가 표시된 것이 가장 중요한 알림이다.

## 개발 시 주의사항

1. **API 키 절대 커밋 X**: `.env` 파일 사용, `.gitignore` 등록
2. **KIS API rate limit**: 초당 20회. 토큰 버킷 또는 throttling 필수
3. **시장 휴일 처리**: KRX 휴장일 캘린더 반영 (pykrx에서 제공)
4. **시간대**: 모든 시각은 Asia/Seoul (KST). UTC 변환 X
5. **수정주가**: pykrx는 수정주가 옵션 있음. 일관되게 사용

## 검증 가능한 사용자 발화 (regression test 자료)

대화록에서 명시된 것 — 백테스트나 검증에 사용:

- "5/4 주도주: 하이닉스, SK스퀘어, 삼성증권, 제룡전기"
- "제룡전기 91,300원에 상한가 칠 때 매수 → 갭상 유력"
- "전기/전선 섹터가 거래대금 상위 다수"
- 박민준의 "+20~30%는 일봉 수익률" 정정

## 자주 빠지는 함정

- 거래대금 순위를 종가 기준으로만 보면 빨리 상한가 친 진짜 주도주 누락 (장중 시점별 추적 필수)
- 거래대금 절대값 1위 = 주도주로 판정 X (항상 대형주). **회전율(거래대금/시총) 1위가 단타 주도주**
- 시총가중 평균을 쓰면 대형주 1종목이 테마 평균을 좌우. **동일가중 평균** 사용
- 상한가 진입 직후 매수는 호가 슬리피지 큼 (백테스트 시 0.2~0.3% 가산)
- 강세장 가정 무너지면 모든 룰 무효화 (regime detector 필수)
- 표본 적은 historical 통계로 Kelly 풀로 박으면 위험 (n<20은 페널티)
- 검증 안 된 자작 가중합 스코어 → 위험. 한국 단타 통설 그대로 따른다
- 텔레그램에 1~2초마다 send → 푸시 폭주. **반드시 editMessageText**
- ETF/ETN/리츠/스팩 미필터 시 KODEX/TIGER/`100030` 같은 종목이 후보로 잡힘
- **호가 잔량 비율만으로 매수 판단 X** — 허매수/스푸핑 함정. 체결강도(VP) + 봉 패턴 + 모멘텀과 조합 점수(Buy.Score)로만 판단
- **Exit.Triggers 매도 트리거 = 카드에만 표시 (별도 푸시 X)**. 자동 주문 코드 작성 절대 X. CLAUDE.md "자동 매매 절대 금지" 정책 유지 — 단타 시스템도 예외 아님
- **M6 모니터링 카드 외 별도 푸시 X (정정 round 17)** — TRANSITION/GRACE/강한 부상/자금 이탈/AVOID/Exit.Triggers 트리거 모두 카드 색상·이모지·사유 한 줄로 통합. 1~2초 갱신만으로 사람이 직접 인지하는 워크플로우. 푸시는 M6 외부(상한가 진입/14:50 결정/16:00 사후 등)만
- **봇 명령 polling 은 24h 상시 (round 18)** — 이전엔 09:00~10:30 cron 안에서만 polling 떠서 운영시간 외 명령은 응답조차 없음. 현재는 `scheduler.run()` 시작 시 polling thread 1회 띄움. `/on`/`/off` 24h 허용, `/start`=`/on` alias, `/pause`=`/off` alias. 10:30 자동 OFF 폐지 — 사용자 임의 시점에 켜고 끔
- **PWA 대시보드 (M7) 도 모니터링 메타 데이터 input 만 허용** — `holdings.json` 토글 / 감시 종목 추가·제거 / `/on`·`/off` 만 POST 가능. **KIS 거래소 주문 영구 X**. PWA 의 buy/sell 버튼은 텔레그램 봇 `/buy`·`/sell` 명령과 **동일 핸들러 재사용** (이중 구현 X). 자세한 정책 매핑은 `docs/dashboard-pwa.md` §3·§6
- **PWA 도 푸시 X** — Web Notifications 은 opt-in 강제, 기본 OFF. 텔레그램 `editMessageText` 정책과 동일 — 카드 1~3초 갱신만으로 사람이 직접 인지하는 워크플로우. 데이터 외부 클라우드 송신 X (집 데스크탑에서 직접 서빙)

## 파일 작성 시 참고 문서

새 코드 작성 전 반드시 읽을 것:

- 데이터 모듈 작성 시: `docs/data-infra.md`
- 단타 (주도주 매매) 분석 모듈 작성 시: `docs/scalping-strategy.md` + **단저단고 재설계 후속 (2026-05-27): `docs/scalping-redesign-2026-05-27.md` ★** — 모멘텀/돌파(Buy.Score) → 분봉 단저단고(intraday mean reversion) 패러다임 전환. 모듈: `src/scalping/bars.py`, `src/scalping/signals/mean_reversion.py`, `src/common/universe.py`, `src/research/backtest_mean_reversion.py`, `src/analysis/mr_alignment.py`. 라이브 dry-run 활성화 (`.env` MONITOR_MEAN_REVERSION=1) — 카드 표시만, 사용자 매매 영향 X
- 종배 분석 모듈 작성 시: `docs/eod-strategy.md`
- Buy.Score 재설계 작업 시: `docs/buy-score-revision-proposal.md`
- 레포트 모듈 작성 시: `docs/report-spec.md`
- 실시간 모니터링 카드/funnel/매수 점수/매도 시그널 설명 (초보자용): `docs/monitoring-guide.md`
- PWA 대시보드 (M7) 작성 시: `docs/dashboard-pwa.md`
- 매매일지 작성 / 매매 평가 요청 처리 시: `docs/trading-journal.md`
- 전체 진행 상황: `docs/plan.md`

## 매매일지 요청 처리 (round 40, Phase 2 운영)

사용자가 다음과 같은 요청을 하면 — "매매일지 작성해줘", "YYYY-MM-DD 매매 평가", "오늘
매매 분석", "이 차트로 매매일지" — **반드시 `docs/trading-journal.md` 먼저 읽고
거기 워크플로우 / 형식 / 톤 가이드 따른다**. 단순 자동 레포트 생성이 아니라 사용자
**감을 시스템화**하기 위한 사후 평가 + 다각도 튜닝 포인트 도출이 본질.

### ★ 매매일지 작성 전 반드시 기억 (2026-05-20 사용자 명시, `docs/trading-journal.md` §0)

1. **사용자 매매 룰 (baseline 가정)**: 매수 = Buy.Score 🟢 STRONG (≥5.0) / 매도 = Exit.Triggers
   청산 시그널 1개라도 발화 OR 매수가 -2%. 사용자는 이 룰을 일관되게 지키려고
   노력. **"시그널 무시" / "룰 위반" / "결정 일관성 부족" 단정 평가 X**.
2. **버튼-실거래 시간차 필수 고려**: trades 의 ts 는 사용자가 시그널 본 시점보다
   수초~수십초 늦음. 매매 ts 의 closest tick 단독으로 보지 X — **반드시
   [-30s, +5s] 윈도우** 의 Buy.Score max 등급 + Exit.Triggers 트리거 발화 history + 매수가 -2%
   도달 여부 함께 확인. 윈도우 내 STRONG / 트리거 발화 있으면 사용자 룰 준수.
3. **시스템 발전 ritual**: 매매일지에서 튜닝 후보 제시 시 — (a) 한국 단타 통설
   검색 (WebSearch / WebFetch) 으로 검증된 기법인지 확인 + (b) 현재 데이터로
   변경 전후 수익률/승률 백테스트 — 두 단계 통과 후만 ritual 진행. 1건 표본 단독
   변경 영구 X. **매매일지 모든 가설에 "통설 검증 필요 + 데이터 검증 필요" 명시**.

입력:
- 사용자 제공 — HTS 차트 이미지 (분봉/일봉/호가, multimodal), 일자, 메모 (선택)
- 시스템 데이터 — `data/tick_logs/YYYY-MM-DD.parquet` (raw jsonl fallback), `data/
  trades/YYYY-MM-DD.parquet`, 필요 시 `data/daily/ohlcv.parquet`
- 분석 도구 — `python -m src.analysis.replay CODE DATE`, `python -m src.analysis.
  regret DATE`. 더 세밀한 분석은 직접 pandas 쿼리.

### ★ 매매일지 요청 시 자동 실행 (2026-05-28 사용자 명시)

매매일지 작성 요청 받으면 다음 두 가지를 **항상 자동으로 같이 실행**:

1. **단저단고 시그널 정합도** — `python -m src.analysis.mr_alignment YYYY-MM-DD`
   - 사용자 매매 ts ±윈도우에 mr_sigB/mr_sigS 발화 여부 자동 평가.
   - 결과 `data/journal/auto/YYYY-MM-DD.md` 저장. 매매일지에 해당 결과 요약 포함.
   - 사용자 매매 vs 단저단고 패러다임 정합도 누적 (검증 데이터).

2. **백테스트 (요청 시)** — 사용자가 "백테스트 해줘" / "백테스트 결과 보자" 같은
   요청 시 `python -m src.research.backtest_mean_reversion` 자동 실행.
   - 결과 `data/backtest/mr_v3_baseline.json` 갱신. 비용 시나리오 3개 (시장가
     0.4% / 지정가 0.2% / 유동리더 0.15%) 자동 계산.
   - 매매일지 작성 중 시그널 변경 가설 나오면 두 번 실행 (변경 전/후) 후 비교.

별도 명시 X — 매매일지 / 백테스트 요청 시 사용자 추가 명령 없이 자동 실행. 결과는
매매일지 본문에 요약 포함.

출력 — Markdown 매매일지:
- 매매 개요 표 / 종목별 분석 (매수·매도 결정 시점 시그널 breakdown / 보유 중 Exit.Triggers
  트리거 timeline / 청산 결정 평가)
- 시스템 시그널 vs 사용자 감 일치도
- 튜닝 포인트 (단기 이번 매매 / 장기 과거 누적, 운전수 가설 시그니처 후보)
- 다음 행동 — 사용자가 다음 매매에서 검증할 가설

⚠ 톤 원칙 (자세한 건 `docs/trading-journal.md` §5):
- "잘 사셨네요" 같은 무비판 옹호 X — 결과 좋아도 결정 과정이 운이면 지적
- 사용자 감의 한계 직시 — 우연히 성공한 패턴이 재현 가능한지 검증 가능한지 분석
- 튜닝 후보는 항상 가설로 제시 — 1건으로 가중치 변경 X (과적합)
- 차트 vs 시스템 데이터 불일치는 시스템 결함 우선 의심
- "느낌상" 같은 표현 X. 모든 평가에 시각/숫자 첨부

매매일지 저장 권고: `data/journal/YYYY-MM-DD.md` — 누적 시 Phase 3 종목별 파라미터
DB / 운전수 가설 검증 데이터셋.

**후속 토론도 같은 파일에 박는다 (round 40 후속, 사용자 명시):** 일지 작성 후
사용자 ↔ Claude 대화 (what-if 시뮬, 가설 제시, 사용자 직관 검증) 까지 같은 파일
끝에 "## 후속 토론 / 피드백" 섹션으로 추가. 틀린 가설도 그대로 보존 — 다음 일지에
같은 가설 재제시 회피 + 누적 패턴 추출용. 형식 / 톤 가이드는 `docs/trading-journal.md`
§1 step 7 + §4 후속 토론 섹션 + §5 톤 원칙 참조.
