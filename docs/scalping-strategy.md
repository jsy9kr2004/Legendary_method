# 단타 전략 정의 (scalping-strategy.md)

주도주 매매 (단타, Scalping) 의 정량적 룰 정의. **09:00~11:00 활동 + M6 모니터링 카드 기반**. 사용자가 카드를 보고 직접 매수/매도 (자동 매매 X).

종배 (14:50 결정 레포트 + 다음날 시초 청산) 룰은 `docs/eod-strategy.md` 참조. 두 시스템은 명확히 분리.

## 한 줄 정의

09:00~10:30 사이 주도섹터 안의 회전율 1위 종목(=단타 주도주) + 사용자 추가 종목을 M6 카드로 1~2초 갱신 모니터링. `Buy.Score` 가 🟢 STRONG 발화하면 매수, R15 청산 시그널(`Exit.A/P/E`) 발화 또는 매수가 대비 -2% 도달 시 매도.

---

## 명명 체계 (2026-05-21 마이그레이션)

옛 R 번호 → 의미있는 이름. 약식은 알파벳 첫 글자.

| 옛 | 새 (긴 형 / 약식) | 의미 |
|---|---|---|
| R3' 단타 주도주 | **Theme.Leader** / **LDR** | 주도섹터 내 회전율 1위 종목 |
| R9 실시간 모니터링 | **Monitor** / **M** | M6 카드 시스템 |
| R10 체결강도 VP | **Buy.VP** / **VP** | Volume Power |
| R11 거래대금 가속 | **Buy.Accel** / **ACC** | Volume acceleration |
| R12 봉 패턴 | **Buy.Candle** / **CDL** | Candle shape |
| R12.5 위치/맥락 | **Buy.Position** / **POS** | Intraday position |
| R13 다이버전스 | **Buy.Div** / **DIV** | Price-VP divergence |
| R14 매수 점수 | **Buy.Score** / **B** | 종합 매수 점수 + 등급 |
| R15 매도 트리거 | **Exit.Triggers** / **E** | 매도 트리거 + 상태 머신 |

**Exit 하위 알파벳** (각 글자가 단어 의미):
- **A**1~A5 = **A**uto-stop (자동 손절 — 가격/봉/이평/시간/EOD)
- **P**1~P3 = **P**rofit-take (익절 — 1차/2차/트레일링). 옛 B1~B3 에서 변경
- **E**1~E5 = **E**xit-signal (시그널 청산 — VP/Divergence/자금이탈/봉/VI). 옛 C1~C5 에서 변경

→ 코드/데이터 컬럼명은 `trigger_a1_stop_price`, `trigger_p1_take_profit_1`, `trigger_e1_vp_below_100` 등.

---

## Theme.Leader. 단타 주도주 식별 (M5.5)

주도주는 두 가지 컨텍스트로 분리해 정의한다. 같은 함수 X.

**(가) 정통 주도주 — post-limit-up, 14:50 결정 레포트용**
- 정의: 주도섹터 내 **first-mover 상한가 도달** 종목
- 의미: 이미 상한가 친 종목 중 가장 빨리 도달한 것
- 구현: `identify_leading_stocks()` (M2 기존 유지)
- **이건 종배 영역** (`docs/eod-strategy.md`)

**(나) 고주파 주도주 — pre-limit-up, M6 모니터링용 (주로 09:00~10:30 활발, 24h 사용자 토글)**
- 정의: 주도섹터 내 **회전율 1위** = 단타 주도주
- 거래대금 절대값으로 1위 잡으면 하이닉스/삼전이 나옴 → 회전율(거래대금/시총)로 자동 배제
- 의미: 상한가 도달 전 진입해야 매수 가능 (상한가 치면 호가 닫힘)
- 한 섹터에 여러 후보, 한 종목이 여러 섹터에 속할 수 있음 (1:1 매핑 X)
- 구현: `identify_early_morning_leaders()` (M5.5)
- **이게 단타 영역 — 본 문서 대상**

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

