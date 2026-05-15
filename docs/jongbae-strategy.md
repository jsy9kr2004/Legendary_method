# 종배 전략 정의 (jongbae-strategy.md)

종배 매매(종가 베팅)의 정량적 룰 정의. 이 문서는 박민준(레전드 채팅방 분석가)의 발화와 일반 한국 단타 노하우를 기반으로 정리된 것이다.

## 한 줄 정의

장중 거래대금 30위 내에서 동일 테마가 3개 이상 출현하면 **주도테마**로 인식하고, 그 테마 안에서 일봉 +20% 이상 마감하는 종목을 추적해 **상한가 진입 순간 또는 종가**에 매수, **다음날 시초**에 매도하여 갭 차익을 노린다.

---

## 핵심 원리

### 종배의 알파 구조

종배가 노리는 알파는 정확히 **close-to-open 갭**이다. 일중 추가 상승 노리는 게임이 아니다.

학술적으로도 미국 시장의 경우 지난 30년간 거의 모든 수익이 close-to-open 구간에서 발생했다는 연구가 있고, 한국 시장도 유사한 경향이 보고된다. 즉 종가 매수 + 시초 매도라는 행위 자체가 약한 양의 기댓값을 갖는다.

다만 **무차별 종가 매수는 노이즈**다. 필터링이 알파다.

### 필터링 3중 구조

```
[Filter 1] 시장 국면     → 대세상승장에서만
[Filter 2] 주도테마      → 자금이 몰리는 곳
[Filter 3] 종목 강도     → 일봉 +20% 이상 양봉
```

여기에 historical 통계로 진입 강도(사이징)를 결정한다.

---

## 정량 룰

### R1. 시장 국면 필터

**원칙:** 대세상승장에서만 종배. 약세장에서는 룰 무효.

**자동 지표 (레포트 상단 표시):**
- `kospi_above_ma200`: KOSPI 종가 > KOSPI 200일 이동평균
- `kospi_60d_return`: KOSPI 60일 수익률
- `vkospi`: 변동성 지수 현재값
- `bear_candle_ratio_20d`: 직전 20거래일 음봉 비율

**최종 판정:** Zeta가 직관 판단 (자동 게이팅은 안 함). 레포트 상단에 지표만 노출.

### R2. 유니버스

**대상:** KOSPI + KOSDAQ 전종목

**제외:**
- ETF, ETN, ELW, ELS, ELB, 우선주, 스팩
- 리츠, 펀드형 종목 (`1XXXXX` 코드 다수)
- 관리종목, 거래정지 종목
- 종목명 패턴 차단: `KODEX`, `TIGER`, `KBSTAR`, `ARIRANG`, `KINDEX`, `HANARO`, `RISE`, `ACE`, `SOL`, `WOORI`, `PLUS`, `KOSEF` 등 ETF 운용사 prefix

**시총/거래대금 컷:** 적용 X. 레포트에 정보만 표시. (v0 단순화)
다만 **회전율 계산을 위해 시총 데이터 적재는 필수** (M5.5).

### R3. 주도테마(주도섹터) 식별

**시점:** 11:00, 13:00, 14:00, 14:50 (정기 4회 스냅샷) + M6 `/on` 상태 1~2초 모니터링 (24h 사용자 토글, 평일 09:00 자동 ON, round 18)

**v0 (Sonnet 1차 구현, 폐기 예정):**
```
거래대금 30위 내 동일 테마 ≥ 3종목 → 주도테마
```
→ 한계: 하이닉스/삼전 같은 대형주가 항상 30위를 차지해 "반도체"가 늘 주도섹터로 잡힘. 단타 자금 쏠림과 무관.

**v1 (M5.5에서 도입, 한국 단타 통설 반영):**
```
[유니버스 컷]
1. 거래대금 50위 추출 (30→50 확장)
2. ETF/ETN/리츠/스팩/펀드 제외 (R2 강화)

[테마 단위 집계 — 한 종목이 N개 테마에 속함]
3. 각 테마에 대해:
   a. breadth     = 테마 내 +5%↑ 종목 수, +10%↑ 종목 수
   b. avg_return  = 테마 구성종목의 등락률 평균(동일가중, 시총가중 X)
   c. turnover    = 테마 구성종목의 (거래대금/시총) 합계
4. (a) (b) (c) 각각 z-score 정규화 → 합산 = theme_score
5. theme_score 상위 N개 = 주도섹터 (N=3 기본)
```

**핵심 — 동일가중 평균:** 시총가중 평균을 쓰면 하이닉스 1종목이 반도체 테마 평균을 좌우함. **동일가중**이라야 단타 자금이 실제로 골고루 들어왔는지 측정 가능.

**핵심 — 회전율(turnover) 사용:** 거래대금 절대값 합계는 대형주 편향. **시총 대비 거래대금**이 단타 자금 유입의 진짜 강도.

**임계값:** N=3, 가중치 (1:1:1) 시작. 운영 중 튜닝.

**테마 분류 우선순위:**
- 코드 내부 판정: **네이버 금융 테마**
- 레포트 표시: 네이버 테마 + WICS 중분류 병기

**중요 — 거래대금 순위의 함정:** 종가 기준 거래대금 순위로 보면 빠르게 상한가 친 진짜 주도주가 누락된다. 반드시 **시점별 누적 거래대금**으로 추적할 것.

### R3'. 주도주 식별 (M5.5에서 재정의)

주도주는 두 가지 컨텍스트로 분리해 정의한다. 같은 함수 X.

**(가) 정통 주도주 — post-limit-up, 결정 레포트(14:50) 용**
- 정의: 주도섹터 내 **first-mover 상한가 도달** 종목
- 의미: 이미 상한가 친 종목 중 가장 빨리 도달한 것 = 박민준 패턴의 "들이미는" 대상
- 구현: `identify_leading_stocks()` (M2 기존 유지)

**(나) 고주파 주도주 — pre-limit-up, M6 모니터링 용 (주로 09:00~10:30 활발, 24h 사용자 토글)**
- 정의: 주도섹터 내 **회전율 1위** = 주도주
- 거래대금 절대값으로 1위 잡으면 하이닉스/삼전이 나옴 → 회전율(거래대금/시총)로 자동 배제
- 의미: 상한가 도달 전 진입해야 매수 가능 (상한가 치면 호가 닫힘)
- 한 섹터에 여러 후보, 한 종목이 여러 섹터에 속할 수 있음 (1:1 매핑 X)
- 구현: `identify_early_morning_leaders()` 시그니처 변경 (M5.5)

**참고 지표 (점수화 X, 표시만):** 상승률, 거래대금 절대값, 분봉 거래대금, 체결강도, 호가잔량, 외국인/기관/프로그램 순매수.

**주도주 교체 상태 머신 (M5.5/M6):**

```
[NORMAL]   주도주 a1만 표시
   ↓ a2 가속배율 ≥ 5배 AND 분봉거래대금 ≥ 20억 AND a2 회전율 ≥ a1 × 0.6
[TRANSITION]  a1 + a2 둘 다 표시 — 교체 가능성
   ↓ a2 회전율 > a1 회전율 (역전)
[GRACE]     a1 + a2 둘 다 5분간 표시 — 엎치락뒤치락 대비
   ↓ 5분 경과
[NORMAL]   a2만 표시
```

**역전 시 GRACE 5분 동안 a1이 다시 a2 추월하면** → NORMAL 복귀(a1만), 카운트다운 무효.
**TRANSITION 에서 a2 회전율이 a1 × 0.4 이하로 3분 지속** → 후보 탈락, NORMAL 복귀.

**임계값 (M5.5 시작값, 튜닝 가능 — `src/jongbae/config_thresholds.py`):**

| 항목 | 값 | 근거 |
|---|---|---|
| 가속배율 | 5배 | 실무 화살표 신호 10배의 절반 — 부상 **시작** 포착 |
| 강한 부상 강조 | 10배 | 실무 화살표 신호 표준 |
| 분봉 거래대금 컷 | 20억 | i-whale 표준, 잡주 노이즈 제거 |
| 회전율비(부상 후보) | a2 ≥ a1 × 0.6 | 추격 가능 거리 |
| 실제 교체 | a2 > a1 | 단순 역전 |
| GRACE 유예 | 5분 | 한국 분봉(1~5분) 운영 단위 |
| 후보 탈락 | a2 < a1 × 0.4, 3분 지속 | 오포착 정정 |

### R4. 종배 후보 추출

**조건 (모두 만족):**
- (a) R1, R2, R3 통과
- (b) 일봉 종가 수익률 ≥ +20%
- (c) historical 유사 사례 ≥ 5건 (표본 부족 시 후보 제외)

