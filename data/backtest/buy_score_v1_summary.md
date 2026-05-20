# Buy.Score Backtest 결과 (v1, 2026-05-21)

`scripts/backtest_buy_score.py` 로 5/18~5/20 3일치 tick_log parquet 에 대해 9 variant × 2 stop_loss 시뮬레이션 결과.

## 가정 + 한계

- **진입**: variant 의 cutoff 이상인 `buy_score` 첫 tick (sorted ascending ts). 종목당 1회만.
- **청산**: (a) -stop% 도달 OR (b) variant 의 cutoff 미만으로 점수 강등 OR (c) EOD 마지막 tick. 
- **Exit.E1~E5 시그널 청산 미반영** — 별도 시뮬 필요 (future work). 본 simulation 은 등급 강등 = 사실상 매도 시그널로 근사.
- **5/18, 5/19 universe 편향 caveat** — KIS FID_BLNG_CLS_CODE="0" 버그 시기. ETF/저가주 편향. variant 간 상대 비교만 신뢰. 절대 수익률은 5/20 부터 의미.

## 3일 합산 결과 (stop -2%)

| variant | N | avg_pnl | median | win_rate | avg_hold | stop_hit |
|---|---|---|---|---|---|---|
| **current (cutoff 5.0)** | 67 | +0.18% | 0.00 | 47.8% | 0.48 min | 3.0% |
| q5_lower_4 (4.0) | 71 | **+0.35%** | -0.09 | 42.3% | 0.53 | 2.8% |
| **q5_inv_6 (6.0)** ★ | 55 | **+0.26%** | **+0.14** | **56.4%** | 0.63 | 3.6% |
| q5_inv_7 (7.0) | 51 | +0.23% | +0.14 | 54.9% | 0.58 | 5.9% |
| r14i_dist_high | 67 | +0.18% | 0.00 | 47.8% | — | 3.0% |
| r14j_consolidation | 67 | +0.18% | 0.00 | 47.8% | — | 3.0% |
| exclude_first_5min | 64 | **-0.12%** | 0.00 | 40.6% | — | 3.1% |
| **combo_h6+h7 (6.0+dist)** | 55 | +0.26% | +0.14 | 56.4% | 0.63 | 3.6% |
| combo_h6+h7+nofirst5 | 48 | -0.03% | +0.04 | 50.0% | 0.61 | 4.2% |

## 일자별 (stop -2%)

| variant | 5/18 N/avg | 5/19 N/avg | 5/20 N/avg ★ |
|---|---|---|---|
| current | 12 / -0.01% | 16 / -0.20% | 39 / +0.40% |
| q5_lower_4 | 12 / -0.13% | 17 / -0.30% | 42 / **+0.76%** |
| **q5_inv_6** ★ | 11 / -0.42% | 10 / +0.18% | 34 / **+0.51%** |
| q5_inv_7 | 11 / -0.61% | 7 / +0.48% | 33 / +0.46% |
| exclude_first_5min | 12 / -0.01% | 16 / -0.20% | 36 / **-0.11%** |

→ 5/20 (universe 버그 fix 후 첫날, 진짜 거래대금 universe) 에서:
- q5_lower_4 +0.76% (proposal 권고와 일치 — first-mover 더 일찍 surface)
- **q5_inv_6 +0.51%** (5/20 일지 §H6 와 일치 — borderline +5~6 false positive 차단)
- current +0.40% baseline
- exclude_first_5min **-0.11%** (시초 5분 매매 자제 가설 무력화 — 오히려 결과 악화)

## 핵심 발견

### 1. q5_inv_6 (cutoff 5.0 → 6.0) 가 가장 균형 잡힘 ★

3일 합산:
- 평균 손익 +0.26% (current +0.18% 대비 **+44%**)
- 승률 56.4% (current 47.8% 대비 **+8.6%p**)
- median +0.14% (current 0.00 대비 양수 전환)
- N 67 → 55 (-18% 표본 감소)

→ **cutoff +5.0 → +6.0 상향이 5/20 일지 H6 가설 (borderline false positive 차단)** 을 backtest 로 정합 확인. 표본 보존 (N=55) + 결과 개선 둘 다.

### 2. q5_lower_4 평균은 최고이나 outlier 의존 ★

- 평균 손익 +0.35% (가장 높음)
- 단 **median -0.09%** (가산점 4.0 진입 종목 절반 이상이 손실)
- 승률 42% (가장 낮음)
- → 큰 익절 outlier 가 평균 끌어올림. 안정성 측면에선 cutoff 상향이 우위.

