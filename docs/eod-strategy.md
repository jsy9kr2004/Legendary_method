# 종배 전략 정의 (eod-strategy.md)

종배 매매 (종가 베팅, **E**nd-**o**f-**d**ay = `Eod`) 의 정량적 룰 정의. **14:50 결정 레포트 + 다음날 시초 청산** 기반. 이 문서는 박민준(레전드 채팅방 분석가)의 발화와 일반 한국 단타 노하우를 기반으로 정리된 것이다.

주도주 매매(단타, 09:00~11:00 활동) 룰은 `docs/scalping-strategy.md` 참조. 두 시스템은 명확히 분리.

## 한 줄 정의

장중 거래대금 30위 내에서 동일 테마가 3개 이상 출현하면 **주도테마**(Theme)로 인식하고, 그 테마 안에서 일봉 +20% 이상 마감하는 종목을 추적해 **상한가 진입 순간 또는 종가**에 매수, **다음날 시초**에 매도하여 갭 차익을 노린다.

---

## 명명 체계 (2026-05-21 마이그레이션)

옛 R 번호 → 의미있는 이름. 약식은 알파벳.

| 옛 | 새 (긴 형 / 약식) | 의미 |
|---|---|---|
| R1 시장 국면 | **Eod.Market** / MKT | 시장 강세 게이트 |
| R2 유니버스 | **Universe** / UNI | 종목 universe (공통) |
| R3 주도테마 | **Theme** / THM | 주도섹터 식별 (공통) |
| R4 종배 후보 | **Eod.Pick** / PICK | 후보 추출 (v2) |
| R5 4-Layer 갭상 | **Eod.GapStats** / GAP | Historical 갭상 통계 |
| R6 사이징 | **Eod.Sizing** / SIZ | Kelly/Sharpe/Equal |
| R7 시초 청산 | **Eod.Exit** / OXE | 다음날 시초가 매도 |
| R8 매매 실행 | **Eod.Exec** / EXE | 사람 직접 (자동 주문 X) |

---

## 핵심 원리

### 종배의 알파 구조

종배가 노리는 알파는 정확히 **close-to-open 갭**이다. 일중 추가 상승 노리는 게임이 아니다.

학술적으로도 미국 시장의 경우 지난 30년간 거의 모든 수익이 close-to-open 구간에서 발생했다는 연구가 있고, 한국 시장도 유사한 경향이 보고된다. 즉 종가 매수 + 시초 매도라는 행위 자체가 약한 양의 기댓값을 갖는다.

다만 **무차별 종가 매수는 노이즈**다. 필터링이 알파다.

### 필터링 3중 구조

```
[Filter 1] 시장 국면 (Eod.Market)  → 대세상승장에서만
[Filter 2] 주도테마 (Theme)         → 자금이 몰리는 곳
[Filter 3] 종목 강도 (Eod.Pick)     → 일봉 +20% 이상 양봉 (또는 v2: 5~27% + 종가 위치)
```

여기에 historical 통계 (`Eod.GapStats`) 로 진입 강도(사이징, `Eod.Sizing`)를 결정한다.

---

## Eod.Market. 시장 국면 필터

**원칙:** 대세상승장에서만 종배. 약세장에서는 룰 무효.

**자동 지표 (레포트 상단 표시):**
- `kospi_above_ma200`: KOSPI 종가 > KOSPI 200일 이동평균
- `kospi_60d_return`: KOSPI 60일 수익률
- `vkospi`: 변동성 지수 현재값
- `bear_candle_ratio_20d`: 직전 20거래일 음봉 비율

**최종 판정:** Zeta가 직관 판단 (자동 게이팅은 안 함). 레포트 상단에 지표만 노출.

---

## Universe. 유니버스 (공통)

**대상:** KOSPI + KOSDAQ 전종목

**제외:**
- ETF, ETN, ELW, ELS, ELB, 우선주, 스팩
- 리츠, 펀드형 종목 (`1XXXXX` 코드 다수)
- 관리종목, 거래정지 종목
- 종목명 패턴 차단: `KODEX`, `TIGER`, `KBSTAR`, `ARIRANG`, `KINDEX`, `HANARO`, `RISE`, `ACE`, `SOL`, `WOORI`, `PLUS`, `KOSEF` 등 ETF 운용사 prefix

**시총/거래대금 컷:** 적용 X. 레포트에 정보만 표시. (v0 단순화)
다만 **회전율 계산을 위해 시총 데이터 적재는 필수** (M5.5).

→ Universe 는 단타 시스템 (`docs/scalping-strategy.md`) 에서도 동일 사용.

---

## Theme. 주도테마(주도섹터) 식별 (공통)

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
2. ETF/ETN/리츠/스팩/펀드 제외 (Universe 강화)

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