**진입 우선순위:**
1. **1순위:** 주도테마 내 first-mover 상한가 도달 종목 (도달 순간 매수)
2. **2순위:** 일중 +28%↑ 찍고 +20~25% 영역으로 정리된 종목 (종가 매수)
3. **3순위 (드묾):** 시황 따른 예외 케이스 — v0에서는 무시

**제외 (애매한 케이스):**
- +28% 찍고 안 빠지고 그대로 마감 (상한가 못 갔는데 자리 잡힘)
- 일중 +30% 찍고 +5%로 떡락 (시세 죽음)
- 비주도테마 종목 (혼자 잘 가는 케이스 거의 없음)
- 강한 양봉 후 다음날 음봉 빈번한 종목 (추후 정량화 — v1)

### R5. Historical 갭상 통계 (4-Layer)

각 종배 후보에 대해 lookback 1년(252거래일)으로 다층 분석.

**Layer 1 — 전체 강한 양봉 (가장 넓음)**
- 조건: 일봉 수익률 ≥ +20%
- 의미: 통계적 유의성 우선

**Layer 2 — 상한가 사례만**
- 조건: 일봉 수익률 ≥ +29.5% (상한가)
- 의미: 강한 시그널만 추출

**Layer 3 — 종가 위치 매칭**
- 조건: Layer 2 + (오늘과 종가 위치 ±2% 일치)
- 의미: 오늘과 가장 유사한 마감 형태

**Layer 4 — 종가 위치 + 고점 도달 시각 매칭**
- 조건: Layer 3 + (오전 도달/오후 도달 일치)
- 의미: 가장 정밀한 매칭, 표본 작음 ⚠

**계산 메트릭 (각 Layer별):**
- 사례 수 `n`
- 갭상 확률 `p` (다음날 시가가 전일 종가보다 높은 비율)
- 평균 갭 `avg_gap`
- 중앙값 갭 `median_gap`
- 갭 표준편차 `std_gap`
- 평균 다음날 종가 수익률 `avg_close_return`

**갭상 정의:** 다음날 시가가 전일 종가보다 +0% 초과 (양수면 모두). 레포트엔 갭 % 그대로 노출.

### R6. 사이징

3가지 방법 모두 계산해서 레포트에 표시. Zeta가 보고 선택.

**방법 1: 균등**
```
weight_i = 1 / N  (N = 시그널 종목 수)
```

**방법 2: Kelly Criterion**
```
f* = p/L - q/W
  where p = 갭상 확률
        W = 평균 갭 (양수, 갭상 시)
        L = |평균 갭| (양수, 갭하락 시)
        q = 1 - p

표본 보정:
  n < 5  → 시그널 제외
  n < 10 → f* × 0.3
  n < 20 → f* × 0.6
  n ≥ 20 → f* × 0.8 (Half Kelly 권장)

캡: max 25% per stock
```

**방법 3: Sharpe-like**
```
expected = p × avg_gap_when_up
score    = expected / std_gap

weight_i = score_i / sum(score)
```

**기준 Layer:** 사이징 계산은 **Layer 3** (종가 위치 매칭) 기준으로 함. 표본 부족하면 Layer 2로 fallback.

### R7. 청산

**v0 (단순):**
- 다음날 09:00 KRX 시초가 매도 (단일가)
- "갭상승하면 시초에 바로 익절" 원칙
- 욕심 부려 일중 추가 상승 노리지 X

**R7' 시초가 분할 룰 (round 30, P3-2)** — 통설(WikiDocs 종가베팅, brokdam):
- **시초 ≤ +1% (또는 마이너스)** → 전량 매도. 갭 미발생 / 종배 실패. 보유 의미 X.
- **시초 +1% ~ +6%** → 전량 익절. 정상 갭, 단타 종료.
- **시초 ≥ +6%** → 30~50% 분할 익절 (40% 적용), 60% 관망. 강한 갭에선 추가 슈팅 노림.

구현: `src/jongbae/jongbae_exit.py:evaluate_jongbae_open_exit()` — 시초가 + 전일종가 입력으로 `JongbaeExitDecision(action, partial_ratio, reason)` 반환. **자동 주문 X** (CLAUDE.md 정책) — 09:00 텔레그램 알림 메시지에 권고 표시만.

**v1 (TODO):**
- NXT 프리장 (08:00~08:50) 활용
- 종목별 NXT 거래 가능 여부 체크 후 우선 청산
- 갭하락 시 30분 내 손절/홀딩 판단 룰

### R8. 매매 실행

**프로그램 역할:** 레포트 생성 + 알림 발송까지.
**사람(Zeta) 역할:** 모든 매수/매도 실행.

프로그램은 절대 자동 매매를 하지 않는다.

### R9. 실시간 모니터링 (M6 신설)

**시간 (round 18):** 24h 사용자 토글. 평일 09:00 자동 ON, `/off` 로만 종료 (10:30 자동 OFF 폐지). 봇 명령 polling 은 데몬 시작 시 1회 띄워 24h 상시. 휴장일/주말도 `/on` 가능 (KIS 시세 변동 없으므로 카드는 정적 유지).