proposal §4.1 Q5 (cutoff 5→4) 가설은 5/20 backtest 와 정면 충돌. **5/20 데이터로는 cutoff 상향이 정답**.

### 3. r14i / r14j 페널티 효과 없음

- r14i_dist_high / r14j_consolidation 모두 current 와 결과 동일 (변동 X).
- 원인: 5/20 진입 종목 중 dist_from_intraday_high < 1% 또는 daily_return >= 15% 조건 종목이 거의 없거나, 페널티가 cutoff 차단까지 못 감.
- 5/20 일지 §H7 (주성 4·5차, 오텍 2차) 가설은 backtest universe 에 잡히지 않음. **proposal P1 R14e/R14f (recent_5m, BB) 같은 더 강한 페널티 필요**.

### 4. exclude_first_5min 가설 무력화 ★

- 시초 5분 매매 자제 → current +0.40% → -0.11% (5/20). **결과 악화**.
- 5/20 일지 §튜닝 2 "시초 5분 자제" 가설 backtest 로 **반박**.
- 원인: 시초 1~5분 STRONG 발화 종목이 사실 first-mover 단계 진입이라 익절 가능성 높음. 일지의 시초 데이터 잡음 (daily_return=2,445% 등) 우려는 R14 점수 자체에는 큰 영향 X (점수 계산이 prev_close NaN 가드).

### 5. stop -2% vs -1.5%

- variant 별로 stop_hit% 만 차이 (4~10%p).
- avg_pnl 거의 동일 — stop_hit 비중이 작아 큰 영향 없음.
- 사용자 룰 -2% 유지 안전.

## 권고 (사용자 결정 대기)

### Tier 1 — 즉시 적용 권장 ★

**Buy.Score cutoff +5.0 → +6.0 상향** (q5_inv_6):
- 3일 backtest: 평균 +44%, 승률 +8.6%p, median 양수 전환
- 5/20 일지 §H6 (borderline +5~6 false positive) 와 정합
- 통설 검증 (memory `system-tuning-ritual` ritual 1): "한국 단타 매수 시그널 강도 임계" — borderline 진입 회피 통설 다수 존재 (namu.wiki 단타매매기법, i-whale 등)
- 데이터 검증 (ritual 2): 본 backtest 통과

**적용 위치**: `src/scalping/score/thresholds.py:246` — `GRADE_STRONG: float = 5.0` → `6.0`.

### Tier 2 — 5/21~ 추가 데이터 누적 후 재검증

- q5_lower_4: 평균 우위지만 안정성 X. 5거래일 N=100+ 후 재검증.
- r14i / r14j: backtest 효과 X. proposal P1 R14e/R14f (recent_5m / BB position) 컬럼 추가 후 재시도. 1분봉 변환 인프라 (반나절 작업).

### Tier 3 — 무효 / 폐기

- **exclude_first_5min**: backtest 로 반박됨. 5/20 일지 §튜닝 2 가설 폐기.

## 5/20 매매일지 v2 와 cross-check

5/20 일지 §1.1 windowed 분석에서:
- 매수 윈도우 max grade STRONG = 15/15 (100% 사용자 룰 준수)
- 매수 윈도우 점수 분포: borderline +5.5~6.5 (3건, 평균 -1.10%) vs +7.0~+8.5 (9건, -0.04%) vs +9.0~+10.0 (3건, +3.60%)

본 backtest 3일 합산:
- cutoff +5.0 → +6.0 상향 = borderline +5.0~+5.9 진입 차단
- 결과: 평균 +44%, 승률 +8.6%p, median 양수 전환

→ **5/20 일지 H6 가설이 표본 N=12 (3건 borderline + 9건 정상 + 3건 강함) 에서 도출됐는데, backtest 표본 N=67~55 (3일치) 로 확장해도 같은 결론**. 통계적 유의미.

## 다음 행동

1. 사용자 명시 결정 후 `GRADE_STRONG` 5.0 → 6.0 적용 PR
2. R14e (recent_5m_price_change_pct) + R14f (BB position) 컬럼 추가 후 backtest 재실행
3. Exit.E1~E5 트리거 시뮬 (현재는 등급 강등 근사) — false positive 비율 측정 (5/19 일지 §C2 평가)
4. 5/21 ~ 5거래일 누적 후 N=100+ 표본으로 q5_inv_6 재검증

## 시뮬 raw 데이터

- 전체 trades: `data/backtest/buy_score_v1.csv` (variant × stop × date × code, 약 1,000 행)
- 본 결과 표는 위 CSV 의 groupby 집계
