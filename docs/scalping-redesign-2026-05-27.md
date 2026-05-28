# 단타 매매 시스템 재설계 — 분봉 단저단고 (2026-05-27)

## 0. 한 줄 요약

현재 단타 시스템 (모멘텀/돌파 추종, Buy.Score / Exit.Triggers) 을 **분봉 단저단고
(intraday mean reversion + swing low/high)** 패러다임으로 전면 교체. 검증 후
M6 카드 완전 교체. 종배는 그대로.

**★ 2026-05-29 운영 전환 완료** — Buy.Score/Exit.Triggers 카드 표시 폐기 (tick_log
로깅만 유지). surface 룰 = 주도섹터 Top 3 × (주도주 + 후보) + 수동 + 보유. 자세한
변경 내역은 §11 정정 이력 표 마지막 행 참조. Back-out 절차: `.env` 에
`LEGACY_RISING_FUNNEL=1` 추가 후 데몬 재시작.

본 문서는 `docs/trading-method-separation-discussion.md` §11 ("화력→리더" 운영
전환) 의 후속이며, 5/27 매매일지 (`data/journal/2026-05-27.md`) 의 토론 결과로
시작된 재설계.

---

## 1. 배경

### 1.1 5/27 매매일지에서 출발

- **프로이천 (321260)** 매수 24초 후 -2.68% 손절 (10:11:16 → 10:11:41).
  매수 윈도우 max(buy_grade)=STRONG (10:10:50 시점, 22초 인지 lag).
- **현대모비스 (012330)** STRONG 7.0 매수 후 19분만에 +2.45% 도달, 매도 안 함,
  종일 횡보 후 -0.86% 마감. P1 26회 / A1 60회 / E2 148회 트리거 누적.
- 사용자 진단: "매수 시그널 직후 곧바로 매도 시그널 발화, 매도 룰이 너무 빠른
  청산 + 피로". docs/scalping-strategy.md 의 Buy.Score / Exit.Triggers 가중치
  조정 수준이 아니라 **시스템 자체 갈아엎기** 요구.

### 1.2 5/25 토론과의 관계 ([[scalping-method-separation]])

5/25 backtest_method_weights / tune_exits / 비용 민감도 검증으로 확인된 사실:
- 현재 STRONG 화력 = 비용 빼면 마이너스 (-0.16%/건, ~110건/일, 동시 22개).
- gross 엣지 양수 (돌파 게이트+개선청산 +0.23%) — 비용 0.4% net 음수.
- 청산 "레벨재이탈 -1% 즉시컷" 제거가 핵심 (winner 죽임).
- 검증된 수익 엣지 아직 없음 (in-sample 26건).
- §11 추천: "화력→리더" 운영 전환 + 학습모드. setup_label P1-4 dry-run 완료.

5/27 재설계는 위 결론을 강화 + 확장:
- "화력→리더" 운영 전환은 시그널 surface 정책 변경 (Buy.Score 구조 유지).
- 본 재설계는 **시그널 자체를 단저단고로 교체** = 한 단계 위 변경.
- 5/25 결론의 "비용 정책" / "winner 살림" / "OOS 검증" 원칙은 본 재설계에도
  그대로 적용.

### 1.3 핵심 사용자 진단 (5/27 토론)

| # | 진단 |
|---|---|
| (1) | 현재 시스템 = 돌파매매 위주, "곧 갈거다" 선행 시그널 부재. 돌파 시작 직후 꼭대기 진입 강요 |
| (2) | 분봉을 거의 안 봄 (확인됨: 비-STRONG 종목은 tick_log 의 candle/MA/VP 모두 None) |
| (3) | 모든 지표 통일적으로 봄, 종목별 시그니처 무시 |
| (4) | 매수는 Avoid/Strong 레벨링, 매도는 시그널 하나라도 뜨면 매도 → 짧은 보유 + 피로 |
| (5) | 매수로 곧바로 매도 시그널 발화 (실제 음봉이 바로 뜸) |
| (6) | 단저단고 반복 매매로 종일 등락 폭보다 큰 수익률 가능 |