**임계값 (M5.5 시작값, 튜닝 가능 — `src/scalping/exit/thresholds.py` 또는 `src/scalping/score/thresholds.py`):**

| 항목 | 값 | 근거 |
|---|---|---|
| 가속배율 | 5배 | 실무 화살표 신호 10배의 절반 — 부상 **시작** 포착 |
| 강한 부상 강조 | 10배 | 실무 화살표 신호 표준 |
| 분봉 거래대금 컷 | 20억 | i-whale 표준, 잡주 노이즈 제거 |
| 회전율비(부상 후보) | a2 ≥ a1 × 0.6 | 추격 가능 거리 |
| 실제 교체 | a2 > a1 | 단순 역전 |
| GRACE 유예 | 5분 | 한국 분봉(1~5분) 운영 단위 |
| 후보 탈락 | a2 < a1 × 0.4, 3분 지속 | 오포착 정정 |

---

## Monitor. 실시간 모니터링 (M6)

**시간 (round 18):** 24h 사용자 토글. 평일 09:00 자동 ON, `/off` 로만 종료 (10:30 자동 OFF 폐지). 봇 명령 polling 은 데몬 시작 시 1회 띄워 24h 상시. 휴장일/주말도 `/on` 가능 (KIS 시세 변동 없으므로 카드는 정적 유지).

**대상 종목:**
- (자동) 주도섹터 내 단타 주도주 (Theme.Leader (나)) — 보통 1~2개. 교체되면 자동 갱신
- (수동) 사용자가 텔레그램 채팅에 6자리 코드 입력 → 토글 추가/해제

**갱신 채널/방식:**
- 채널: 텔레그램 (`editMessageText`로 메시지 1개 유지 갱신, 푸시 알림은 신규 종목 진입 시점만)
- 종목 1~2개 → 2초 / 3~5개 → 3초 / 6~10개 → 5초 / 10개 초과 추가 거부
- 별도 TUI/웹 대시보드는 v1 선택사항 (PWA 는 M7 — `docs/dashboard-pwa.md`)

**메시지 표시 항목 (1초 단위 갱신):**

(공통)
- 현재가, 등락률, 상한가 마크
- 거래대금, 회전율(시총 대비)
- 분봉 거래대금 가속 — `vol_accel_1m` / `vol_accel_5m` (Buy.Accel)
- 체결강도 VP + 5MA / 20MA (Buy.VP)
- 최근 5분봉 패턴 — 양봉/음봉/도지, 윗꼬리·아랫꼬리 비율 (Buy.Candle)
- 위치/맥락 — 당일 고점 대비 %, 시초가 대비 %, 전일 종가 대비 % (Buy.Position)
- 외국인/기관/프로그램 순매수 (장 시작 누적)
- 호가잔량 매수/매도 비 — **보조 표시만** (Buy.VP 도입과 함께 메인에서 강등)
- **매수 점수 + 등급** 🟢STRONG / 🟡WATCH / ⚫NEUTRAL / 🔴AVOID (Buy.Score)
- 다이버전스 마크 — Bearish/Bullish 발생 시 (Buy.Div)

(감시 모드, Exit.Triggers)
- 매수 점수 사유 한 줄 (어떤 가산점/감산점이 컸는지)

(보유 모드, Exit.Triggers — `/buy` 로 진입)
- 매수가, 현재 손익 %
- 손절선 (-2%, 진입 봉 저점, 5분 이평) — **알림만, 실주문 X** (Exit.A1 -2% 사용자 룰 통일, 2026-05-21)
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
| Buy.Score STRONG/WATCH/NEUTRAL/AVOID | 카드 헤더 등급 이모지 + 점수 |
| Buy.Score 진입 STRONG (감시 모드) | 카드 헤더 등급 변화로 충분 |
| Exit.A 손절선 도달 (보유 모드) | 카드 헤더 🛑, 손절선 라인 색상 + ✅ 마크 |
| Exit.P 익절선 도달 (보유 모드) | 카드 익절선 라인 ✅ + "도달" 표시 (멱등 1회) |
| Exit.E 시그널 청산 (보유 모드) | "🔔 매도 트리거 상태" 섹션 ❌→✅ 갱신 |