→ Theme 식별 결과는:
- 종배 영역: Eod.Pick 의 universe 필터링 + 결정 레포트 헤더 "최종 주도테마" 섹션
- 단타 영역: Theme.Leader (`docs/scalping-strategy.md`) — 주도섹터 내 회전율 1위 = M6 모니터링 자동 대상

---

## Eod.Pick. 종배 후보 추출

### v0 ~ v1 (~round 40, 운영 결과 매일 0종목)

**조건 (모두 만족):**
- (a) Eod.Market, Universe, Theme 통과
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

**한계 (round 41 진단):** 5/12~5/18 운영 결과 5일 연속 후보 0종목. 원인은 Theme (v0 거래대금 50위 카운트) 가 항상 대형주(삼성전자/현대차/LG 등) 테마만 잡고 그 안에는 일봉 +20%↑ 종목이 존재하지 않는 universe 미스매치. Theme v1 (breadth + 회전율 + 평균상승률 z-score) 미구현. Theme 가 v1 으로 정공법 구현될 때까지 결정 레포트는 사실상 마비.

### v2 (round 41, 사후 검증 기반 재설계 — 결정 레포트 채널)

**배경:** Theme v1 정공법 구현 전까지 결정 레포트 마비 회피용. 주도섹터 제약을 제거하고 단순 정량 컷으로 후보 추출. 5/11~5/14 4영업일 backtest 로 갭상 확률/평균 검증 후 채택.

**Hard cut (탈락 조건, 모두 만족):**
- (a) **거래대금 50위** (단일 종목 — `is_tradable_for_jongbae` 통과, ETF/ETN/리츠/스팩/펀드/우선주 제외)
- (b) **일봉 상승** (`ret > 0`) — (e) 와 동일 (round 42 하한 0 초과로 일치)
- (c) **종가 고가 대비 -10% 이내** (`(high - close) / high ≤ 0.10`) — 매물 소화 후 강세 마감
- (e) **`0% < ret ≤ 상한`** — 하한 0 초과 (보합/하락 제외, round 42 2026-05-25: 5→0 — 레전드 픽/1년 backtest 검증, 0~5% 구간 56% 갭상 양의 EV + 통설상 선정축은 수급/시장이지 일봉 % 아님). **상한 = NXT 가능/추정 29.5% / 비-NXT 27%** (round 42: NXT 애프터마켓 ~20:00 매수 가능 → 점상한가도 종배 가능. 기존 27% 단일컷이 NXT 표시보다 먼저 상한가를 제외하던 모순 해소). 비-NXT 27% 는 14:50 KRX 점상한가 매수 불가 회피 (안전마진 3%)

**Soft 보조 지표 (표시만, 탈락 X — 2026-05-19 사용자 정정):**
- (d) **52주 신고가** — 일중 고가가 직전 250 거래일 종가 최대치 갱신. 카드에 ✓/✗/— 로 표시만, 후보 탈락 X.
- (f) **historical Layer 표본 ≥ 5** — 표본 부족 시 Kelly 산출 불가 (None) 만, 후보 탈락 X. Sharpe/Equal/사용자 직관으로 사이즈 결정.

**정정 사유 (2026-05-19):** (d)(f) 를 hard cut 으로 박으면 5일 backtest 표본 한계 + 강세장 가정 무너졌을 때 후보 0~1종목 수준으로 좁아져 운영 의미 없음. 보조 지표로 활용하는 게 의사결정에 더 유용. round 41 본문의 (a)(b)(c)(e) 만 hard, (d)(f) 는 카드 메타로 강등.

**보조 표시 (점수화/컷 X, 카드 메타로만):**
- **종목별 1년 historical**: 그 종목의 ret≥10% 횟수 + 그중 다음날 갭상 횟수 + 비율. 카드에 한 줄 표기 — `📊 1년 ret≥10: N회 / 갭상 K회 (X%)`. 5일 backtest 결과 50% 이상 컷을 룰에 박으면 갭상 확률이 오히려 약간 떨어져서(58.8 → 55.6%) **컷으로는 사용 X**. 다만 단골 종배 종목 (대한광통신 78%, 빛과전자 80%, 한화갤러리아 100% 등) 식별엔 유용.

**진입 우선순위 (v0 의 1~2순위 그대로):**
1. **1순위 (`limit_up`):** 상한가 도달 — 도달 순간 매수 (NXT 가능 시 ret≤29.5, 비-NXT ret≤27)
2. **2순위 (`high_pull`):** 일중 +28%↑ → +20~25% 정리 (상한 cap 안에서)
3. **3순위 (`normal`):** ret > 0 + (a)~(e) 모든 조건 통과

**제외 (애매한 케이스):**
- +28% 찍고 안 빠지고 그대로 마감 (상한가 못 갔는데 자리 잡힘)
- 일중 +30% 찍고 +5%로 떡락 (시세 죽음)