### 1.4 사용자 비전 (6개 항목, 5/27 합의)

| # | 항목 | 핵심 |
|---|---|---|
| 1 | **주도섹터 정의 강화** | 거래대금 30위 ∩ 회전율 30위 종목들의 섹터 카운트 → 1·2·3위 섹터. 한 종목 여러 섹터 모두 카운트. (현재 50위 거래대금 단일) |
| 2 | **주도주 정의** | 주도섹터 내 상승 + 거래대금 上 + 회전율 上. 두 기준이라 일시 분기 가능, 교체는 큰 이벤트 |
| 3 | **매매 패러다임** | 돌파 추종 → **일중 분봉 단저단고 반복**. 추세 무관 (image/123.jpg SK하이닉스 5/27 W bottom 패턴 참조) |
| 4 | **종목별 시그니처** | 양봉/거래대금/봉형태 비슷 조건의 그 종목 과거 저점/고점 데이터 → Phase 3 운전수 가설. 본 재설계에선 dry-run 로깅만, 운영 X |
| 5 | **매도 룰** | 현재 "Exit.Triggers 1개라도 발화 → 매도" 가 너무 빠른 청산. 단기 고점 도달 신호 기반 매도로 |
| 6 | **수동 종목** | 같은 단저단고 알림 (스윙 보조) |

종배는 그대로 (`docs/eod-strategy.md`). 단타 (`src/scalping/`) 만 변경.

---

## 2. 단저단고 정량 시그널 정의 (v3 확정안)

### 2.1 시간 frame

- **3분봉** 1차 (분봉 swing 표준 단위, image/123.jpg 정합).
- 향후 1분봉 / 5분봉 다중 시간 frame 검토 (STEP 4 후).

### 2.2 평균회귀 지표 (한국 통설 + 검증된 통계)

| 지표 | 정의 | 매수 임계 | 매도 임계 |
|---|---|---|---|
| **Z-score** | (close - MA20) / std20 | ≤ -1.5 | ≥ +0.5 |
| **RSI (14)** | 표준 RSI | ≤ 35 | — (상대 임계 사용) |
| **Bollinger (20, ±2σ)** | bb_lower / bb_upper | close ≤ bb_lower | — |
| **Swing high (5봉)** | prev5_high | — | high < prev5_high (직전 5봉 high 갱신 X) |

### 2.3 봉 패턴 (한국 통설)

| 패턴 | 정의 | 용도 |
|---|---|---|
| **≥2 음봉 후 첫 양봉** | consec_bear ≥ 2 → 현재 봉 bull | 매수 (단저) |
| **≥2 양봉 후 첫 음봉** | consec_bull ≥ 2 → 현재 봉 bear | 매도 (단고) |
| **윗꼬리 음봉** | candle=bear & upper_wick / range ≥ 50% | 매도 추가 |

### 2.4 거래량 게이트

| 지표 | 정의 | 임계 |
|---|---|---|
| **vol_spike** | 현재 봉 거래대금 / 직전 20봉 평균 | ≥ 2.0 (매수 시) |

### 2.5 시장/종목 국면 게이트

- **종목**: daily_return ≥ 0 (상승 종목만 매수, catch falling knife 방지).
- **universe**: 매수 시점 rank ≤ 30 (거래대금) OR turnover ≥ 30 (회전율 강한).
- **시장 (향후 추가)**: KOSPI 200 MA20 위 (5/18, 5/27 같은 약세장 회피).

### 2.6 매수 시그널 (확정안 v3)

```
[필수 모두]
1. candle: ≥2 연속 음봉 후 첫 양봉 (consec_bear ≥ 2, 현재 봉 bull)
2. 평균회귀: zscore_20 ≤ -1.5 OR RSI_14 ≤ 35 OR close ≤ bb_lower
3. 거래량: vol_spike ≥ 2.0
4. 종목 국면: daily_return ≥ 0
5. universe: rank ≤ 30 OR turnover ≥ 30
```