푸시 ON 메시지가 발생하는 경우는 **M6 외부 이벤트**만:
- 🚨 상한가 진입 (모니터링 안 하던 종목 포함, `src/report/event.py`)
- 자동 주도주 신규 추가 (카드가 새로 생기는 첫 발송 — `editMessageText` 대상이 아직 없으므로 send 1회. 이후엔 갱신만)
- 14:50 결정 레포트 (종배) 등 정기 알림

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

---

## Buy.VP. 체결강도 (Volume Power)

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

**임계 (Buy.Score 에서 사용):**

| 조건 | 의미 |
|---|---|
| VP > 110 AND VP_5MA > 100 | 강한 매수 체결 우세 (+2점) |
| VP < 100 | 매수 압력 약함 (-2점) |
| VP_5MA 가 100 하향 돌파 | 보유 모드 매도 트리거 (Exit.E1) — 카드 트리거 상태에 ✅ |

**호가 잔량과의 관계:** 호가 잔량은 보조 표시만 유지. 가중치 +0.5점(메인 가중치 -2 ~ +2 와 비교해 약함).

---

## Buy.Accel. 거래대금 가속 — 다중 윈도우

**배경:** 기존 가속배율 정의는 "현재 5분봉 거래대금 / 직전 30분 평균" 단일 분모. 단타 의사결정엔 1분 단위가 더 빠른 변화 포착에 유리. 두 윈도우를 병행한다.

**정의:**
```
vol_accel_1m = 최근 1분 거래대금 / 직전 5분 평균 분당거래대금
vol_accel_5m = 최근 5분 거래대금 / 직전 20분 평균 5분 거래대금
```

(Theme.Leader 의 "가속배율 5배 / 10배" 임계는 분모 30분 윈도우 기준이며 주도주 교체 상태머신용. Buy.Accel 은 매수 점수/매도 트리거용으로 별도 운영.)

**임계 (Buy.Score):**

| 조건 | 점수 |
|---|---|
| vol_accel_5m > 1.2 AND vol_accel_1m > 1.0 | +1 (자금 유입 가속) |
| vol_accel_1m < 0.5 | -1 (자금 고갈) |
| vol_accel_5m > 2.0 | +1 (강한 가속) |

**임계 (Exit.Triggers):**
- vol_accel_1m < 0.5 가 2분 연속 지속 → 보유 모드 시그널 청산 (Exit.E3)

---

## Buy.Candle. 봉 패턴 분석

**배경:** "1분봉 가속 감소"가 양봉 정체인지 큰 음봉인지 Buy.Accel만으론 구분 불가. 봉 자체 형태를 별도 시그널로.

**기준:** 최근 5분봉(완성봉, 진행 중 봉 제외).

**정의:**
```
candle_type   = "bullish" if close > open else "bearish" if close < open else "doji"
body          = |close - open|
upper_wick    = (high - max(open, close)) / max(high - low, ε)
lower_wick    = (min(open, close) - low) / max(high - low, ε)
```

(ε = 1e-9, 0 division 가드)

**임계 (Buy.Score):**

| 조건 | 점수 |
|---|---|
| candle_type = bullish AND upper_wick < 0.3 | +2 (장대양봉) |
| candle_type = bearish OR upper_wick > 0.4 | -2 (윗꼬리 음봉/긴 윗꼬리) |

**임계 (Exit.Triggers):**
- candle_type = bearish AND upper_wick > 0.5 → 보유 모드 시그널 청산 (Exit.E4)

---

## Buy.Position. 위치/맥락 정보