**v2 사후 검증 결과 — 정확성 재확인 (2026-05-19 round 41 후속 3, backtest 재실행):**

round 41 후속 2 에서 KIS API 운영이 거래량 universe 였다는 사실이 드러나
본 표 결과도 함께 의심받았으나, 재실행 결과 (`scripts/backtest_r4v2.py`,
`data/backtest/summary.md`) **round 41 본문 backtest 는 daily_ohlcv 기반이라
처음부터 정확한 거래대금 universe 였음 확인**:
- 최대 갭상 LG전자 5/14 +17.97% — 재실행 결과와 일치 ✓
- 최악 갭하락 엑스게이트 5/13 -4.88% — 재실행 결과와 일치 ✓

표 그대로 유효. 단 종목 수 차이 (본문 17 vs 재실행 30) 는 본문이 그 시점
(d)(f) hard cut 적용 결과이고, 후속에서 (d)(f) soft 로 정정한 현재 운영 룰
기준 정확한 결과는 재실행본 (`data/backtest/r4v2_4d_511_514_top50.csv`).

| 지표 | 본문 (top 50, 4d, (d)(f) hard) | 재실행 (top 50, 4d, hard=a,b,c,e만) |
|---|---|---|
| 총 후보 | 17 | 30 |
| 갭상 확률 | 58.8% | 70.0% |
| 평균 갭상률 | +2.23% | +2.04% |
| 중앙 갭상률 | +0.43% | +1.05% |
| 평균 일중 고점 매도 | +4.97% | (미측정 — 분봉 부재) |
| 최대 갭상 | LG전자 5/14 **+17.97%** | 동일 ✓ |
| 최악 갭하락 | 엑스게이트 5/13 **-4.88%** | 동일 ✓ |
| 다음날 종가까지 보유 시 평균 | -2.63% | -3.41% |

**30 vs 50 universe 비교 (재실행, hard=a,b,c,e, 4영업일):**

| top_n | N | P(갭상) | 평균갭 | 다음날 종가 평균 |
|---|---|---|---|---|
| 30 | 20 | 80.0% | +2.96% | -3.41% |
| 50 | 30 | 70.0% | +2.04% | -3.41% |

→ 30→50 확장 시 후보 풀 50% 증가, 평균 갭률 +2.96%→+2.04% / P(갭상) 80%→70%
감소. 다양성 vs 신호 강도 trade-off. 현재 코드는 50 universe 채택 (운영
universe 와 backtest 일치 + 후보 다양성 확보).

**KIS API 운영 universe 와의 관계 (중요):**
- 본 backtest = daily_ohlcv 기반 → 정확한 거래대금 universe 였음.
- 5/12~5/18 실제 KIS API 운영 = `FID_BLNG_CLS_CODE="0"` 버그로 거래량 universe
  → 5일 연속 0종목 현상. backtest 의 통과 종목들이 운영의 거래량 30위 밖에
  있어 누락. **즉 backtest 와 운영 universe 가 어긋났던 게 0종목 본질**, backtest
  자체가 무효였던 게 아님.
- round 41 후속 2 (FID fix) + 후속 3 (30→50 가격 분할) 적용 후 다음 영업일
  (5/20) 부터 backtest 와 일치하는 운영 universe 진입 예상.

시초 매도 정책은 그대로 유지 — 다음날 종가 평균 -3.41% / 시초 평균 +2.04% 라
시초 매도 알파 명백.

**한계 (명시):**
- 표본 4일 — KOSPI 60일 +35~46% 강세장에서만 측정. 약세장 효과 미검증.
- 분봉 부재 → 14:50 시점 매수 가능성 100% 보장 X. 표본 21개 중 점상한가(`high == close`) 0개, ret≥29% 0개로 자동 제외 효과 확인. cap≤27% 가 안전 마진 3% 확보.
- Theme 컷이 제거되어 "테마 동조" 가설(Theme 의 원래 이론) 은 본 룰에서 검증 안 됨. Theme v1 z-score 구현 후 별도 채널로 비교 측정.

**관계:**
- Theme (주도섹터 식별) 은 그대로 유지 — 결정 레포트 헤더의 "최종 주도테마" 섹션 + M6 모니터링용. 다만 결정 후보 universe 컷에서는 Eod.Pick v2 가 Theme 를 우회.
- Theme.Leader (단타 주도주 식별) 은 `docs/scalping-strategy.md` 영역. 결정 레포트의 후보 universe 와는 분리.
- **코드 적용 미완 (round 41 시점):** `src/overnight/candidates.py` (`MIN_DAILY_RETURN=20`, Theme 후보 필터 의존) 와 `src/pipeline.py` 가 아직 v0 룰. v2 적용은 후속 라운드의 `extract_candidates_v2()` 또는 `MIN_DAILY_RETURN` 10/27 cap + Theme 의존 분리 PR.