### 2.7 매도 시그널 + 청산 (v3 + v3 청산 우선순위)

```
[매도 시그널 sigS — 보수안]
candle (≥2 양봉 후 첫 음봉 OR 윗꼬리 음봉) + zscore ≥ +0.5

[청산 우선순위 — 발화 우선]
1. stop_loss: 매수가 -2% 도달 (사용자 룰 [[user-trading-rules]] 정합)
2. trailing: peak * -1% 이탈 (peak ≥ 매수가 +0.5% 일 때만)
3. sigS: 매도 시그널 발화
4. max_hold: 보유 30분 (= 10 × 3분봉)
5. EOD: 15:15 이후 강제 청산
```

### 2.8 사용자 비전 (1) 주도섹터 정의 (별도 모듈)

본 재설계의 매수 universe 게이트 (§2.5) 의 입력. STEP 4 분봉 인프라와 함께
구현. 현재 모듈 (`src/scalping/sector/`) 의 50위 단일 기준 → 30위 ∩ 30위
교집합으로 변경. 세부 알고리즘:

```
매시점 (예: 10초 주기):
  candidates = 거래대금 30위 종목들 ∩ 회전율 30위 종목들 (ETF/etc 제외)
  for 종목 in candidates:
    for 섹터 in 종목.속한_섹터_리스트:   # 한 종목 여러 섹터 모두 카운트
      sector_count[섹터] += 1
  sorted_sectors = sector_count.sort_values(desc)
  주도섹터 = sorted_sectors[0]
  후보_섹터 = sorted_sectors[1], sorted_sectors[2]

주도주 정의 (주도섹터 내):
  내부 종목들 중 daily_return > 0 + (거래대금 1위, 회전율 1위)
  두 기준이 다를 경우 일시적으로 주도주 둘로 분기 (TRANSITION 이벤트)
```

---

## 3. 백테스트 결과 (5/18~5/27, 6 거래일)

### 3.1 종합

| 버전 | n | gross/건 | net(0.4%) | net(0.15%) | 승률 | 보유 중앙 | 비고 |
|---|---|---|---|---|---|---|---|
| v1 (단순 candle + 평균회귀 매도) | 256 | -0.343% | -0.74% | -0.49% | 47% | 87분 | EOD 보유 위주 |
| v2 (+ stop -2% + max_hold 30분 + 국면) | 267 | -0.082% | -0.48% | -0.23% | 43% | 9분 | swing 형태 확보 |
| **v3 (+ vol_spike 2x, 매도 보수, trailing)** | **18** | **+0.082%** | **-0.32%** | **-0.07%** | **56%** | 11분 | **gross 양수 도달** |
| v3.1 (+ 사용자 universe 게이트 rank≤30) | 15 | -0.038% | -0.44% | -0.19% | 60% | 12분 | 표본 줄음 |
| v3.2 (시그널 완화 vol 1.5x, zscore -1.0) | 43 | -0.218% | -0.62% | -0.37% | 51% | 12분 | 표본↑ 품질↓ |

### 3.2 핵심 발견

1. **사용자 비전 정량 정의 가능** (v3 까지). gross 양수 도달.
2. **net 양수 엣지 6일 데이터로 검증 불가**. 비용 0.15% 가정에서도 -0.07~ -0.49%.
3. **표본 ↔ 품질 trade-off 고착** — 엄격 (v3, 18건) / 완화 (v3.2, 43건) 의 net 거의
   동일. 더 큰 universe / 더 긴 누적 없이 빠져나갈 수 없음.
4. **winner 살림이 알파 본체** — v3 청산 사유별 max_hold +1.08% 승률 100%
   (peak +1.30%) / trailing +0.19% 승률 60% / stop_loss -2% 0%. 사용자 진단
   (4)(5) 정량 입증.