**필드 (표시용 + 진입 조건):**
```
dist_from_intraday_high = (current - intraday_high) / intraday_high × 100   (음수)
dist_from_open          = (current - open) / open × 100
dist_from_prev_close    = (current - prev_close) / prev_close × 100
vi_triggered_at         = 발동 시각 (datetime | None)
vi_elapsed_sec          = 발동 후 경과초 (None if 발동 없음)
```

**임계 (Buy.Score 진입 필수조건):**
- 매수 점수 계산과 별도로, **진입 필수조건 = `dist_from_intraday_high ≥ -2.0%` (추격매수 방지)**. 미충족 시 등급과 무관하게 진입 비권장.

**VI 데이터:** KIS API에서 직접 endpoint 미확인 — v0 에서는 분봉 가격/거래대금 급변(±10% within 1분) 기반 휴리스틱으로 placeholder, 정밀 추적은 v1 TODO.

---

## Buy.Div. 가격-체결강도 다이버전스

**정의 (5분 윈도우):**
```
price_5m_change = (current - close_5m_ago) / close_5m_ago × 100
vp_5ma_change   = VP_5MA(now) - VP_5MA(5분 전)

bearish_divergence = (price_5m_change > 0) AND (vp_5ma_change < 0)   # 고점 신호
bullish_divergence = (price_5m_change < 0) AND (vp_5ma_change > 0)   # 매집 신호
```

**임계 (Buy.Score):**

| 조건 | 점수 |
|---|---|
| bullish_divergence | +1 (round 27 P2-1: 통설 외 약신호라 +2 → +1 강등) |
| bearish_divergence | -1 (round 27 P2-1: -2 → -1 강등) |

**임계 (Exit.Triggers):**
- bearish_divergence 발생 시 보유 모드 시그널 청산 (Exit.E2, 즉시)

---

## Buy.Score. 매수 점수 + 등급 (메인)

**배경:** 기존 "개별 시그널마다 색상 부여" 방식은 호가 잔량 하나로 초록불 켜지는 가짜 매수 신호 발생(흥아해운 케이스). 조합 점수 기반 등급으로 통일.

**경고:** 임계값/가중치는 **한국 단타 통설 조합**이며 검증 데이터 누적 전엔 추정치. 흥아해운 회귀 + 추가 5~10 케이스 미통과 시 단순 룰(VP < 100 AND vol_accel_1m < 0.5 → AVOID)로 폴백.