---

## Eod.GapStats. Historical 갭상 통계 (4-Layer)

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

---

## Eod.Sizing. 사이징

3가지 방법 모두 계산해서 레포트에 표시. Zeta가 보고 선택.

**v2 (2026-05-25) — 거래대금순위 버킷 Kelly (`src/overnight/sizing_bucket.py`):** factor_edge backtest (250일 + 최근 3개월, `scripts/backtest_factor_edge.py`) 로 per-종목 historical(끼)·신고가·시총·종가위치·거래량이 모두 **노이즈(창 사이 뒤집힘)** 로 드러나고, 다음날 갭상을 robust 하게 가르는 유일한 일봉 팩터가 **거래대금순위** 임이 확인됨 (top10 갭상 1년 55%/3개월 64%, 단조). 따라서 사이징을 거래대금순위 버킷(1~10 / 11~25 / 26~50위)의 **rolling-window p/W/L → `kelly_fraction`** 으로 전환. 후보는 **거래대금순위 정렬 + top3 플래그**(사용자 hold-3: 시초 동시매도 부담 → top3 만 매수, `scripts/backtest_top3_selection.py` 검증), top3 종목별 **비중(절대 계좌% + top3내 상대 강약) + 현금버퍼** 표시. 같은 버킷이면 동률(엣지 같음 — 강약은 엣지 다를 때만; 점상한가 등 fat pitch 면 Kelly 가 자동 2~3배). 아래 per-종목 3방식(균등/Kelly/Sharpe)은 참고용으로 강등. ★ 선별 엣지(+0.7%)보다 **청산 타이밍 폭(~9%p)이 13배** 라, 다음 빌드는 시초/NXT 청산 지원.

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

---

## Eod.Exit. 청산 (다음날 시초)

**v0 (단순):**
- 다음날 09:00 KRX 시초가 매도 (단일가)
- "갭상승하면 시초에 바로 익절" 원칙
- 욕심 부려 일중 추가 상승 노리지 X

**Eod.Exit' 시초가 분할 룰 (round 30, P3-2)** — 통설(WikiDocs 종가베팅, brokdam):
- **시초 ≤ +1% (또는 마이너스)** → 전량 매도. 갭 미발생 / 종배 실패. 보유 의미 X.
- **시초 +1% ~ +6%** → 전량 익절. 정상 갭, 단타 종료.
- **시초 ≥ +6%** → 30~50% 분할 익절 (40% 적용), 60% 관망. 강한 갭에선 추가 슈팅 노림.

구현: `src/overnight/exit.py:evaluate_overnight_open_exit()` — 시초가 + 전일종가 입력으로 `OvernightExitDecision(action, partial_ratio, reason)` 반환. **자동 주문 X** (CLAUDE.md 정책) — 09:00 텔레그램 알림 메시지에 권고 표시만.

**Eod.Exit v2 라이브 청산 지원 (2026-05-25):** 시초 1회 판정은 fade 를 못 잡음 —
`scripts/backtest_recent_kelly.py` 의 매도 시점 envelope (top3 시초 +0.7% / 일중최저
−3.7% / 일중최고 +5.5%, 폭 **~9%p**) 에서 보듯 **청산 타이밍이 선별(+0.7%)보다 13배
큰 변수.** 청산 타이밍은 분봉 히스토리 부재로 backtest 불가 → **새 자작 임계값 금지**,
검증된 ≤1/1-6/≥6% 룰을 '시초' 대신 **'현재가' 에 라이브 재평가**(같은 임계값) + **고점
대비 되돌림을 정보로 표시**. `evaluate_overnight_exit_live(prev_close, current,
intraday_high, open_price=None)` → `OvernightExitContext` (current_gap / pullback_from_high
/ decision / note) + `format_overnight_exit_line`. 아침 **다회 체크인** (09:01/10/20/30
cron). 예: 시초 +7%(분할 권고)였다가 현재 +2%로 fade → 현재가 기준 '전량 익절' 자동
전환. **자동 주문 X — 매도 시점은 사람이 결정.** `tests/test_jongbae_exit.py` 7 신규.

**v1 (TODO):**
- NXT 프리장 (08:00~08:50) 활용 — KIS NXT 시세 API 검증 후. NXT 가능 시 08:00~09:00
  프리마켓이 KRX 09:00 동시 매도 러시 전 분산 청산 창 (사용자 hold-3 시초 매도 부담 완화)
- 종목별 NXT 거래 가능 여부 체크 후 우선 청산
- 갭하락 시 30분 내 손절/홀딩 판단 룰

---

## Eod.Exec. 매매 실행