5. **holdout (5/27) 일관 음수** — train +0.4% / holdout -0.7%. 5/27 약세장 +
   단저단고 mean reversion 부조화. 시장 국면 게이트 필수.

### 3.3 v3 청산 사유별 상세

| 사유 | n | 평균 손익 | 승률 | peak 평균 |
|---|---|---|---|---|
| max_hold (30분 강제) | 7 | **+1.08%** | **100%** | +1.30% |
| trailing (peak -1%) | 5 | +0.19% | 60% | +1.20% |
| stop_loss (-2%) | 3 | -2.00% | 0% | +0.27% |
| eod | 1 | -0.21% | 0% | +0.52% |
| eod_force | 2 | -0.41% | 0% | +0.07% |

→ **max_hold > trailing** = trailing -1% 가 winner 너무 일찍 죽임. 향후 -1.5% 또는
  ATR 기반 동적 trailing 검토.

---

## 4. STEP 4 — 분봉 인프라 확대 설계

### 4.1 현재 상태 (확인 결과)

- `src/data/intraday.py` 가 KIS volume-rank → 거래대금 30~50위 + STRONG 등으로
  surface 된 종목만 `inquire-price` (현재가) 3초 tick 수집.
- candle / MA / VP / 호가는 `src/scalping/scoring/` 의 worker 가 surface 종목에
  한정 산출.
- 결과: tick_log 의 비-STRONG 종목 (SK하이닉스 같은 대형주) 은 raw price 만
  있고 candle / MA / VP 모두 None.

### 4.2 필요한 변경

| 컴포넌트 | 현재 | 변경 후 |
|---|---|---|
| Universe 선정 | 거래대금 50위 단일 | **거래대금 30위 ∩ 회전율 30위** (사용자 비전 1) |
| 분봉 OHLC | 비-surface 종목 X | **모든 universe 종목 3분봉 / 1분봉 fetch** (KIS `inquire-time-itemchartprice`) |
| 평균회귀 지표 | X | **MA5/20, Bollinger(20,±2σ), RSI(14), zscore** 산출 worker 추가 |
| VP / 호가 | surface 종목만 | universe 모든 종목 (KIS 호출 rate 부담 검토) |
| 섹터 카운팅 | 단일 테마 ≥3 | 30위 ∩ 30위 교집합 → 섹터 카운트 1·2·3위 |

### 4.3 KIS API 부담 추정

- KIS rate limit: 초당 20회.
- universe ~50종목 × (현재가 + 분봉) = 100 호출 / 3초 cycle = ~33 호출/초.
- → 분봉은 더 긴 cycle (예: 30초 ~ 1분) 로 분리. 현재가만 3초 cycle 유지.
- 또는 분봉을 tick aggregation 으로 직접 구성 (현재 tick log 도 이미 그렇게 가능).
  단 분봉 OHLC 정확도는 tick 빈도에 의존.

### 4.4 우선순위

1. **분봉 aggregation 직접 구성** (tick log → 3분봉 OHLC) — 추가 API 호출 X,
   즉시 가능. 5/27 백테스트도 이 방식.
2. **MA / Bollinger / RSI / Z-score 산출 worker** — 모든 universe 종목으로
   확대.
3. Universe 확대 (거래대금 30위 ∩ 회전율 30위) — KIS volume-rank 호출 정책
   변경.
4. (선택) KIS 분봉 API fetch — accuracy 검증 후.

→ **1~3 만으로도 본 재설계 백테스트 + dry-run 카드 가능**. STEP 4 핵심 작업.

---

## 5. 운영 전환 ritual (한 달 누적 + 검증 후)

### 5.1 raw timeline