**대상 종목:**
- (자동) 주도섹터 내 주도주 (R3'(나)) — 보통 1~2개. 교체되면 자동 갱신
- (수동) 사용자가 텔레그램 채팅에 6자리 코드 입력 → 토글 추가/해제

**갱신 채널/방식:**
- 채널: 텔레그램 (`editMessageText`로 메시지 1개 유지 갱신, 푸시 알림은 신규 종목 진입 시점만)
- 종목 1~2개 → 2초 / 3~5개 → 3초 / 6~10개 → 5초 / 10개 초과 추가 거부
- 별도 TUI/웹 대시보드는 v1 선택사항

**메시지 표시 항목 (1초 단위 갱신):**

(공통)
- 현재가, 등락률, 상한가 마크
- 거래대금, 회전율(시총 대비)
- 분봉 거래대금 가속 — `vol_accel_1m` / `vol_accel_5m` (R11)
- 체결강도 VP + 5MA / 20MA (R10)
- 최근 5분봉 패턴 — 양봉/음봉/도지, 윗꼬리·아랫꼬리 비율 (R12)
- 위치/맥락 — 당일 고점 대비 %, 시초가 대비 %, 전일 종가 대비 % (R12.5)
- 외국인/기관/프로그램 순매수 (장 시작 누적)
- 호가잔량 매수/매도 비 — **보조 표시만** (R10 도입과 함께 메인에서 강등)
- **매수 점수 + 등급** 🟢STRONG / 🟡WATCH / ⚫NEUTRAL / 🔴AVOID (R14)
- 다이버전스 마크 — Bearish/Bullish 발생 시 (R13)

(감시 모드, R15)
- 매수 점수 사유 한 줄 (어떤 가산점/감산점이 컸는지)

(보유 모드, R15 — `/buy` 로 진입)
- 매수가, 현재 손익 %
- 손절선 (-1.5%, 진입 봉 저점, 5분 이평) — **알림만, 실주문 X**
- 익절선 1차 (+2%, 1/3 청산 권장), 2차 (+3.5%, 1/3 청산 권장), 잔여 1/3 트레일링(-1.5%)
- 시간 손절 카운트다운 (진입 후 N분 내 +0.5% 미달 시 권장, 기본 N=10)
- 매도 트리거 발화 사유 (체결강도 100 이탈 등)

**알림 정책 (M6 카드 통합):**

M6 모니터링 카드는 1~2초 간격 `editMessageText` in-place 갱신 — **카드 외 별도 푸시 알림은 모두 폐기**. 사용자가 `/on` 한 시점부터 `/off` 칠 때까지 텔레그램 채팅을 띄워놓고 카드 색상/이모지/사유 한 줄 변화로 모든 상태를 직접 인지하는 워크플로우 (round 18 이후 24h 사용자 토글).

다음 이벤트는 모두 카드 안에 표시 (별도 메시지 발송 X — round 19 에서 실코드 반영):

| 이벤트 | 카드 표시 위치 |
|---|---|
| TRANSITION (부상 후보 감지) | a1 카드 헤더 `🔥 부상 후보 a2: NAME (회전율 X.X%)` |
| GRACE (실제 교체 후 5분 유예) | a1 카드 헤더 `🔄 GRACE — a2: NAME (회전율 X.X%)`, a2 카드 헤더 `[GRACE m:ss 남음]` 카운트다운 |
| 부상 후보 (거래대금 급증, RISING) | 신규 카드 자체가 발송됨. 풀에서 빠지면 카드 자동 제거 (시간 만료 X) |
| 강한 부상 (5분봉 가속 10배+ & 20억+) | 5분봉가속 라인 `🟢⚡ ... (강한 부상)` |
| 자금 이탈 (5분봉 가속 < 0.6) | 5분봉가속 라인 `🔴⚠ ... (자금 이탈)` |
| 1분봉 부상 (1분봉 가속 5배+ & 10억+) | 1분봉가속 라인 `🟢⚡ ... (1분봉 부상)` |
| 1분봉 급감 (1분봉 가속 < 0.4) | 1분봉가속 라인 `🔴⚠ ... (1분봉 급감)` |
| 호가 역전 (🟢→🔴 / 🔴→🟢) | 호가 라인 색상 갱신 (별도 alert X) |
| R14 STRONG/WATCH/NEUTRAL/AVOID | 카드 헤더 등급 이모지 + 점수 |
| R14 진입 STRONG (감시 모드) | 카드 헤더 등급 변화로 충분 |
| R15 A 손절선 도달 (보유 모드) | 카드 헤더 🛑, 손절선 라인 색상 + ✅ 마크 |
| R15 B 익절선 도달 (보유 모드) | 카드 익절선 라인 ✅ + "도달" 표시 (멱등 1회) |
| R15 C 시그널 청산 (보유 모드) | "🔔 매도 트리거 상태" 섹션 ❌→✅ 갱신 |

푸시 ON 메시지가 발생하는 경우는 **M6 외부 이벤트**만:
- 🚨 상한가 진입 (모니터링 안 하던 종목 포함, `src/report/event.py`)
- 자동 주도주 신규 추가 (카드가 새로 생기는 첫 발송 — `editMessageText` 대상이 아직 없으므로 send 1회. 이후엔 갱신만)
- 14:50 결정 레포트 등 정기 알림

**갱신 방식 보강:**
- 채팅이 다른 메시지로 밀려 안 보이게 되는 일이 없으니 (별도 알림 폐기 → 카드만 1개 메시지로 유지), 메시지 N분마다 삭제/재발송 패턴은 **도입 X**.
- `editMessageText`로 동일 메시지 ID를 계속 갱신만.

**명령어 (round 18):**
- `/on` 또는 `/start` — 모니터링 ON (멱등). 24h 허용. `/start` 가 텔레그램 기본 명령이라 alias
- `/off` 또는 `/pause` — 모니터링 OFF (멱등). 카드 메시지 정리 후 정지
- `/list` — 현재 모니터링 중인 종목 출력
- `/clear` — 수동 추가분만 해제 (자동 주도주는 유지)
- `091340` (6자리 숫자) — 토글 추가/해제 (감시 모드). 24h 허용
- `/buy 091340` — 보유 모드 진입 (round 20). 매수가는 모니터링 중인 최근 시세에서 자동 보충 → 손절/익절선 즉시 계산. 24h 허용
- `/buy 091340 91300` — 매수가 명시 (자동 보충값을 신뢰하지 못할 때, 예: 다른 HTS 에서 슬리피지 있게 체결)
- `/buy 091340 91300 5` — 매수가 + 5분 시간 손절(기본 10분) 오버라이드
- `/sell 091340` — 감시 모드 복귀 (보유 청산)
- `/status 091340` — 해당 종목 풀 카드 강제 재발송
- 미정의 명령은 무시 (스팸 방지)

### R10. 체결강도 (Volume Power, VP)

**배경:** 호가 잔량 비율은 허매수/스푸핑에 취약하고 매도자가 시장가로 던지면 매도잔량이 줄어 비율만 커지는 함정. → 메인 신호에서 강등, 체결강도가 메인.

**정의:**
```
VP = 능동적 매수체결량 / 능동적 매도체결량 × 100   (당일 누적, KIS 제공)
VP_5MA  = 직전 5분간 VP 평균
VP_20MA = 직전 20분간 VP 평균
```

**의미:**
- VP = 100 → 매수체결 = 매도체결 (균형선)
- VP > 100 → 매수 우세 (체결 기준, 호가 잔량과 별개)
- VP < 100 → 매도 우세

**데이터 소스:** KIS API `inquire-ccnl` (`체결강도` 필드 직접 사용). 5MA/20MA는 장중 메모리 시계열에서 산출(영구 적재는 v1).

**임계 (R14 매수 점수에서 사용):**

| 조건 | 의미 |
|---|---|
| VP > 110 AND VP_5MA > 100 | 강한 매수 체결 우세 (+2점) |
| VP < 100 | 매수 압력 약함 (-2점) |
| VP_5MA 가 100 하향 돌파 | 보유 모드 매도 트리거 (R15 C1) — 카드 트리거 상태에 ✅ |

**호가 잔량과의 관계:** 호가 잔량은 보조 표시만 유지. 가중치 +0.5점(메인 가중치 -2 ~ +2 와 비교해 약함).

### R11. 거래대금 가속 — 다중 윈도우

**배경:** 기존 가속배율 정의는 "현재 5분봉 거래대금 / 직전 30분 평균" 단일 분모. 단타 의사결정엔 1분 단위가 더 빠른 변화 포착에 유리. 두 윈도우를 병행한다.

**정의:**
```
vol_accel_1m = 최근 1분 거래대금 / 직전 5분 평균 분당거래대금
vol_accel_5m = 최근 5분 거래대금 / 직전 20분 평균 5분 거래대금
```

(기존 R3' 표 "가속배율 5배 / 10배" 임계는 분모 30분 윈도우 기준이며 주도주 교체 상태머신용으로 그대로 유지. R11은 매수 점수/매도 트리거용으로 별도 운영.)

**임계 (R14 매수 점수):**

| 조건 | 점수 |
|---|---|
| vol_accel_5m > 1.2 AND vol_accel_1m > 1.0 | +1 (자금 유입 가속) |
| vol_accel_1m < 0.5 | -1 (자금 고갈) |
| vol_accel_5m > 2.0 | +1 (강한 가속) |

**임계 (R15 매도 트리거):**

- vol_accel_1m < 0.5 가 2분 연속 지속 → 보유 모드 시그널 청산

### R12. 봉 패턴 분석

**배경:** "1분봉 가속 감소"가 양봉 정체인지 큰 음봉인지 R11만으론 구분 불가. 봉 자체 형태를 별도 시그널로.

**기준:** 최근 5분봉(완성봉, 진행 중 봉 제외).

**정의:**
```
candle_type   = "bullish" if close > open else "bearish" if close < open else "doji"
body          = |close - open|
upper_wick    = (high - max(open, close)) / max(high - low, ε)
lower_wick    = (min(open, close) - low) / max(high - low, ε)
```

(ε = 1e-9, 0 division 가드)

**임계 (R14 매수 점수):**

| 조건 | 점수 |
|---|---|
| candle_type = bullish AND upper_wick < 0.3 | +2 (장대양봉) |
| candle_type = bearish OR upper_wick > 0.4 | -2 (윗꼬리 음봉/긴 윗꼬리) |

**임계 (R15 매도 트리거):**

- candle_type = bearish AND upper_wick > 0.5 → 보유 모드 시그널 청산

### R12.5. 위치/맥락 정보

**필드 (표시용 + 진입 조건):**
```
dist_from_intraday_high = (current - intraday_high) / intraday_high × 100   (음수)
dist_from_open          = (current - open) / open × 100
dist_from_prev_close    = (current - prev_close) / prev_close × 100
vi_triggered_at         = 발동 시각 (datetime | None)
vi_elapsed_sec          = 발동 후 경과초 (None if 발동 없음)
```

**임계 (R14 매수 진입 필수조건):**

- 매수 점수 계산과 별도로, **진입 필수조건 = `dist_from_intraday_high ≥ -2.0%` (추격매수 방지)**. 미충족 시 등급과 무관하게 진입 비권장.

**VI 데이터:** KIS API에서 직접 endpoint 미확인 — v0 에서는 분봉 가격/거래대금 급변(±10% within 1분) 기반 휴리스틱으로 placeholder, 정밀 추적은 v1 TODO.

### R13. 가격-체결강도 다이버전스

**정의 (5분 윈도우):**
```
price_5m_change = (current - close_5m_ago) / close_5m_ago × 100
vp_5ma_change   = VP_5MA(now) - VP_5MA(5분 전)

bearish_divergence = (price_5m_change > 0) AND (vp_5ma_change < 0)   # 고점 신호
bullish_divergence = (price_5m_change < 0) AND (vp_5ma_change > 0)   # 매집 신호
```

**임계 (R14 매수 점수):**

| 조건 | 점수 |
|---|---|
| bullish_divergence | +2 |
| bearish_divergence | -2 |

**임계 (R15 매도 트리거):**

- bearish_divergence 발생 시 보유 모드 시그널 청산 (즉시)

### R14. 매수 점수 + 등급

**배경:** 기존 "개별 시그널마다 색상 부여" 방식은 호가 잔량 하나로 초록불 켜지는 가짜 매수 신호 발생(흥아해운 케이스). 조합 점수 기반 등급으로 통일.

**경고:** 임계값/가중치는 **한국 단타 통설 조합**이며 검증 데이터 누적 전엔 추정치. 흥아해운 회귀 + 추가 5~10 케이스 미통과 시 단순 룰(VP < 100 AND vol_accel_1m < 0.5 → AVOID)로 폴백.

**점수 산정:**
```
score = 0

# 거래대금 (1차 필터, 약한 가산)
if volume_turnover_rank ≤ 10:                                 score += 1

# 가격 모멘텀 (R11 가속)
if vol_accel_5m > 1.2 and vol_accel_1m > 1.0:                 score += 2
if vol_accel_5m <= 0.8 and vol_accel_1m <= 0.5:               score -= 3   # 강한 페널티
# 감속(WEAK)은 ≤ — "0.8 이하" 한국 단타 통설 표현 부합. 가속(STRONG)은 strict >.

# 봉 패턴 (R12)
if candle_type == "bullish" and upper_wick < 0.3:             score += 2
if candle_type == "bearish" or upper_wick > 0.4:              score -= 2

# 체결강도 (R10)
if vp > 110 and vp_5ma > 100:                                 score += 2
if vp < 100:                                                  score -= 2

# 거래대금 가속 추가
if vol_accel_1m > 2.0:                                        score += 1
if vol_accel_1m < 0.5:                                        score -= 1

# 다이버전스 (R13) — round 27 (P2-1): 통설 외 약신호라 ±2 → ±1 강등
if bearish_divergence:                                        score -= 1
if bullish_divergence:                                        score += 1

# R14d 거래량 비율 검증 (round 28, P2-2) — 통설(상따): 전일 대비 1~3배 정상, 10배↑ 과열
if 1.0 ≤ volume_ratio_vs_prev_day ≤ 3.0:                      score += 0.5
if volume_ratio_vs_prev_day ≥ 10.0:                           score -= 1

# 호가 잔량 (약화 — R10 도입으로 강등)
if bid_ask_ratio > 3.0:                                       score += 0.5

# R14a VWAP 위치 (round 23, P0-1) — 통설 단타 핵심 지표
# VWAP = 거래량 가중 평균 = 장중 세력 평단가의 근사.
# 가격이 VWAP 위면 매수 우위, 아래면 매도 우위.
if price_vs_vwap_pct ≥ +0.3:                                  score += 1
if price_vs_vwap_pct ≤ -0.3:                                  score -= 1

# R14b 5/20분 이평 위치 (round 24, P0-2) — 통설 단타 기본
# 5분/20분 SMA = 1분봉 5개/20개 close 평균. 정배열/역배열.
# R15 A3 (5분 이평 이탈 청산) 와 대칭 — 진입에선 가격>MA5 가산.
if price_vs_ma5 ≥ +0.3 and price_vs_ma20 ≥ +0.3:              score += 1  # 정배열
if price_vs_ma5 ≤ -0.3 and price_vs_ma20 ≤ -0.3:              score -= 1  # 역배열

# R14c 상한가 진입 시간 가산 (round 25, P1-1) — 일중 first-mover 강도
# 통설(상따): "9~10시 진입, 9:30 이내가 가장 강한 상한가". 상한가 도달 시각
# 기준. 도달 안 했으면 None (무가산).
if limit_up_hit_time < 09:30:                                 score += 1
elif limit_up_hit_time < 10:30:                               score += 0.5
```

**등급:**
```
score ≥ 5  → 🟢 STRONG   (강한 매수 후보)
score ≥ 2  → 🟡 WATCH    (지켜볼 만함)
score ≥ -1 → ⚫ NEUTRAL  (관망)
else       → 🔴 AVOID    (회피)
```

**진입 필수조건 (등급과 별도, AND):**
- 거래대금 회전율 상위
- VP > 110 AND VP_5MA > 100
- vol_accel_5m > 1.2 AND vol_accel_1m > 1.0
- candle_type = bullish AND upper_wick < 0.3
- dist_from_intraday_high ≥ -2.0% (추격매수 방지)

→ 점수 5점 이상이라도 필수조건 미충족이면 텔레그램에 "STRONG (필수조건 미충족: 추격구간)" 같이 사유 명시.

**검증 케이스 — 흥아해운 회귀:**
입력(거래대금 1316억 1위, 회전율 +19.4%, vol_accel_5m=0.8, vol_accel_1m=0.4, 호가 5.3배, 윗꼬리 음봉 가정, VP=95, VP_5MA=98) → **점수 ≤ -3, 등급 🔴 AVOID**.

### R15. 매도 트리거 + 상태 머신 (감시/보유 모드)

**상태:**
```
[감시 모드]  /buy 091340 91300                  [보유 모드]
     ←─────────────────────────────────────────────→
                /sell 091340  또는  매도 트리거 알림 후 사람 수동 청산
```

**보유 모드 진입 시 즉시 계산:**
```
stop_loss        = entry_price × 0.985        (R15-A 손절선)
take_profit_1    = entry_price × 1.020        (R15-B 익절 1차, 1/3)
take_profit_2    = entry_price × 1.035        (R15-B 익절 2차, 1/3)
trailing_stop    = high_since_entry × 0.985   (R15-B 잔여 1/3 트레일링)
time_stop_sec    = N분 (기본 N=10) — 진입 후 +0.5% 미달 시 알림
entry_bar_low    = 진입 직전 1분봉 저점
```

**매도 트리거 (OR, 하나라도 발동 시 푸시):**

| 트리거 | 조건 | 우선순위 |
|---|---|---|
| A1. 손절 — 가격 | 현재가 ≤ stop_loss | 최우선 |
| A2. 손절 — 봉 저점 | 현재가 < entry_bar_low | 최우선 |
| A3. 손절 — 이평 이탈 | 5분봉 종가 < 5분 이평 | 최우선 |
| A4. 시간 손절 | 진입 후 N분 경과 + 현재 +0.5% 미달 | 최우선 |
| A5. EOD 컷오프 | now ≥ 14:45 AND 가격 < 5분이평 AND 직전 분봉 음봉 | 최우선 |
| B1. 익절 1차 | 현재가 ≥ take_profit_1 (1회만 발화) | 정상 |
| B2. 익절 2차 | 현재가 ≥ take_profit_2 (1회만 발화) | 정상 |
| B3. 트레일링 | 현재가 ≤ trailing_stop AND B1 발화 후 | 정상 |
| C1. 체결강도 이탈 | VP_5MA 가 100 하향 돌파 | 시그널 |
| C2. Bearish Divergence | R13 bearish_divergence True | 시그널 |
| C3. 자금 고갈 | vol_accel_1m < 0.5 (2분 연속) | 시그널 |
| C4. 윗꼬리 음봉 | R12 candle_type=bearish AND upper_wick>0.5 | 시그널 |
| C5. VI 재상승 실패 | vi_triggered_at 후 5분 내 고가 회복 X | 시그널 |

**모든 트리거 발화 = 보유 모드 카드의 "🔔 매도 트리거 상태" 섹션에 표시 (별도 푸시 X).** 사용자가 모니터링 카드를 보고 직접 인지 + 청산 여부 결정. 실주문은 Zeta 직접. 본 프로젝트는 자동 매매 영구 미지원 (CLAUDE.md "자동 매매 절대 금지" 정책).

**보유 모드 카드 갱신:** 트리거가 발화해도 카드는 계속 갱신(현재가/손익/남은 손절·익절 거리 표시). `/sell` 들어오기 전엔 자동으로 감시 모드 복귀 X (사람이 청산 여부 확인 후 명시적 `/sell` 필요).

**카드 시각 강조 (사람이 빨리 인지하도록):**
- 손절선 도달 (A1~A4) — 헤더 🟡 HELD → 🛑 STOPPED, 발화 라인 빨간 ✅
- 익절선 도달 (B1/B2) — 라인 옆 ✅ + "도달" 텍스트 (1회 멱등)
- 시그널 청산 (C1~C5) — "🔔 매도 트리거 상태" 섹션의 해당 줄 ❌→✅

---

## 알림 시점

| 시점 | 종류 | 핵심 내용 |
|---|---|---|
| 09:30 | 모닝 정기 | 시장 국면 + 보유 종목 갭 분석 |
| 11:00 | 1차 추적 | 거래대금 30위, 주도테마 1차 식별 |
| 13:00 | 2차 추적 | 변화 감지, 신규 상한가 |
| 14:00 | 3차 추적 | 주도테마 굳어짐 확인 |
| **14:50** | **결정 레포트** ★ | **종배 후보 + Historical + 사이징** |
| **상한가 진입** | **이벤트 트리거** ★ | **즉시 푸시 (장중 어느 때나)** |
| 16:00 | 사후 정기 | 시간외 단일가 + 다음날 갭 예측 |

★ 표시는 종배 의사결정에 가장 중요한 두 알림이다.

---

## 검증 가능한 사용자 발화

대화록에서 명시된 것 — 백테스트나 검증에 사용 가능:

| 날짜 | 종목 | 매매 | 가격/근거 |
|---|---|---|---|
| 2025-05-04 | 제룡전기 | 매수 | 91,300원 (상한가 도달 순간) |
| 2025-05-04 | 주도주 후보 | — | 하이닉스, SK스퀘어, 삼성증권, 제룡전기 |
| 2025-05-04 | 거래대금 상위 | — | 전기/전선 섹터 다수 |

추가 검증 발화는 `docs/test-cases.md` (작성 예정)에 누적.

---

## 정정 이력

대화 과정에서 발견된 오해/정정 기록:

| Round | 잘못 알았던 것 | 정정 |
|---|---|---|
| 1 | 8:30 시간외에서 갭 익절 가능 | KRX 시간외는 어제 종가 고정 → 9:00 단일가가 첫 갭 |
| 2 | -20~30% = 일중 떡락폭 | +20~30% = 일봉 상승률 |
| 3 | 종가에 매수 | 상한가 진입 순간이 best entry |
| 3-add | 종가 거래대금 순위로 주도섹터 식별 | 장중 누적 거래대금으로 실시간 |
| 4 | 9:00 KRX 시초가 첫 청산 가능 시점 | NXT 프리장 08:00부터 가능 (v1) |
| 4-add | 9:00~9:30이 청산 윈도우 | 시초에 바로 익절 정석 |
| 5 | 장마감 후 16:00 결정 레포트 | **14:50 결정 레포트** (장마감 전) |
| 6 | Layer 4 (고점 도달 시각 매칭) v0 구현 가능 | 분봉 히스토리 부재로 v0 미구현. Layer 1~3만 사용. v1에서 매일 분봉 적재 누적 후 구현 (`src/jongbae/historical.py` `layer4` 슬롯에 안내 메시지) |
| 7 | "거래대금 30위 ≥3종목"이 주도섹터 식별 충분 | 대형주(하이닉스/삼전) 편향 심함. 단타 자금 쏠림과 무관. → 테마 단위 breadth + 동일가중 평균상승률 + 회전율 합계 z-score (R3 v1) |
| 8 | 주도섹터 내 거래대금 1위 = 주도주 | 거래대금 절대값으로는 항상 대형주가 1위. → 회전율(거래대금/시총) 1위로 변경 (R3'(나)) |
| 9 | 주도주 1:1 매핑 (섹터당 1개), criterion='volume/return/both'로 분기 | 검증 안 된 자작 종합 스코어. 정설은 단순 회전율 1위 + 자금 흐름 추적. criterion 필드 폐기 |
| 10 | "수익률 좋은 주도섹터/주도주"는 우리만의 지표로 정의 가능 | 검증되지 않은 자작 공식은 위험. 한국 단타 통설(테마 상승률/회전율/breadth)을 그대로 따른다 |
| 11 | 09:00~10:00 모니터링 1회 1메시지 발송 1800회 | 푸시 알림 폭주. → `editMessageText`로 메시지 1개 유지 갱신, 푸시는 신규 종목 진입 시점만 |
| 12 | 주도주 교체는 순간 이벤트 | 단계가 둘. (1) 부상 후보 감지(TRANSITION) (2) 실제 회전율 역전(GRACE 5분 함께 표시). 엎치락뒤치락 대비 |
| 13 | 호가 잔량 비율이 매수 강도 메인 시그널 | 허매수/스푸핑 취약 + 매도자 시장가 던지면 비율 함정. → **체결강도(VP, R10)가 메인**, 호가는 가중치 +0.5 보조로 강등. 흥아해운 케이스(모멘텀 죽었는데 호가 5.3배만으로 초록불) 회피 |
| 14 | 개별 시그널마다 색상 부여 (각 줄 🟢/🟡/🔴) | 모멘텀 죽고 호가만 살았을 때 가짜 매수 신호. → **점수 합산 등급 시스템(R14)** 으로 통일. score ≥ 5 STRONG / ≥ 2 WATCH / ≥ -1 NEUTRAL / 그 외 AVOID. 점수 가중치/임계는 검증 데이터 누적 전엔 추정치 — 회귀 케이스 미통과 시 단순 룰로 폴백 |
| 15 | 가속배율 분모는 "직전 30분 평균" 단일 윈도우 | 단타 의사결정엔 1분 단위가 더 빠름. R11에 `vol_accel_1m`(분모 5분), `vol_accel_5m`(분모 20분) 신설. **R3' 주도주 교체용 30분 분모 가속배율은 유지** (다른 용도) — R11은 매수 점수/매도 트리거 전용 |
| 16 | 손절선 도달 시 자동 주문 (단타 시스템 한정 완화) | 본 프로젝트 정책 `자동 매매 절대 금지` 유지. R15 모든 트리거는 텔레그램 알림만, 실주문은 Zeta 직접. 자동 매매는 영구 미지원 (CLAUDE.md, plan.md v0 제외 항목) |
| 17 | TRANSITION/GRACE/강한 부상/자금 이탈/AVOID/R15 매도 트리거를 모두 별도 푸시 메시지로 발송 | 카드를 1~2초 갱신하면서 같은 정보를 푸시로 또 보내면 중복. 사용자는 09:00~10:30 텔레그램 채팅 띄워놓고 카드 색상/이모지/사유 변화로 직접 인지 → **카드 외 별도 푸시 폐기, `editMessageText` in-place 갱신만**. 푸시는 M6 외부 이벤트(상한가 진입/자동 주도주 첫 추가/정기 레포트)만 유지. 메시지 N분마다 삭제/재발송 패턴도 도입 X (밀려 안 보일 일이 없으니 불필요) |
| 18 | 봇 명령 polling 을 평일 09:00~10:30 cron 안에서만 띄움 (`_dashboard_start` 09:00 시작, `_dashboard_stop` 10:30 종료) — 운영시간 외엔 `/list`/`/start`/`/on` 등 어떤 명령에도 응답 X. `/pause` 가 ON/OFF 토글 (`/start` 와 동의어). | 사용자 의도: "단타 칠 수 있을 때 임의 시점에 켜고 끄기". 24h 사용자 토글로 정책 변경. 변경 사항: ①polling thread 를 `scheduler.run()` 시점에 1회 띄워 24h 상시 가동. ②`/on`/`/off` 정식 명령 도입 (멱등). ③`/start`=`/on`, `/pause`=`/off` alias. ④10:30 자동 OFF cron 폐지 — `/off` 로만 종료. ⑤평일 09:00 자동 ON cron 은 편의를 위해 유지. ⑥`/buy`/`6자리 토글` 도 24h 허용 (`/on` 24h 정책과 일관성, NXT/임의 시점 매수 알림 용도). ⑦`MonitoringSession.set_on/set_off` 추가, `toggle_pause` 제거. ⑧카드 정리는 `/off` 발화 후 다음 tick 에서 1회 (`off_cleanup_pending` 플래그). |
| 19 | round 17 정책("카드 외 푸시 폐기")이 docs 에만 있고 코드엔 안 반영. `worker._send_alert` 가 살아있어서 `[부상 후보 감지]` `[1분봉 부상]` `[1분봉 급감]` `[자금 이탈 경보]` `[강한 부상]` `[호가 역전]` `[주도주 교체 완료]` `[부상 후보 — 거래대금 급증]` 등을 별도 메시지로 발송. 발송 후 카드가 위로 밀려나는 걸 보정하려고 `reposition_pending` flag 로 매 tick delete+silent send 재배치. 부상 후보(RISING)는 첫 알림 + 2분 TTL 로 자동 만료. | 사용자 인지: ①카드 재배치가 보고 있던 메시지를 갑자기 사라지게 해서 UX 망침. ②2분 TTL 만료는 사용자가 보던 후보가 시간 만료로 사라지게 만들어 부자연스러움 — 다른 후보 등장 시 자연 교체로 충분. ③alert 별도 푸시는 round 17 정책 반영하면 어차피 폐기 대상. → 변경: ① `_send_alert` 함수 + 호출 5곳 (RISING 신규/강한 부상/자금 이탈/1분봉 부상·급감/호가 역전 + step_tracker TRANSITION·REPLACEMENT) 전부 제거. ②`session.reposition_pending`, `_send_or_edit_monitor` 의 `reposition` 인자, `disable_notification=reposition` 분기 제거 — alert 가 없으니 카드가 밀려날 일도 없음. ③`MonitoredStock.expires_at` 필드 + `prune_expired` 메서드 + `update_rising_candidates` 의 TTL 인자 제거. RISING 동기화 정책 변경: candidates 풀에 없는 RISING 종목은 즉시 카드 제거, 풀 회전율 상위 max_count 까지 신규 등록. `LEADER_EXCLUDE_DAILY_RETURN_PCT=29.0` 필터로 +29% 도달 종목은 풀에서 자동 빠짐 → 카드도 자동 제거. ④`step_tracker` 반환형을 `Alert | None` → `None` 로 변경. TRANSITION/GRACE 상태는 `render_monitor_message(transition_info=...)` 로 a1 카드 헤더에 "🔥 부상 후보 a2: NAME (회전율 X.X%)" / "🔄 GRACE — a2: ..." 한 줄 통합 표시. ⑤render 5분봉/1분봉 가속 라인에 `is_strong_rise` / `is_exit_signal` / `is_one_min_rise` / `is_one_min_exit` 임계 도달 시 🟢⚡ / 🔴⚠ 마크 + 라벨("강한 부상" / "자금 이탈" / "1분봉 부상" / "1분봉 급감") 강조. ⑥`Alert` dataclass, `last_alert_accel`, `last_asking_color` 세션 필드, `is_*` 디바운싱 분기 모두 제거 (predicate 함수 자체는 momentum.py 에 유지 — render 에서 사용). |
| 20 | `/buy CODE PRICE [MIN]` 명령에서 PRICE 가 필수 — 사용자가 매 매수 시 가격을 직접 입력해야 보유 모드 진입 가능. 봇이 KIS 시세를 1~2초 단위로 이미 받고 있는데도 가격을 또 받음. | 사용자 의도: "이미 모니터링 중인 종목이면 봇이 현재가를 아는데 왜 가격을 또 치냐". 손절/익절선이 -1.5%/+2%/+3.5% 단위라 KIS 시세와 실제 체결가 1~2 틱(수십 원) 차이는 무시 가능. → 변경: ①`parse_command` 에서 PRICE 를 선택 인자로 강등 — `/buy 091340` 만 입력해도 valid. ②`MonitoringSession.last_prices: dict[str, float]` 신설. worker tick 이 매 사이클 `snapshot[code].price` 로 채움. ③`_apply_buy(price=None)` 일 때 `session.last_prices.get(code)` 에서 자동 보충. last_prices 에도 없으면 (모니터링 안 하던 종목 + 아직 첫 tick 전) "시세 미확보 — `/buy CODE PRICE` 로 명시" 안내 메시지 반환. ④사용자가 슬리피지 큰 다른 HTS 에서 체결한 경우 등 명시 입력을 원하면 PRICE 인자 그대로 사용 (역호환). ⑤사용자 manual 영속화 없는 readonly 시세 공유라 lock 없이 GIL 만 의존. |
| 21 | `identify_rising_candidates` 가 snapshot 기반 회전율 상위 5개만 surface — 흥아해운처럼 거래대금/회전율은 크지만 모멘텀 죽은 종목도 카드로 잡힘. R14 매수 점수 시스템(grader.py)이 이미 존재하지만 부상 후보 입구에 연결 안 됨. worker docstring 은 "5초 tick" 이라고 stale (실제는 3초). | 사용자 정정: ①"부상 후보 = 매수 점수 높은 후보" 로 재정의. ②비용 분산을 위해 다단계 funnel — 한 번에 전체 R14 풀스코어 돌리지 말고, 값싼 필터부터 점차 좁힘. ③한국 단타 통설(회전율 1위 / 양봉 / 모멘텀 살아있음 / VP 100 이상) 그대로 임계 채택. → 변경: ①`config_thresholds.py` 에 4단계 임계 추가 (`RISING_STAGE1_TURNOVER_TOP_N=15` / `RISING_STAGE2_VOL_ACCEL_MIN=0.8` / `RISING_STAGE3_VP_MIN=100.0` / `RISING_MIN_SCORE=2.0` = WATCH 이상). ②`identify_rising_candidates` 시그니처 유지하되 default `top_n` 을 5 → 15 로 확장 (Stage 1 책임만). ③`worker._evaluate_rising_funnel(stage1, client, snap_by_code, tick_cache)` 신설 — Stage 2 (minute_bars + vol_accel + is_weak_candle) → Stage 3 (ccnl + VP) → Stage 4 (asking + investor + `calculate_buy_score`) 순차 호출, 각 단계에서 미달 종목 drop 후 다음 단계 fetch 비용 X. tick_cache 로 통과 종목의 fetch 결과 보관 → 카드 렌더에서 재사용 (중복 fetch 회피). ④`MonitoredStock` 에 `buy_score / buy_grade / buy_reasons` 필드 추가. ⑤`update_rising_candidates` 가 candidates dict 의 `buy_score/buy_grade/buy_reasons` 를 monitored 에 저장. ⑥`render_monitor_message` 헤더에 `🟢 STRONG +5.5점` 형식 등급 + 점수 표시 + 사유 한 줄 (상위 3개 reasons). ⑦worker.py docstring 5초 → 3초 정정. 흥아해운 회귀: Stage 2 (vol_accel 0.8 임계 미달 + 음봉 윗꼬리 50%) 에서 drop, 카드 발송 안 됨 (`test_rising_funnel_filters_heunga_haewoon`). 비용: 3초 tick 에서 평균 ~33 KIS req (한도 60 의 55%). R13 divergence (VP_5MA 시계열 필요) 는 차후 라운드에서 추가 — 현 R14 점수 계산은 divergence 가산 없이도 흥아해운류 거름 충분. |
| 22 | (a) 보유 카드(`/buy` 후)에 R15 매도 시그널이 표시 안 됨 — `exit_triggers.py` 구현돼 있지만 worker → render wiring 미완 (`plan.md:142`). (b) 카드 시각/가격/매수가/손익이 분리된 라인 → redundant. (c) R15 C1 트리거의 "VP_5MA 100 하향" 이 무슨 뜻인지 초보자에게 불친절. (d) 외국인/기관/프로그램 수치는 KIS 응답 신뢰도 낮음 (데이터 검증 안 됨). (e) docs 가 깊이 위주라 단타 처음 보는 사람에겐 진입 장벽. | 사용자 정정: ①R15 wiring 완성. ②카드 한 줄 합치기 (`시각 (+경과초) 현재가(오늘%)/매수가(손익%)`). ③체결강도 라인에 5MA + 1MA 둘 다 표시 (1MA 는 정보용, 트리거는 5MA 유지). ④봉 패턴 청산(C4)은 1분봉 기준 명시. ⑤외국인/기관/프로그램 라인 제거. ⑥`docs/monitoring-guide.md` 신규 작성 — 봉/회전율/VP/다이버전스/VI 용어 풀어 설명, 4단계 funnel 그림, 매수 점수 가중치, 청산 시그널 C1~C5, 봇 명령 사용법 포함. → 변경: ①`volume_power.VPSeries.ma_1(now)` 헬퍼 추가. ②`MonitoringSession.vp_series: dict[code, VPSeries]` 신설 — worker tick 매 사이클 VP push, 카드/트리거에서 5MA/1MA 조회. ③`worker.dashboard_tick` 에 `load_holdings()` + `evaluate_triggers()` 호출 추가. holding 객체 + trigger_states + divergence 를 render 에 전달. ④`render_monitor_message` 에 `holding / trigger_states / vp_5ma / vp_1ma / divergence` 인자 추가. 보유 모드 헤더 `[보유]` prefix (source emoji 중복 제거), 시각/가격/매수가/손익 합친 라인, 청산 시그널 섹션(C1~C5 각각 ❌/✅ + 현재 수치 노출). +29% 매도가 라인은 감시 모드만 표시. ⑤외국인/기관/프로그램 라인 제거. ⑥`docs/monitoring-guide.md` 신규 ~300줄. CLAUDE.md "파일 작성 시 참고 문서" 에 monitoring-guide.md 추가. |
| 23 | R14 매수 점수가 한국 단타 통설(회전율/체결강도/봉패턴/가속/호가)을 다 다루는 것으로 가정. 검색 기반 통설 재검토 결과 **VWAP 위/아래** 시그널이 누락됨 — VWAP 은 장중 세력 평단가의 근사값으로 단타 핵심 지표인데 grader 입력에 없음. R15 청산 트리거 A3 가 5분 이평을 보지만, R14 매수 진입에는 대칭되는 가격-기준선 시그널이 없어 비대칭. | round 23 (P0-1): ①`momentum.compute_vwap(minute_bars)` 추가 — Σ(typical × volume) / Σ(volume), typical = (H+L+C)/3. ②`momentum.price_vs_vwap_pct(price, vwap)` 헬퍼. ③`config_thresholds.VWAP_ABOVE_THRESHOLD_PCT=+0.3` / `VWAP_BELOW_THRESHOLD_PCT=-0.3` (호가 노이즈 컷오프). ④`GraderSnapshot.price_vs_vwap_pct: float = NaN` 필드 추가, 호출자가 미리 계산. ⑤`calculate_buy_score` 에 R14a 분기: ≥+0.3% → +1 / ≤-0.3% → -1 / 사이는 무가산. ⑥`test_grader.py` 7 케이스 (위/아래/경계/뉴트럴/NaN/제룡전기 보강/흥아해운 보강). ⑦`test_momentum.py` 9 케이스 (VWAP 계산 단위/볼륨가중/empty/zero/missing column + price_vs_vwap_pct 경계). 가중치 ±1 은 회전율(+1) 과 동격 — 통설 검증 누적 전엔 추정치. **호출자 wiring (worker → grader) 은 P0-1 범위 외 — 메타 작업에서 일괄 처리**. |
| 24 | R14 에 5분/20분 이평 위치 시그널도 누락. 한국 단타 통설(namu.wiki, 알파스퀘어 등)에서 5일/20일 이평은 가장 기본 지표이고, R15 A3 가 이미 5분 이평 이탈을 청산 트리거로 쓰는데 R14 진입에는 대칭이 없음. 정배열(가격>MA5>MA20) = 매수 우위, 역배열 = 매도 우위라는 통설 미적용. | round 24 (P0-2): ①`momentum.compute_minute_ma(bars, window)` 추가 — 1분봉 N개 close SMA. R15 A3 minute_ma_5 와 동일 정의. ②`momentum.price_vs_ma_pct(price, ma)` 헬퍼 (VWAP 헬퍼와 시그니처/가드 동일). ③`config_thresholds.MA5_THRESHOLD_PCT=+0.3` / `MA20_THRESHOLD_PCT=+0.3` / `MA_SHORT_MINUTES=5` / `MA_LONG_MINUTES=20`. ④`GraderSnapshot.price_vs_ma5_pct` / `.price_vs_ma20_pct` 필드. ⑤`calculate_buy_score` 에 R14b 분기: 둘 다 ≥+0.3% → +1 (정배열) / 둘 다 ≤-0.3% → -1 (역배열) / 혼합/NaN → 무가산. ⑥`test_grader.py` 8 케이스 (정/역/혼합/경계/뉴트럴/NaN/제룡전기·흥아해운 보강). ⑦`test_momentum.py` 9 케이스 (SMA 단순/마지막 윈도우만/부족/empty/missing col/MA20 + price_vs_ma_pct 가드). **wiring (worker.tick 에서 minute_bars 받아 MA 계산 → snap 채우기) 은 메타 작업으로 일괄**. |
| 25 | R14 가 종목별 시그널만 보고 "도달 시각" 컨텍스트를 안 봄. 통설(namu.wiki 상따): 강한 상한가는 9~10시 진입, 9:30 이내가 가장 강함. 같은 +30% 상한가 종목이라도 09:15 도달과 14:30 도달은 first-mover 강도가 다른데 R14 점수는 동일하게 나옴. | round 25 (P1-1): ①`config_thresholds.LIMIT_UP_EARLY_HH/MM=09:30` / `LIMIT_UP_MID_HH/MM=10:30` 추가. ②`GraderSnapshot.limit_up_hit_time: dt.time | None = None` 필드. 호출자가 상한가 감지 이벤트에서 도달 시각 저장. ③`calculate_buy_score` 에 R14c 분기: 09:30 미만 +1 / 09:30~10:30 미만 +0.5 / 10:30 이상 무가산 / None 무가산. 경계 strict < (09:30 정확히는 mid). ④`test_grader.py` 7 케이스 (조기/중간/late/None/경계/제룡전기 보강). 가중치 +1/+0.5 — 회전율(+1)과 동격 max — 통설 검증 누적 전 추정치. **호출자 wiring (상한가 감지 → snap 채우기) 은 메타 작업에서 일괄**. |
| 26 | R15 가 시간 컨텍스트를 안 봄. 통설("2시 45분경 이평선 밑 음봉이면 목숨 걸고 팔아라" - namu.wiki 단타매매기법): 장 마감 임박 + 약세 시그널은 단일 트리거가 아니라 시간 게이트된 AND 조건이라 더 강한 청산 신호. A3(이평 이탈) / C4(음봉) 가 각각 발화될 때보다 EOD 컷오프 후 동시 발생이 결정적. | round 26 (P1-2): ①`config_thresholds.EOD_CUTOFF_HH=14 / EOD_CUTOFF_MM=45` 추가. ②`TriggerKind` 에 `"A5_eod_ma_break"` 추가, `TRIGGER_LABELS` 에 "A5 EOD 이평+음봉 강제" 라벨. ③`evaluate_triggers` 에 A3 다음 위치로 A5 분기 — `now ≥ 14:45 AND price < ma5 AND candle.type == "bearish"` AND. `is_stop_loss=True` (A 카테고리). ④`test_exit_triggers.py` 6 케이스 (14:45 발화/14:44 미발화/양봉 미발화/MA 위 미발화/MA None 스킵/카드 포맷). ⑤R15 표에 A5 행 추가. **종배는 보유 만료가 다음날 09:00 KRX 시초라 본질적으로 EOD 컷오프 발화 빈도는 낮음 — 단, "장중 매수 → 당일 청산" 케이스(스윙 분리 안 됨, M6 보유 모드) 에선 유효**. |
| 27 | R13 다이버전스 가중치 ±2 가 통설 검증과 어긋남. 한국 단타 통설 검색(namu.wiki 단타매매기법, i-whale, steemit VP) 어디에도 다이버전스가 단타 핵심 지표로 안 나옴 — 차트분석/스윙 영역. 회전율(+1)/봉패턴(+2)/VP(+2)/가속(+2) 같은 통설 지표보다 가중치가 동등하거나 더 큰 건 비통설 위험. CLAUDE.md "검증 안 된 자작 가중합 X" 원칙과 충돌. | round 27 (P2-1): ①`grader.calculate_buy_score` 의 R13 분기 `score -= 2` / `score += 2` → `-1` / `+1`. ②`test_grader.py` 에 다이버전스 단일 ±1 검증 2 케이스 추가 (`test_bearish_divergence_subtracts_one_not_two` / `test_bullish_divergence_adds_one_not_two`). ③기존 회귀 케이스(`test_regression_heungahaeun_with_bearish_divergence` score ≤ -5.0) 는 다이버전스 외 합산으로 -5 미만 유지 가능해 영향 없음 — 119 pass 확인. ④R15 C2 (Bearish Divergence 청산 트리거) 는 OR 조건이라 가중치 개념 없음, 그대로 유지. ⑤docs/jongbae-strategy.md R14 본문 점수 식 갱신. **회귀 안전성**: 다이버전스 단일로 STRONG 들어가던 케이스는 없었음(통상 다른 시그널 동반) → 등급 분포 영향 거의 0. |
| 28 | R14 가 "거래량 폭증 = 좋은 매수 신호" 라는 단순한 가정을 따르고 있었음. 통설(namu.wiki 상따): "전일 거래량 대비 300% 이내 정상 매집, 10배 이상 동반 시 강한 상한가 아니므로 주의" — 폭증은 매도 출회나 단발 신호 가능성이라 오히려 약신호. 거래량 자체는 R3(회전율 top10)/R11(가속) 만으로 봤지 "전일 대비 비율"이라는 통설 컷이 없었음. | round 28 (P2-2): ①`config_thresholds.VOLUME_RATIO_NORMAL_MIN=1.0 / VOLUME_RATIO_NORMAL_MAX=3.0 / VOLUME_RATIO_EXCESSIVE=10.0`. ②`GraderSnapshot.volume_ratio_vs_prev_day: float = NaN` 필드 추가. 호출자가 일봉 데이터에서 (오늘누적 / 전일) 계산. ③`calculate_buy_score` 에 R14d 분기: 1.0~3.0 → +0.5 (정상) / ≥10.0 → -1 (과열) / 그 외 0 / NaN 무가산. ④`test_grader.py` 8 케이스 (정상/과열/경계 3개/중간/낮음/NaN). 127 pass. ⑤docs 본문 점수식 갱신. **wiring**: 호출자(worker) 가 KIS 일봉 API 의 전일 거래량 + 현재 누적 거래량으로 계산 → snap 채우기. 메타 작업에서 일괄. |
| 29 | "외국인+기관 동반 순매수 = 강한 매수" 통설을 R14 에 도입할지 조사 필요. `fetch_investor_flow` (KIS `inquire-investor`) 는 이미 구현됐고 추가 호출 비용 0 인데, round 22 정정 이력에서 "KIS 응답 신뢰도 낮음 — 데이터 검증 안 됨" 으로 모니터링 카드 라인이 제거됨. R14 가산으로 부활시킬지 결정 필요. | round 29 (P3-1 조사): ①가용성 확인 — fetch_investor_flow 응답 필드(외인/기관/프로그램 순매수 수량+금액) 모두 완비. 호출 비용 0. ②검증 부재 확인 — round 22 정정대로 KIS 추정치와 익일 KRX 공시 간 괴리 가능성. ③**현 단계 R14 가산 도입 X** — CLAUDE.md "검증 안 된 자작 가중합 X" 원칙. ④사전 검증 ritual 명시: 3~5종목 × 5거래일 1분 단위 fetch 로그 vs KRX 공시 비교 → ±10% 이내면 R14e 채택 (외인+기관 동시 양수 +0.5), 초과면 v1 연기. ⑤docs/data-infra.md 에 조사 결과 + 검증 ritual 정리 (`KIS API 호출수 영향` 섹션 다음). ⑥카드 표시는 round 22 정정 유지 — 가벼운 "참고 라인" 옵션은 추후 사용자 동의 시 별도 라운드. **결론: 코드 변경 0, 문서만 갱신**. |
| 30 | R7 청산이 "09:00 시초 매도" 단일 룰. 통설(WikiDocs 종가베팅, brokdam): 시초가 갭 크기에 따라 분기 — ≤+1% (또는 마이너스) 는 갭 미발생이라 무조건 전량 / ≥+6% 강한 갭은 30~50% 분할 후 관망. 종배의 가장 중요한 청산 의사결정인데 단일 룰로 묶여있어 갭 폭증 시 추가 슈팅 기회 누락, 갭 실패 시 보유 지연 위험. R15 (장중 모니터링) 와 다른 시각/컨텍스트라 분리 필요. | round 30 (P3-2): ①신규 모듈 `src/jongbae/jongbae_exit.py` — `JongbaeExitDecision(action, partial_ratio, open_gap_pct, reason)` dataclass + `evaluate_jongbae_open_exit(open_price, prev_close)` pure 함수. ②`config_thresholds`: `JONGBAE_OPEN_FULL_SELL_MAX_PCT=1.0` / `JONGBAE_OPEN_PARTIAL_SELL_MIN_PCT=6.0` / `JONGBAE_OPEN_PARTIAL_RATIO=0.4`. ③로직: gap ≤ +1% → sell_all(갭 미발생) / +1% < gap < +6% → sell_all(정상 갭, 익절) / gap ≥ +6% → sell_partial(0.4). ④신규 테스트 `tests/test_jongbae_exit.py` 13 케이스 (갭 마이너스/0/경계 +1%/정상 갭/+6% 경계/큰 갭/0 가드/음수 가드/frozen). 140 pass. ⑤R7' 섹션 신규 추가 (R7 다음 위치). **자동 주문 X** — CLAUDE.md 정책 유지, 09:00 텔레그램 알림에 권고 표시만. R15 와 모듈 분리 (다른 시각/컨텍스트). **wiring**: scheduler 가 09:01~09:05 에 일봉 fetch + jongbae_exit 호출 → 텔레그램 발송. 메타 작업. |
| 31 | round 23~30 신규 시그널 + R7' 모듈은 만들었으나 호출자 wiring 미완: ①worker funnel 에서 VWAP/MA 만 채우고 거래량비율/상한가시각은 placeholder. ②jongbae_exit 09:00 텔레그램 권고 미연결. ③ritual 2/3 자동화(paper_trade 기록기 + 통설 가드레일) 모듈 없음. | round 31~32: ①`worker._prev_day_volume` 헬퍼 + funnel 인자 `daily_ohlcv` → `volume_ratio_vs_prev_day` 채움. ②`MonitoringSession.limit_up_hit_times: dict[str, dt.time]` 신설, scheduler 의 상한가 감지 2 지점(`_collect_snapshot` / `_poll_limit_up`)에서 시각 저장, funnel 에서 `session.limit_up_hit_times` 전달 → R14c 가산. ③scheduler 에 `_send_jongbae_open_exit_recommendation` 09:01 cron + `Dispatcher.send_jongbae_open_exit`. boot 인자 client/settings/dispatcher. holdings.json 비면 no-op. **자동 주문 X, 권고만**. ④신규 모듈 `src/jongbae/paper_trade.py` — `PaperTradeRecord` dataclass + `record_decision` (14:50 호출) + `record_open_result` (다음날 09:30/16:00 호출) + `load_records` (필터 가능) + `compute_summary` (Spearman ρ scipy 의존 X, 자체 구현). `data/paper_trade/YYYY-MM-DD.json` atomic write. test 15 케이스. ⑤`test_grader.py` invariant 3 케이스 — 통설 양수 합 ≥ 비통설×2, 통설 음수 합 ≤ 비통설×2 (절댓값), 다이버전스 단일 ±1 cap. 219 pass. **남은 wiring**: 14:50 결정 레포트 → `record_decision` / 09:30 모닝 → `record_open_result` (다음 라운드). |
| 32 | 사용자 운영 보고 (2026-05-15): ①주도주 모니터링 카드가 전혀 안 뜸. ②30분 동안 부상 후보 카드 0건. ③보유 카드 본문에서 체결강도 라인이 통째로 사라짐 (트리거 줄에 "현재 —" 만). ④`자금 고갈` 트리거가 ❌ 인데 "현재 0.0배" 표시 → 사용자 혼동. ⑤일관성 부재 — 보유/주도주 카드에는 STRONG/WATCH 등급이 안 보이고 RISING 카드만 표시. 진단: ②는 funnel Stage 3 (`vp < 100`) 가 KIS `cttr` 빈 응답(NaN)까지 hard-fail 로 처리해 모든 후보가 drop. ③은 `render._render_monitor_message` 의 `if ccnl:` 가드가 데이터 부재 시 라인 전체를 숨김. ④는 보유 모드 C3 가 `VOL_ACCEL_DRAIN_PERSIST_SECONDS=120` 2분 sustain 후 발화인데 라벨이 단순 `(1분 가속 < 0.5)` 라 즉시 발화처럼 읽힘. ⑤는 R14 점수가 `_evaluate_rising_funnel` 안에서만 매겨져 RISING 외 monitored 종목엔 `buy_grade=None`. | round 33: ①`render.py` 체결강도 라인 `if ccnl:` 가드 폐지 — 데이터 부재 시 `⚪ 체결강도: — (데이터 없음)` placeholder + 5MA/1MA 가 있으면 항상 표시. ②`worker._evaluate_rising_funnel` Stage 3 — `vp is None / NaN` 은 통과 (Stage 4 R14 풀스코어로 가산점 0 처리), 명시적으로 100 미만일 때만 drop. 흥아해운 회귀 안전 (음수 합산으로 점수 부족 → Stage 4 drop). ③`worker.dashboard_tick` 종목 루프 안에 `GraderSnapshot` + `calculate_buy_score` 매 tick 호출 — AUTO/MANUAL/HOLD/RISING 모든 모드에 buy_score/grade/reasons 채움 → 카드 헤더 등급 라벨 일관성. 입력은 이미 fetch 한 값 재사용 → KIS 추가 호출 0. ④`render.py` C3 라벨 보유 모드만 `(1분 가속 < 0.5, 2분 지속)`, 감시 모드는 기존 `(1분 가속 < 0.5)` 유지 — instantaneous vs sustain 발화 룰과 라벨 일치. ⑤funnel/leader 단계별 통과 종목 수 INFO 로깅 — "왜 카드 안 나오는지" 사용자 진단 가능. tests: render 4 신규 (체결강도 ccnl=None / cttr NaN+MA 있음 / 감시 모드 C3 sustain 표기 없음 / 등급 라벨 AUTO 에서 표시), worker 2 신규 (VP NaN funnel 통과 / VP 명시적 85 drop). 기존 회귀 안전 (`test_rising_funnel_filters_heunga_haewoon` 통과). 764 pass. 카드 외 별도 푸시 X (round 17/19 정책 유지). |

---

## 알려진 한계

1. **분봉 히스토리 부재** — 키움 API도 1년 한정, 그 이전 정밀 분석 불가
2. **장중 백테스트 어려움** — 시점별 거래대금 순위 historical 데이터 없음 → 매일 적재
3. **표본 부족 위험** — 일부 종목은 historical 사례가 5개 미만일 수 있음
4. **테마 매핑 변동성** — 네이버 테마는 비공식 분류, 자주 바뀜 (월 1회 재크롤링)
5. **regime change 위험** — 강세장 가정 무너지면 룰 무효화. 자동 감지 한계

---

## 향후 확장 (v1+)

- NXT 청산 로직
- "현차 같은 종목" 정량 제외 룰
- 테마 자동 식별 (NLP, 임베딩 기반)
- 시그널 빈도/품질의 regime별 차이 분석
- 자동 백테스트 (데이터 6개월~1년 누적 후)