**프로그램 역할:** 레포트 생성 + 알림 발송까지.
**사람(Zeta) 역할:** 모든 매수/매도 실행.

프로그램은 절대 자동 매매를 하지 않는다.

---

## 알림 시점 (종배 영역)

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

단타 영역 알림 (M6 카드 / 09:00~10:30) 은 `docs/scalping-strategy.md` 참조.

---

## 코드 위치 (마이그레이션 후, 2026-05-21)

| 영역 | 모듈 경로 |
|---|---|
| Eod.Market (R1) | `src/overnight/market.py` (또는 pipeline 내부) |
| Eod.Pick (R4) | `src/overnight/candidates.py` |
| Eod.GapStats (R5) | `src/overnight/gap_stats.py` |
| Eod.Sizing (R6) | `src/overnight/sizing.py` |
| Eod.Exit (R7) | `src/overnight/exit.py` |
| Universe (R2) | `src/data/master.py` (공통) |
| Theme (R3) | `src/common/theme.py` |
| 14:50 결정 레포트 | `src/report/decision.py` |

---

## 검증 가능한 사용자 발화 (종배)

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
| 42 (2026-05-25, 레전드 픽 정합) | 사용자(Zeta) "레전드 종배 픽(image/jb.png)이랑 내 후보가 안 맞는 것 같다". 분석(`scripts/analyze_legend_picks.py`): 레전드 픽 64개 중 시스템 Eod.Pick v2 통과 50%뿐. 일봉상승률 중앙값 +7.2% (39%가 +5% 미만, 10개는 하락마감)인데도 레전드는 픽 — 종배 선정축이 일봉 % 가 아님(통설=수급/미야간선물). **결정타:** 시스템이 탈락시킨 픽이 오히려 더 갭상(+3.16% vs 통과 +1.71%), 0~5% 구간 갭상 80%. 1년 top50 backtest(`scripts/backtest_eod_floor.py`)도 0~5% 버킷 56%/+0.41% 양의 EV. 또 사용자 "ret 0 초과로 푼 것 같은데?" → 실제로는 **단타** LEADER_MIN_DAILY_RETURN_PCT 만 0, **종배** MIN_DAILY_RETURN 은 5 그대로(단타/종배 혼동). NXT 표시도 27% 상한이 먼저 상한가를 제외해 무력화되던 모순. | round 42 (사용자 지시, 튜닝 리추얼 통설+backtest 통과): ①**MIN_DAILY_RETURN 5→0** (ret>0 초과, 보합/하락 제외 — 단타와 일관). ②**상한 NXT 조건부** — `extract_candidates(nxt_set=...)` 행별 effective_max: NXT 가능(True)/추정(None=목록미적재) 29.5% / 불가(False) 27%. ③pipeline·scheduler 가 nxt_set 을 후보추출에 주입(카드 표시와 동일 set 재사용). ④decision 레포트 라벨/푸터 갱신. test +5 (NXT 상한·보합컷), 전체 pass. **dry-run(demo) 통과, 운영 1일 검증 후 확정.** NXT 정밀화는 nextrade.co.kr 크롤러로 `data/meta/nxt_tradable.txt` 적재 필요 (현재 전 종목 '추정 가능'으로 29.5 적용). |
| 2026-05-25 (종배 빌드 batch) | 종배 동료 추가 시 그룹 운영 / 양봉·NXT 표시 / 수급·체결강도 누적 / 막판 진입 점검 요청 ("쭉 이어서 다 해줘"). | ①**텔레그램 종배 그룹 라우팅** (`TELEGRAM_EOD_CHAT_ID` — 종배 레포트만 그룹, 단타 M6 카드/봇명령/에러알림은 개인 DM). ②**양봉/장대양봉 카운트 표시** (`gap_stats.candle_count_aux`, 표시 전용 — factor_edge 상 갭 변별력 없음). ③**forward 로깅** (`src/overnight/forward_log.py`: 14:50 후보 벡터 + 다음날 실현 갭 join, 16:40 cron — 수급/체결강도 등 backtest 불가 신호의 미래 factor_edge + 청산 envelope 누적). ④**막판 진입 점검** (`src/overnight/eod_entry.py`: 15:00/10/20 cron, 14:50 top3 대상 VP/점상한가/고점대비 표시 — 새 매수 hard rule X, 표시만). ⑤**NXT 가능/불가/추정 표시** (`src/overnight/nxt.py`: nextrade.co.kr 목록 `data/meta/nxt_tradable.txt` pluggable; 크롤러·KIS NXT 시세/주문 API 는 v1). `fetch_quote` 에 시가(open) 추가. test +24, 1004 pass. |
| 2026-05-25 (Eod.Exit v2 라이브) | 청산이 시초 1회(open vs prev_close) 판정뿐 — fade 를 못 잡음. | 매도 시점 envelope(~9%p)이 선별(+0.7%)보다 13배 큼(`backtest_recent_kelly.py`). 청산 타이밍은 분봉 부재로 backtest 불가 → 새 임계값 X, **검증된 ≤1/1-6/≥6% 룰을 '현재가'에 라이브 재평가 + 고점대비 되돌림 표시**. `evaluate_overnight_exit_live` / `format_overnight_exit_line` 신설, 아침 다회 체크인(09:01/10/20/30). fetch_quote 에 시가(open) 추가. 시초 +7%→현재 +2% fade 시 '전량 익절' 자동 전환. 자동 주문 X. test 7 신규, 980 pass. NXT 프리장(08:00~) 청산 분산은 KIS NXT API 검증 후 v1. |
| 2026-05-25 (Eod.Sizing v2 + top3) | (보강) 사이징이 per-종목 historical(끼) 기반인데, 종목별 끼·신고가·시총·종가위치·거래량을 더 보면 비중을 더 가를 수 있지 않냐는 사용자 질문. | factor_edge backtest (250일 + 3개월, `scripts/backtest_factor_edge.py`/`factor_edge2.py`): 위 팩터 전부 노이즈(창 사이 뒤집힘, 다중비교), **거래대금순위만 robust 변별**(단조, 3개월 더 강함). ①사이징을 **거래대금순위 버킷 rolling Kelly** (`src/overnight/sizing_bucket.py`) 로 전환, per-종목 3방식은 참고 강등. ②후보 **거래대금순위 정렬 + top3 플래그** (사용자 hold-3 = 시초 동시매도 부담, `backtest_top3_selection.py`: top3 갭상 1년 55%/3개월 64%). ③top3 종목별 비중(절대+상대)+현금버퍼 표시. ④**청산 타이밍 envelope(~9%p) ≫ 선별(+0.7%) = 13배** (`backtest_recent_kelly.py`) — 다음 빌드는 시초/NXT 청산 지원. scheduler+pipeline 양쪽 wiring, `test_sizing_bucket.py` 6 신규, 973 pass. |
| 2026-05-21 명명 마이그레이션 | docs/jongbae-strategy.md 한 파일에 단타 (R9~R15) + 종배 (R1~R8) 룰이 R 번호로 섞임. 사용자 (Zeta) 가 매매일지 작성 시 혼동 보고. | 명명 재설계 + 시스템 분리: ①R1 → Eod.Market, R4~R8 → Eod.* 의미있는 이름. ②docs/scalping-strategy.md (단타) + docs/eod-strategy.md (종배, 본 문서) 분리. ③src/jongbae/ → src/overnight/ + src/scalping/ + src/common/ 재구성. ④CLAUDE.md "현재 종배만 구현 중" 표현 정정. |
| 41 후속 3 (backtest 재실행) | round 41 후속 2 에서 backtest 결과도 무효일까 우려. 사용자 (Zeta) "R4 v2 backtest 재실행도 해줘". 재실행 (`scripts/backtest_r4v2.py`, daily_ohlcv 기반): 최대 갭상 LG전자 5/14 +17.97% / 최악 엑스게이트 5/13 -4.88% **둘 다 round 41 본문 결과와 정확히 일치** → 본문 backtest 는 처음부터 daily_ohlcv 기반이라 거래대금 universe 가 정확했음을 확인. 운영 universe 만 거래량이었던 게 진짜 문제 (= 5일 연속 0종목의 본질). 종목 수 차이 (본문 17 vs 재실행 30) 는 (d)(f) hard→soft 정정 차이로 설명. **30 vs 50 universe 비교 (4영업일, hard=a,b,c,e)**: 30위 N=20 P=80.0% 평균+2.96% / 50위 N=30 P=70.0% 평균+2.04%. 다양성 vs 신호 강도 trade-off. | round 41 후속 3 (backtest 재실행 + strategy.md 정정): scripts/backtest_r4v2.py 신규, data/backtest/ 디렉터리 신설, docs/jongbae-strategy.md v2 결과 표 "정확성 재확인" 으로 정정. **다음 영업일 5/20 부터 운영 universe 도 backtest 일치 진입**. |
| 41 후속 2 후속 (30→50 확장) | round 41 후속 2 직후 사용자(Zeta) "50위 가져오는 방법 찾자". KIS volume-rank 가 한 호출당 30개 hard cap. 진단: ctx 페이지네이션 미지원, **가격 범위 분할 작동 확인**. 3회 호출 합집합 90 고유 종목 → 거래대금 desc top 50 = 완벽 cover. | round 41 후속 3 (30→50 확장): _PRICE_BUCKETS 상수 + 가격 버킷 3회 호출 union + trading_value desc top_n. 회귀 테스트 5건. 906 pass. **다음 영업일 (2026-05-20) 14:50 cron 부터 진짜 거래대금 50위 universe 진입**. |
| 41 후속 2 (★ critical) | 2026-05-19 사용자(Zeta) "너가 보여준거 거래대금이 아니라 거래량인거 같은데?". KIS rank 가 거래량 desc 와 일치, 거래대금 desc 와 어긋남. 원인: `fetch_volume_rank` 의 `FID_BLNG_CLS_CODE="0"` (=평균거래량). 정상은 `"3"`. **영향 범위 대규모** — Eod.Pick backtest 5일 분석의 "거래대금 top 50" 라벨이 사실은 "거래량 top 30" — Eod.Pick v2 룰 채택 근거 자체가 잘못된 universe 에서 도출. | round 41 후속 2: `_VOLUME_RANK_BLNG_CLS_TRADING_VALUE = "3"` 상수 신설 + 회귀 테스트 2개 신규. CLAUDE.md "절대 헷갈리지 말 것" 최상단에 거래량≠거래대금 항목 추가. **무효화 표시**: round 41 본문의 5일 backtest 결과는 거래량 universe 기준. fix 적용 후 거래대금 universe 로 다시 5일~ 누적 측정 후 Eod.Pick v2 룰 재검증 필요. Eod.Pick v2 자체 (10%≤ret≤27%, 종가 고가-10% 이내 등) 는 universe 와 무관한 일봉 컷이므로 룰 로직은 유지 가능. |
| 41 후속 ((d)(f) hard→soft) | round 41 Eod.Pick v2 룰 코드 적용 진행 중 사용자(Zeta) 발견 (2026-05-19): (d) 52주 신고가 + (f) Layer 표본 ≥5 를 hard cut 으로 박았더니 5일 backtest 표본 한계 + (d) 가드 까다로워서 후보 0~1종목으로 좁아짐. 사용자 지적: "(d)(f) 는 보조 지표로만 보여주고 hard cut 에서는 제외시켜줘". | `apply_r4v2_post_filters` (d) hard cut 제거 — soft 표시만. scheduler `has_enough_samples` 가드 제거. 결정 레포트 표시 라인 갱신. 테스트 의도 반전. 896 pass. **사용자 정정 의도**: 5일 backtest 같은 작은 표본으로는 (d)(f) 의 통계적 의미가 약함 + 결과적으로 후보가 너무 적게 살아남아 운영 의미 X. |
| 41 후속 2 (ret 5%) | round 41 후속 (d)(f) soft 정정 후 사용자 추가 보고 (2026-05-19): 후보가 여전히 주도섹터 종목만 나옴 + 50위 안 ret=20 엑스게이트는 후보로 선정 X. 사용자 명시 요청: "ret 하한을 10에서 5로 낮춰줘". | `MIN_DAILY_RETURN` 10.0 → 5.0. scheduler 진단 로깅 신규 — snapshot 의 KIS rank 범위 + top 10 + 제외 사유. 테스트 정정. 897 pass. |
| 41 (Eod.Pick v2 채택) | 결정 레포트가 5/12~5/18 5일 연속 후보 0종목인데도 운영 유지. (a) Theme (v0 거래대금 50위 카운트) 가 항상 대형주 테마만 잡음 — 대형주 일봉 변동성으로는 Eod.Pick v0 의 +20%↑ 컷 영영 통과 못함. Theme v1 미구현. (b) 시장엔 매일 +20%↑ 단일종목 10~31개 있음. 거의 다 거래대금 절대값 top100 밖. (c) "주도섹터 안" 제약 풀고 거래대금 top50 만 universe 로 잡아도 진성 갭상 종목 거의 못 잡음. (d) "안전 종배 (top50 안 단순 정량 컷)" 와 "진짜 단타 종배" 는 다른 가설. | round 41 (사후 검증 기반 Eod.Pick v2 확정): ①진입 룰 — `(a) 거래대금 50위 단일종목 + (b) 일봉 상승 + (c) 종가 고가-10% 이내 + (d) 52주 신고가 + (e) 10% ≤ ret ≤ 27% + (f) historical Layer 표본 ≥5`. ②ret 상한 27% cap 채택. ③52주 신고가 채택 (60일/120일/250일 비교 후). ④historical 갭상 비율은 카드 보조 정보로만 표시, 컷으로 사용 X. ⑤Theme (주도섹터) 는 그대로 유지 — 결정 레포트 헤더 + M6 모니터링용. 결정 후보 universe 컷에서는 Eod.Pick v2 가 Theme 를 우회. ⑥시초 매도 정책 재확인 — 다음날 종가까지 보유 시 평균 -2.63%. CLAUDE.md "9:00 KRX 시초 매도" 룰 정당화. |
| 40 (tick 최적화) — 단타 영역 | tick 길어졌다는 사용자 인지 — 처음엔 캐시 + 주기 분리(funnel 5초 주기) 로 풀려고 했음. 사용자(Zeta) 정정. | 단타 영역 정정 — `docs/scalping-strategy.md` 정정 이력 참조 (round 40). |
| 42 (결정 카드 표시 정정, 2026-05-24) | 사용자 보고: "최근에 바꿔달라고 한 게 반영 안 된 느낌". 진단 — ①일봉이 `0 → 3,180` (prev_close=0). 05-21 정정 때 넣은 daily fallback 이 `_enrich_candidates_with_quote` 의 `from src.data.daily import read_daily_ohlcv` 오타(실제 위치 `src.data.storage`)로 ImportError → except 에 삼켜져 영영 안 돎 (정정 미배포). ②시총 전 종목 N/A — master(KIS mst char[172:181]) 시총 컬럼이 모두 0 으로 파싱됨. | ①import 모듈 수정 (storage). ②시총 = 거래대금 / (회전율/100) 역산 fallback (`infer_market_cap_eok`, KIS 가 거래대금회전율 직접 제공 → 표시 회전율과 자기일관) + fetch_quote 는 `hts_avls` 직접 사용. ③카드 표시 사용자 요청 반영: 일봉→`현재가 (+%)` / 일중고점 "현재가 대비" / 거래대금·회전율 괄호 중복단어 제거 / 시총 별도 줄 / "표시만—사이징 미반영"·"자체 누적 시작" 문구 제거 / 거래대금·회전율·수급 **3거래일 추이 + 순위 변동** 신규. |
| 1 | 8:30 시간외에서 갭 익절 가능 | KRX 시간외는 어제 종가 고정 → 9:00 단일가가 첫 갭 |
| 2 | -20~30% = 일중 떡락폭 | +20~30% = 일봉 상승률 |
| 3 | 종가에 매수 | 상한가 진입 순간이 best entry |
| 3-add | 종가 거래대금 순위로 주도섹터 식별 | 장중 누적 거래대금으로 실시간 |
| 4 | 9:00 KRX 시초가 첫 청산 가능 시점 | NXT 프리장 08:00부터 가능 (v1) |
| 4-add | 9:00~9:30이 청산 윈도우 | 시초에 바로 익절 정석 |
| 5 | 장마감 후 16:00 결정 레포트 | **14:50 결정 레포트** (장마감 전) |
| 6 | Layer 4 (고점 도달 시각 매칭) v0 구현 가능 | 분봉 히스토리 부재로 v0 미구현. Layer 1~3만 사용. v1에서 매일 분봉 적재 누적 후 구현 |
| 7 | "거래대금 30위 ≥3종목"이 주도섹터 식별 충분 | 대형주(하이닉스/삼전) 편향 심함. → 테마 단위 breadth + 동일가중 평균상승률 + 회전율 합계 z-score (Theme v1) |
| 8 | Layer 1과 2 점수 가중평균 | 각 Layer 독립 표시 |
| 9 | Sharpe = (avg_gap / std)로 단순 계산 | p × avg_gap_when_up / std (방향성 보정) |
| 10 | 8:30 단일가 → 09:00 NXT 전환에 갭 발생 | 단일가 기간은 어제 종가 고정 — 갭 X |
| 11 | 갭 자체로 익절 종료 신호 | 갭 + 시초 거래량으로 판단 |
| 12 | 음봉 시작 = 매수가 음수로 출발 | 음봉 시작 = 그날 일봉이 음봉 |
| 13 | 시초 NXT 청산은 일반 KRX 청산보다 좋음 | 종목별 NXT 거래 가능 여부 다름, v1에서 정밀화 |
| 14 | 강세장 가정이 자동 ON/OFF 가능 | Zeta가 직관 판단 — 자동 게이팅 X |
| 15 | 종배 시그널은 1~3종목만 골라야 함 | 시그널 종목 수는 Eod.Sizing에 따라 결정 — 1개여도 OK |
| 16 | Layer 3 매칭이 종가위치 단일 조건 | 종가위치 ±5% + Layer 2 (상한가) 둘 다 만족 (2026-05-24: ±2% → ±5% 완화) |
| 17 | 가중치는 종가위치 가중평균 등 복합 | 한 시점 가중치 1.0, 단순화 우선 |
| 18 | 모든 룰이 자동화 가능 | 시장 국면 판정, 사이징 선택은 사람이 — 자동화 영역 분리 |

---

## 관련 문서

- `docs/scalping-strategy.md` — 단타 (Scalping) 전략 룰
- `docs/data-infra.md` — 데이터 인프라
- `docs/report-spec.md` — 종배 레포트 명세
- `docs/plan.md` — 전체 마일스톤