```
Phase 0 (현재 ~ 6/15) : 라이브 매매 = 기존 시스템 (Buy.Score / Exit.Triggers).
                       단저단고 시그널 = dry-run 카드 (M6 옆 별도 라인).
                       데이터 누적 + 분봉 인프라 확대 (STEP 4).

Phase 1 (6/16 ~ 6/22) : 한 달치 (5/18~6/18) walk-forward expanding 백테스트.
                       train (5/18~6/4) / holdout (6/5~6/18) 의 v3 결과 정량 평가.
                       net 양수 미달 시 시그널 튜닝 + ritual.

Phase 2 (6/23 이후) : net 양수 검증 시 운영 전환.
                       M6 카드 단저단고 시그널로 교체.
                       기존 Buy.Score / Exit.Triggers 폐기 (또는 보조 라인).
```

### 5.2 system tuning ritual ([[system-tuning-ritual]])

매 시그널 변경 (예: trailing 임계 -1% → -1.5%) 전:

1. **한국 단타 통설 검색** (WebSearch / WebFetch) — 검증된 기법인지 확인.
2. **데이터 검증** — 변경 전후 walk-forward expanding 백테스트, train/holdout
   양쪽 net 양수 확인.
3. 두 단계 통과 후만 적용. 1건 표본 단독 변경 X.

### 5.3 fallback

- 한 달 누적 후에도 net 양수 미검증 시: 운영 전환 보류. 비용 정책 (지정가 +
  유동 리더) 별도 검토 + 시간 추가 누적.
- 사용자 메모 [[autotrade-future-intent]] 참조 — 4~6개월 후 자동매매 전환
  의향. 단저단고 검증된 net 양수 엣지 확보 후 자동매매 후보.

---

## 6. 사용자 비전 (3) 종목별 시그니처 — dry-run 만

5/27 합의: 데이터 부족이라 보류, 통합 단저단고 우선. 단 **dry-run 로깅은
시작**:

- 매수 / 매도 시점의 종목별 패턴 (양봉 비율, 거래량 분포, 봉 형태 누적) tick_log
  에 추가 컬럼 (예: `signature_features` JSON 컬럼).
- 매매일지 누적 (`data/journal/*.md`) 에 종목별 패턴 메모.
- Phase 3 (3~6개월 누적 후) — 종목별 임계 차별화 검토. 운전수 가설
  [[long-term-vision]].

본 재설계 Phase 2 에선 모든 종목 동일 임계 적용.

---

## 7. 사용자 비전 (6) 수동 종목 — 단저단고 알림

기존 M6 "수동 등록 종목" 에 단저단고 시그널 알림 추가:

- `/watch CODE` → 단저단고 시그널 발화 시 카드에 표시 (별도 푸시 X, M6 정책 유지).
- 스윙 보조용 — 매매 X, 알림만.
- 검증 후 운영 전환 시 같이 적용.

---

## 8. scope + 명시적 X

### 8.1 scope

- 단타 (`src/scalping/`) 만 변경.
- 종배 (`src/overnight/`, `docs/eod-strategy.md`) 그대로.
- 공통 (`src/common/`) — universe 정의 (1) 변경으로 일부 영향 (Theme.Leader 등
  rank 입력 변경 가능성).

### 8.2 명시적 X

- 자동 매매 X (CLAUDE.md 자동매매 금지 정책 유지). 단저단고 시그널도 카드 표시
  + 사용자 수동 매매.
- 단일 매매일지로 시그널 변경 X (system tuning ritual 통과 필수).
- 검증 안 된 net 양수 가정 매매 X — Phase 0 동안 dry-run 만, 라이브 매매는
  기존 시스템 (사용자 명시 의사 [[user-trading-rules]]).
- 비용 정책 (지정가 + 유동 리더) 은 본 재설계 scope 외. 별도 토론.

---

## 9. 즉시 작업 (다음 세션 또는 본 세션 추가)

### 완료 (2026-05-27 본 세션)