**점수 산정:**
```
score = 0

# 거래대금 (1차 필터, 약한 가산)
if volume_turnover_rank ≤ 10:                                 score += 1

# 가격 모멘텀 (Buy.Accel)
if vol_accel_5m > 1.2 and vol_accel_1m > 1.0:                 score += 2
if vol_accel_5m <= 0.8 and vol_accel_1m <= 0.5:               score -= 3   # 강한 페널티
# 감속(WEAK)은 ≤ — "0.8 이하" 한국 단타 통설 표현 부합. 가속(STRONG)은 strict >.

# 봉 패턴 (Buy.Candle)
if candle_type == "bullish" and upper_wick < 0.3:             score += 2
if candle_type == "bearish" or upper_wick > 0.4:              score -= 2

# 체결강도 (Buy.VP)
if vp > 110 and vp_5ma > 100:                                 score += 2
if vp < 100:                                                  score -= 2

# 거래대금 가속 추가
if vol_accel_1m > 2.0:                                        score += 1
if vol_accel_1m < 0.5:                                        score -= 1

# 다이버전스 (Buy.Div) — round 27 (P2-1): 통설 외 약신호라 ±2 → ±1 강등
if bearish_divergence:                                        score -= 1
if bullish_divergence:                                        score += 1

# Buy.Score.d 거래량 비율 검증 (round 28, P2-2) — 통설(상따): 전일 대비 1~3배 정상, 10배↑ 과열
if 1.0 ≤ volume_ratio_vs_prev_day ≤ 3.0:                      score += 0.5
if volume_ratio_vs_prev_day ≥ 10.0:                           score -= 1

# 호가 잔량 (약화 — Buy.VP 도입으로 강등)
if bid_ask_ratio > 3.0:                                       score += 0.5

# Buy.Score.a VWAP 위치 (round 23, P0-1) — 통설 단타 핵심 지표
# VWAP = 거래량 가중 평균 = 장중 세력 평단가의 근사.
# 가격이 VWAP 위면 매수 우위, 아래면 매도 우위.
if price_vs_vwap_pct ≥ +0.3:                                  score += 1
if price_vs_vwap_pct ≤ -0.3:                                  score -= 1

# Buy.Score.b 5/20분 이평 위치 (round 24, P0-2) — 통설 단타 기본
# 5분/20분 SMA = 1분봉 5개/20개 close 평균. 정배열/역배열.
# Exit.A3 (5분 이평 이탈 청산) 와 대칭 — 진입에선 가격>MA5 가산.
if price_vs_ma5 ≥ +0.3 and price_vs_ma20 ≥ +0.3:              score += 1  # 정배열
if price_vs_ma5 ≤ -0.3 and price_vs_ma20 ≤ -0.3:              score -= 1  # 역배열

# Buy.Score.c 상한가 진입 시간 가산 (round 25, P1-1) — 일중 first-mover 강도
# 통설(상따): "9~10시 진입, 9:30 이내가 가장 강한 상한가". 상한가 도달 시각
# 기준. 도달 안 했으면 None (무가산).
if limit_up_hit_time < 09:30:                                 score += 1
elif limit_up_hit_time < 10:30:                               score += 0.5

# R14k 일중 최고점 거리 페널티 (2026-05-21) — 정점 진입 회피
# 사용자 의도 (2026-05-21): "차트의 매수 포인트가 너무 고점에서 찾아옴".
# 5/20 매매일지 §H7 + backtest_user_trades 검증 (5/20 사용자 매매 7건 차단,
# 누적 +5.24% → +12.21%). 통설: namu.wiki 상따 "고점 추격 회피".
if dist_from_intraday_high_pct >= -2.0:                       score -= 2  # 정점 2% 이내
elif dist_from_intraday_high_pct >= -5.0:                     score -= 1  # 정점 5% 이내

# R14l 횡보 정점 페널티 (2026-05-21) — 폭등 후 횡보 micro fluctuation
# 수젠텍 5/20 케이스 — 일중 +18% 도달 후 횡보 → 사용자 매수 시점 모두 dist 0~3%.
# 통설: i-whale "+15% 도달 후 횡보 micro fluctuation 매매 회피".
if daily_return_pct >= 15 and dist_from_intraday_high_pct >= -5.0:
    score -= 1.5
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

---

## Exit.Triggers. 매도 트리거 + 상태 머신 (감시/보유 모드)

**상태:**
```
[감시 모드]  /buy 091340 91300                  [보유 모드]
     ←─────────────────────────────────────────────→
                /sell 091340  또는  매도 트리거 알림 후 사람 수동 청산
```

**보유 모드 진입 시 즉시 계산:**
```
stop_loss        = entry_price × 0.98         (Exit.A1 손절선 -2%, 2026-05-21 사용자 룰 통일)
take_profit_1    = entry_price × 1.020        (Exit.P1 익절 1차, 1/3)
take_profit_2    = entry_price × 1.035        (Exit.P2 익절 2차, 1/3)
trailing_stop    = high_since_entry × 0.985   (Exit.P3 잔여 1/3 트레일링)
time_stop_sec    = N분 (기본 N=10) — 진입 후 +0.5% 미달 시 알림
entry_bar_low    = 진입 직전 1분봉 저점
```

**매도 트리거 (OR, 하나라도 발동 시 카드 표시):**

| 트리거 | 의미 | 조건 | 우선순위 |
|---|---|---|---|
| **Exit.A1** | **A**uto-stop — 가격 | 현재가 ≤ stop_loss (= 진입가 × 0.98) | 최우선 |
| **Exit.A2** | **A**uto-stop — 봉 저점 | 현재가 < entry_bar_low | 최우선 |
| **Exit.A3** | **A**uto-stop — 이평 이탈 | 5분봉 종가 < 5분 이평 | 최우선 |
| **Exit.A4** | **A**uto-stop — 시간 | 진입 후 N분 경과 + 현재 +0.5% 미달 | 최우선 |
| **Exit.A5** | **A**uto-stop — EOD 컷오프 | now ≥ 14:45 AND 가격 < 5분이평 AND 직전 분봉 음봉 | 최우선 |
| **Exit.P1** | **P**rofit-take 1차 | 현재가 ≥ take_profit_1 (1회만 발화) | 정상 |
| **Exit.P2** | **P**rofit-take 2차 | 현재가 ≥ take_profit_2 (1회만 발화) | 정상 |
| **Exit.P3** | **P**rofit-take 트레일링 | 현재가 ≤ trailing_stop AND P1 발화 후 | 정상 |
| **Exit.E1** | **E**xit-signal — 체결강도 이탈 | VP_5MA 가 100 하향 돌파 | 시그널 |
| **Exit.E2** | **E**xit-signal — Bearish Divergence | Buy.Div bearish_divergence True | 시그널 |
| **Exit.E3** | **E**xit-signal — 자금 고갈 | vol_accel_1m < 0.5 (2분 연속) | 시그널 |
| **Exit.E4** | **E**xit-signal — 윗꼬리 음봉 | Buy.Candle candle_type=bearish AND upper_wick>0.5 | 시그널 |
| **Exit.E5** | **E**xit-signal — VI 재상승 실패 | vi_triggered_at 후 5분 내 고가 회복 X | 시그널 |

→ 옛 명명 A/B/C 매핑: A1~A5 그대로 / **B1~B3 → P1~P3** / **C1~C5 → E1~E5**.

**모든 트리거 발화 = 보유 모드 카드의 "🔔 매도 트리거 상태" 섹션에 표시 (별도 푸시 X).** 사용자가 모니터링 카드를 보고 직접 인지 + 청산 여부 결정. 실주문은 Zeta 직접. 본 프로젝트는 자동 매매 영구 미지원 (CLAUDE.md "자동 매매 절대 금지" 정책).

**보유 모드 카드 갱신:** 트리거가 발화해도 카드는 계속 갱신(현재가/손익/남은 손절·익절 거리 표시). `/sell` 들어오기 전엔 자동으로 감시 모드 복귀 X (사람이 청산 여부 확인 후 명시적 `/sell` 필요).

**카드 시각 강조 (사람이 빨리 인지하도록):**
- 손절선 도달 (Exit.A1~A5) — 헤더 🟡 HELD → 🛑 STOPPED, 발화 라인 빨간 ✅
- 익절선 도달 (Exit.P1/P2) — 라인 옆 ✅ + "도달" 텍스트 (1회 멱등)
- 시그널 청산 (Exit.E1~E5) — "🔔 매도 트리거 상태" 섹션의 해당 줄 ❌→✅

**사용자 매매 룰 (baseline, `docs/trading-journal.md` §0.1 참조):**

1. **매수**: Buy.Score 🟢 STRONG 일 때만.
2. **매도**: Exit 트리거 (A1~A5/P1~P3/E1~E5) 중 하나라도 발화 OR 매수가 대비 -2% 도달.

매수가 -2% 도달은 Exit.A1 과 동일 (둘 다 누적 -2%). 사용자는 룰을 일관되게 지키려고 노력. 매매일지에서 "룰 위반" 단정 평가 전에 시간차 윈도우 [-30s, +5s] + 윈도우 max(buy_grade) 확인 (`docs/trading-journal.md` §0.2).

---

## 알림 시점 (단타 영역)

| 시점 | 종류 | 핵심 내용 |
|---|---|---|
| 09:00 (자동) | M6 ON | 자동 주도주 카드 발송 시작. 1~2초 갱신 |
| 09:00~10:30 (활발) | M6 카드 | 사용자 매매 시간대. STRONG 발화 시 사용자가 매수 결정 |
| 상한가 진입 | 이벤트 트리거 ★ | 즉시 푸시 (장중 어느 때나) |
| 사용자 토글 | `/on`/`/off` | 24h 가능. 텔레그램 명령 |

★ 표시는 단타 의사결정의 핵심 푸시.

종배 영역 알림 (09:30 모닝 / 14:50 결정 레포트 / 16:00 사후) 은 `docs/eod-strategy.md` 참조.

---

## 코드 위치 (마이그레이션 후, 2026-05-21)

| 영역 | 모듈 경로 |
|---|---|
| Buy.Score (R14) | `src/scalping/score/grader.py` |
| Buy.VP (R10) | `src/scalping/score/vp.py` |
| Buy.Accel (R11) | `src/scalping/score/accel.py` |
| Buy.Candle (R12) | `src/scalping/score/candle.py` |
| Buy.Div (R13) | `src/scalping/score/divergence.py` |
| Buy.Position (R12.5) | `src/scalping/score/position.py` 또는 grader 내부 |
| Buy.Score 가중치/임계 | `src/scalping/score/thresholds.py` |
| Exit.Triggers (R15) | `src/scalping/exit/triggers.py` |
| Exit 임계 (A1 -2% 등) | `src/scalping/exit/thresholds.py` |
| Theme.Leader (R3' 단타 주도주) | `src/scalping/leader.py` |
| Paper trade | `src/scalping/paper_trade.py` |
| M6 카드 워커 / 렌더 / 상태 | `src/dashboard/` (M7 PWA 도 동일 디렉토리) |
| Theme (R3 주도섹터, 공통) | `src/common/theme.py` |

---

## 검증 가능한 사용자 발화 (단타)

대화록 + 매매일지 (`data/journal/`) 에서 명시된 것. backtest/검증에 사용:

| 날짜 | 종목 | 매매 | 가격/근거 |
|---|---|---|---|
| 2026-05-19 | 메이슨캐피탈 (021880) | 매수 → 익절 | 10:30 263 → 10:37 278 (+5.70%). Buy.Score WATCH +4.5. 매수 윈도우 STRONG. 시그널 무시 익절 사용자 감 |
| 2026-05-19 | 흥아해운 (003280) | 매수 → 손절 | 10:26 2,880 → 10:38 2,835 (-1.56%). Exit.E2 동시 매도 (delta 0.16초). 흥아해운 케이스 정확 적중 |
| 2026-05-20 | 주성엔지니어링 (036930) 3차 | 매수 → 익절 (일중 최고점) | 09:27 198,900 → 09:32 214,000 (+7.59%). Buy.Score STRONG +10.0 매수 / Exit.E2+E3 매도 동시. 시스템·사용자 모두 우위 케이스 |
| 2025-05-04 | 제룡전기 | 매수 | 91,300원 (상한가 도달 순간) — 종배 케이스 (eod-strategy.md 참조) |

추가는 `data/journal/*.md` 누적 매매일지 참조.

---

## 정정 이력 (단타 영역)

| Round | 잘못 알았던 것 | 정정 |
|---|---|---|
| 2026-05-21 R14k/R14l 정점 회피 도입 ★ | 사용자 발화 (2026-05-21): "image/0520 의 차트들을 보면 매수 포인트가 너무 고점에서 찾아오는 느낌". 본질: Buy.Score 의 lagging indicator 9개가 동시 발화하는 시점 = 폭등 막바지 = 정점 매수. cutoff 변경 (q5_inv_6) 으로는 본질 해결 X — 점수 높을수록 정점에 더 가까움. | R14k (일중 최고점 거리 페널티 -2/-1) + R14l (횡보 정점 페널티 -1.5) 신설. backtest_user_trades 검증: 5/20 사용자 매매 15건 중 7건 차단 (B_정점직후 3/3 + C_횡보고점 2/4 + 시초 정점 2/8). 누적 손익 +5.24% → +12.21% (133% 증가). 통설: namu.wiki 상따 + i-whale + Bollinger mean reversion. 회귀: test_grader.py 에 9개 신규 테스트 (R14k 4 + R14l 4 + 사용자 매매 케이스 2). 917 passed. **선행 조건 (해결됨)**: tick_log.intraday_high 컬럼 backtest 시 0 박힘 — 운영 grader 는 bars.high.max() fallback (worker.py:766) 으로 정상 작동 확인. |
| 2026-05-21 명명 마이그레이션 | docs/jongbae-strategy.md 한 파일에 단타 (R9~R15) + 종배 (R1~R8) 룰이 R 번호로 섞임. src/jongbae/ 디렉토리에도 단타 8 파일이 들어있어 디렉토리 이름과 내용 불일치. 사용자 (Zeta) 가 매매일지 작성 시 두 시스템 혼동 발생 보고. | 명명 재설계 + 시스템 분리: ①R 번호 → Buy.*/Exit.*/Theme.* 의미있는 이름. 약식 알파벳 (B/E/T/etc). ②매도 트리거 알파벳 B→P (Profit), C→E (Exit-signal). A는 그대로 Auto-stop. ③docs/scalping-strategy.md (본 문서) 신설 + docs/eod-strategy.md (종배) 분리. ④src/jongbae/ → src/scalping/{score,exit}/ + src/overnight/ + src/common/ 재구성. ⑤tick_log parquet 컬럼명 trigger_b1_* → trigger_p1_*, trigger_c1_* → trigger_e1_* 마이그레이션 (5/18~5/20 변환). ⑥CLAUDE.md "현재 종배만 구현 중" 표현 정정 — 두 시스템 명시 + prefix 표 추가. |
| 2026-05-21 A1 손절 통일 | A1 손절 임계 -1.5% (코드) vs 사용자 룰 -2% 불일치. 사용자는 -2% 시 손절 의도. | A1 임계 -1.5% → -2% 통일. config_thresholds.py STOP_LOSS_PCT -1.5 → -2.0. 테스트 fixture 98_500 → 98_000. |
| 40 | tick 길어졌다는 사용자 인지 — 처음엔 캐시 + 주기 분리(funnel 5초 주기) 로 풀려고 했음. 사용자(Zeta) 정정: "체결강도/거래대금/거래량/봉형태 stale 되면 의미 없음 — 캐시 X, fetch 병렬화로 fresh 유지". | round 40: parallel_fetch.py 신설 + dashboard_tick 흐름 재설계 + 캐시 정책 (tick 안 buffer 만, tick 간 cache X). |
| 27 (P2-1) | 다이버전스 ±2 가산이 통설 외 약신호인데 큰 비중 차지 | ±2 → ±1 강등 (round 27) |
| 23~26 (P0~P1) | R14 점수가 호가 잔량 + VP 만으로 STRONG 발화하는 가짜 신호 가능성 | VWAP/MA/limit_up_time 등 통설 기반 시그널 추가 |
| 17 | R15 매도 트리거 발화 = 자동 매도 푸시 | 카드 내부 통합 — 별도 푸시 X. 사람 수동 청산 |
| 16 | 매도 트리거 발화 직후 보유 → 감시 모드 자동 복귀 | `/sell` 명시적 명령 필요. 자동 복귀 X |

---

## 관련 문서

- `docs/eod-strategy.md` — 종배 (Eod) 전략 룰
- `docs/monitoring-guide.md` — M6 카드 라인별 의미 (초보자용)
- `docs/dashboard-pwa.md` — M7 PWA 대시보드 (단타 시각화)
- `docs/trading-journal.md` — 매매일지 작성 가이드 + 사용자 매매 룰
- `docs/buy-score-revision-proposal.md` — Buy.Score 재설계 제안 (정점 진입 함정 분석)
- `docs/data-infra.md` — 데이터 인프라 (공통)
- `docs/plan.md` — 전체 마일스톤