| # | 작업 | 산출물 | 상태 |
|---|---|---|---|
| A | `src/scalping/signals/mean_reversion.py` v3 시그널 (sigB/sigS) | classify() vectorized + classify_tick_realtime() | ✓ |
| B | 분봉 aggregation 모듈 `src/scalping/bars.py` | build_bars(ticks, freq) | ✓ |
| D | 백테스트 인프라 `src/research/backtest_mean_reversion.py` CLI | data/backtest/mr_v3_baseline.json 자동 생성, 비용 시나리오 3개 | ✓ |

### 다음 세션 (라이브 통합 필요)

| # | 작업 | 핵심 변경 |
|---|---|---|
| C | Universe 확대 `src/common/universe.py` — 거래대금 30위 ∩ 회전율 30위 | 사용자 비전 (1) |
| 10 | tick 단위 실시간 swing worker — M6 worker 에 classify_tick_realtime() 통합 + 봉 진행 중 tick low/high state | 봉 close 지연 0.37% 회피 |
| 12 | M6 카드 단저단고 라인 — classify() 호출 추가, sigB/sigS/청산 트리거 표시 | 라이브 교체 vs dry-run 사용자 결정 |
| F | 매매일지 자동 통계 — 사용자 매매가 단저단고 시그널과 정합한지 자동 평가 | `data/journal/auto/` |

### 5/27 백테스트 baseline (모듈 + CLI 검증)

`python -m src.research.backtest_mean_reversion` 실행 결과:
- n=1,107 매매 / 6 거래일 (평균 184건/일)
- gross -0.137%/건 / 승률 43.5% / 보유 중앙 9분
- **sigS 매도 시그널: 20건 승률 100% 평균 +0.91%** (봉 패턴 보강 효과)
- stop_loss 166건 -2% (15%) → 매수 시그널 false swing 비율 ~43% 정합
- net 비용 시나리오: 시장가 0.4% → -0.54% / 지정가 0.2% → -0.34% / 유동리더 0.15% → -0.29%
- **봉 close 지연 슬리피지 +0.37% 보정 시: net 0.15% → +0.08% (양수 도달)**

---

## 10. 관련 문서 / 메모리 / 코드

### 문서
- [`docs/scalping-strategy.md`](scalping-strategy.md) — 현재 Buy.Score / Exit.Triggers 정량 룰.
- [`docs/trading-method-separation-discussion.md`](trading-method-separation-discussion.md) — 5/25 토론 (배경).
- [`docs/scalping-method-taxonomy.md`](scalping-method-taxonomy.md) — 매매법 분류 v0.
- [`docs/buy-score-revision-proposal.md`](buy-score-revision-proposal.md) — 이전 Buy.Score 재설계 시도.
- [`docs/trading-journal.md`](trading-journal.md) — 매매일지 작성 가이드.
- [`data/journal/2026-05-27.md`](../data/journal/2026-05-27.md) — 본 재설계 트리거 일지.
- [`docs/plan.md`](plan.md) — 전체 진행 상황.

### 메모리
- [[scalping-method-separation]] — 5/25 결론.
- [[system-tuning-ritual]] — 통설 + 데이터 검증.
- [[user-trading-rules]] — 사용자 매매 룰 baseline.
- [[user-trading-vision]] — 사용자 결정 평가 X / 시스템 정확도 + 튜닝 가설 누적.
- [[long-term-vision]] — Phase 1/2/3, 운전수 가설.
- [[autotrade-future-intent]] — 4~6개월 후 자동매매 의향.
- [[volume-vs-trading-value]] — 거래량/거래대금 혼동 방지.

### 코드 (예정)
- `src/scalping/signals/mean_reversion.py` (신규)
- `src/scalping/bars.py` (신규)
- `src/common/universe.py` (확장)
- `src/research/backtest_mean_reversion.py` (신규)

### 백테스트 결과 데이터
- 본 문서 §3 표 — 5/18~5/27 6일 v1~v3.2 결과.
- 작업 스크립트: 본 세션 inline (재현 가능하게 향후 `scripts/backtest_mr.py` 로 이관).

---

## 11. 정정 이력

| 일자 | 변경 | 사유 |
|---|---|---|
| 2026-05-27 | 본 문서 초안 작성 | 5/27 매매일지 토론 결과. 단저단고 패러다임 전환 결정 |
| 2026-05-27 (후속) | v3 시그널 정의 보강 — vol_spike/bid_ask_ratio/vol_accel 제거, 평균회귀 OR 조합 | feature selection AUC 분석: 가속/호가 noise 입증 (0.50~0.53), 평균회귀 단일 임계는 SK하이닉스 4 swing 중 1개만 잡음 |
| 2026-05-27 (후속) | 종목별 차이 = 통합 룰 hard ceiling 확인 | 한온시스템 zscore AUC -0.80 (평균회귀) vs 삼성전자 ma5 AUC +0.92 (추세). 사용자 비전 (3) 종목별 시그니처 필수 입증 |
| 2026-05-27 (후속) | 봉 close 지연 = gross 음수 주범 폭로 | sigB 발화 봉의 43%가 false swing (다음 봉 더 빠짐), 매수 시점 vs 진짜 저점 평균 +0.37% 슬리피지. 해결 = tick 실시간 swing 감지 (classify_tick_realtime) |
| 2026-05-27 (후속) | bars.py / mean_reversion.py / backtest_mean_reversion.py 신규 + CLI 검증 | 모듈화 완료, baseline 결과 data/backtest/mr_v3_baseline.json 저장. M6 worker 통합은 다음 세션 |
| 2026-05-28 | universe 게이트 정정 — auto/rising/manual/holding 우회 | 초안에선 universe 미통과 시 모든 종목 막힘 = 사용자가 수동 등록한 종목 (예: SK하이닉스) 도 막힘. 사용자 명시: universe 게이트는 자동 추가 종목 풀 좁힘 용도, 사용자 관심 종목 (auto/rising/manual/holding) 은 항상 분석. worker.py `_in_mr_universe` 로직 수정 |
| 2026-05-28 | "universe 통과 = N 종목" 의미 명확화 | "6 종목" = 거래대금 30위 ∩ 회전율 30위 교집합 = **주도섹터 후보 종목 풀** (사용자 비전 1 정의). 주도주 1~3 개가 아니라 그 풀 안에서 섹터 카운트 → 1·2·3위 섹터 → 섹터별 주도주 식별 |
| 2026-05-28 | `intersect_scalping_universe` dead code 발견 | 사용자가 PWA 카드에서 "거래대금 43위" 종목 surface 본 후 조사 — universe.py 함수 정의만 있고 호출처 0건. surface 자체 룰 재설계 필요 인식 |
| **2026-05-29 ★ 운영 전환** | **단저단고 패러다임 single 머지** — Buy.Score/Exit.Triggers 카드 표시 폐기, surface 룰 = 주도섹터 Top 3 × (거래대금∩회전율 1위) 주도주 + (2위 == 2위) 후보 + 수동 + 보유 | 사용자 결정: Phase 1 holdout 누적 미달이지만 dry-run 1일 결과 (031330 +1.24%, 주도주 한정 시뮬 net +1.38%) + universe 결함 발견으로 즉시 전환. Back-out: `LEGACY_RISING_FUNNEL=1` env 토글. 변경 파일: `src/common/theme.py` (select_leaders_and_candidates 신규), `src/dashboard/worker.py` (surface 파이프라인), `src/dashboard/state.py` (MonitoredStock 에 sector_role/surface_sector_name/mr_history 신규 + push_mr_event 헬퍼), `src/dashboard/render.py` (카드/페이로드 재구성), `src/dashboard/static/{index.html, app.js, manifest.json}` (이름 + 라벨), `src/data/tick_log.py` (sector_role/surface_sector_name/surface_source 신규 컬럼) |

향후 단저단고 시그널 / 임계 / 청산 룰 변경 시 본 표에 행 추가. 5/25 결론
([[scalping-method-separation]]) 강화하는 변경은 같은 행에 cross-ref.
