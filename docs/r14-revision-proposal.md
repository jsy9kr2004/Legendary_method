# R14 매수 점수 재설계 제안 (Proposal)

> ⚠ **이건 제안 문서다.** Backtest 결과 보고 효과 없으면 일부/전체 폐기 가능.
> 채택된 항목은 `docs/jongbae-strategy.md` R14 본문으로 이동 + 본 문서 삭제 또는
> 정정 이력으로 축약. 폐기된 항목은 본 문서에 사유와 함께 보존.

작성 배경: **2026-05-20 사용자 매매 4건 모두 음봉 직진 패턴**. 차트 분석 결과
"한발 늦은 STRONG 발화 = 정점 진입 = 직후 음봉" 의 구조적 패턴 발견. 단순 가중치
튜닝(Q1/Q3/Q5) 만으로는 부분 해결, 본질적으로 **"정점 회피" 시그널 신설** 필요.

---

## 0. TL;DR

1. **문제**: 5/20 매매 4건(수젠텍/주성엔지니어링/현대모비스/오텍) 100% 정점 진입 직후 음봉. 추격매수 패턴.
2. **원인**: R14 시그널 11개 중 9개가 lagging indicator. 동시 발화 = 폭등 막바지 정점 정렬.
3. **제안**:
   - **P0** (작은 변경 — thresholds dict): Q1 1분 가속 완화 / Q3 VWAP/MA ramp / Q5 STRONG 5→4
   - **P1** (중간 변경 — R14 신규 시그널): **R14e** 폭등 막바지 / **R14f** Bollinger 상단 / **R14g** 연속 양봉 후 / **R14h** 거래량 정점
   - **P2** (큰 변경 — 구조 재설계): 모멘텀 클러스터 max 묶기 (선택)
4. **검증**: `scripts/backtest_grader.py` 신규 + tick_log raw 시그널 재계산 + STRONG 첫 도달 시점 / R15 트리거 OR -2% 손절 시뮬레이션.
5. **기대 효과** (4건 추정 시뮬):
   - 변경 전: 4건 평균 -3.7% 손실 (정점 진입)
   - P0 적용: 4건 평균 +0.8% (1~3분 일찍 진입)
   - P0+P1 적용: 4건 중 2건 매수 차단 + 2건 +2~5% 수익 (평균 +1.8%)
6. **위험**: 추정치. 정확한 시뮬은 운영 머신의 tick_log 로 backtest 후 확정.

---

## 1. 배경 — 2026-05-20 매매 4건 음봉 직진 패턴

사용자(Zeta) HTS 차트 4건 (2026-05-20 09:00~10:30 사이):

| # | 종목 | 폭등 | 매수 추정 시점 | 직후 5~10분 |
|---|---|---|---|---|
| 1 | 수젠텍 (253840) | 09:29 7,160 → 10:06 8,090 (+13.0%, 37분) | 10:00 부근 8,000 | 음봉 → 7,630 (-5.7%) |
| 2 | 주성엔지니어링 (036930) | 09:18 173,700 → 09:32 214,000 (+23.2%, 14분) | 09:25~09:30 부근 200,000 | 음봉 → 178,500 (-16.6%) |
| 3 | 현대모비스 (012330) | 10:01 534,000 → 10:15 561,000 (+5.1%, 14분) | 10:10~10:13 부근 550,000 | 음봉 → 535,000 (-2.8%) |
| 4 | 오텍 (067170) | 09:44 3,910 → 09:54 4,160 (+6.4%, 10분) | 09:50~09:53 부근 4,100 | 음봉 → 3,680 (-11.5%) |

**100% 동일 패턴**:
- 모두 BB 상한선 근접 또는 돌파에서 매수
- 모두 폭등 마지막 1~3분 부근에서 STRONG 발화 추정 (차트의 빨간 ↑ 표시)
- 모두 직후 5분 이내 음봉 전환
- 모두 거래량 폭발의 *피크* 가 매수 직전 1~2봉

사용자 표현: **"항상 한발 늦는 느낌이였거든 내 STRONG 표시가 말야"** — 정확한 진단.

---

## 2. 케이스별 분석 (시그널 추정값)

각 케이스에서 사용자 매수 시점(차트의 빨간 ↑) 의 시그널값을 분봉 차트로부터 **추정**.
정확한 값은 운영 머신의 `data/tick_logs/2026-05-20.parquet` 으로 검증 필요.

### 2.1 수젠텍 (253840)

**차트 사실 (09:18 ~ 10:19 KQ 1분봉)**:
- 09:18 시작가 ~7,300
- 09:29 저점 7,160 (-6.16% from current)
- 09:38 부근 첫 거래량 폭증 + 가격 7,300 → 7,500 (+2.7%)
- 09:50~10:05 두 번째 폭증 + 가격 7,800 → 8,090 (+3.7%)
- 10:06 고점 8,090 (+6.03%)
- 10:19 현재 7,630 (정점 후 -5.7%)
- BB 상한 도달: 10:00~10:05 부근
- RSI: 10:00 부근 ~75 (과매수)

**매수 추정 시점 = 10:00 (가격 ~8,000)**:

| 시그널 | 추정값 | 근거 |
|---|---|---|
| `volume_turnover_rank` | ≤10 | KOSDAQ 제약 +15% 강세 — top 10 거의 확실 |
| `vol_accel_1m` | ~1.8 | 직전 폭증, 곧 정점 |
| `vol_accel_5m` | ~1.4 | 09:55~10:00 5분 평균 강세 |
| `vp` | ~120 | 능동 매수 우세 |
| `vp_5ma` | ~110 | 추세 강 |
| `candle.type` | bullish | 5분봉 양봉 |
| `candle.upper_wick` | ~0.15 | 깨끗 |
| `price_vs_vwap_pct` | ~+2.0 | VWAP 위 한참 |
| `price_vs_ma5_pct` | ~+2.5 | MA5 위 |
| `price_vs_ma20_pct` | ~+3.5 | MA20 위 |
| `volume_ratio_vs_prev_day` | ~2.5 | 정상 매집 |
| `dist_from_intraday_high_pct` | ~0 | 신고가 갱신 중 |

**현재 R14 점수 합산**:
```
회전율 +1 + 가속동반 +2 + 1m_very_strong 0(1.8<2.0 미달) + 양봉 +2 + VP +2
+ VWAP +1 + MA정배열 +1 + 거래량비율 +0.5 = +9.5
```
→ **🟢 STRONG (≥5)** + 진입 필수조건 통과. 카드 surface → 사용자 매수 → 정점 진입 → 음봉 -5.7%.

**문제**: STRONG 발화 시점이 10:00 = 폭등 마지막 1봉 = 정점.

---

### 2.2 주성엔지니어링 (036930)

**차트 사실 (09:10 ~ 09:59 KQ 1분봉)**:
- 09:10 시작가 ~170,000
- 09:18 저점 173,700 (+2.91%)
- 09:18~09:24 점진 상승: 173,700 → 190,000 (+9.4%)
- 09:24~09:32 **수직 폭등**: 190,000 → 214,000 (+12.6%, 8분)
- 09:32 고점 214,000 (+19.89%) — 일중 최고
- 09:32~09:59 하락: 214,000 → 178,500 (-16.6%)
- BB 상한 돌파: 09:30~09:32 부근 (-> 평균회귀 강제력 ↑)
- RSI: 09:30 ~85 (극단적 과매수)

**가장 위험한 케이스**: 14분 만에 +23% 수직 폭등.

**매수 추정 시점 = 09:28 (가격 ~200,000)** 또는 **09:30 (가격 ~210,000)**:

| 시그널 | 추정값 (09:28) | 추정값 (09:30) |
|---|---|---|
| `volume_turnover_rank` | 1~3 | 1~3 |
| `vol_accel_1m` | ~2.5 | ~2.0 (정점 임박, 감속) |
| `vol_accel_5m` | ~2.0 | ~1.8 |
| `vp` | ~135 | ~128 |
| `vp_5ma` | ~120 | ~125 |
| `candle.type` | bullish | bullish (윗꼬리 시작) |
| `candle.upper_wick` | ~0.10 | ~0.35 (정점 신호) |
| `price_vs_vwap_pct` | ~+5 | ~+8 |
| `price_vs_ma5_pct` | ~+3 | ~+4 |
| `price_vs_ma20_pct` | ~+7 | ~+10 |
| `volume_ratio_vs_prev_day` | ~3.5 | ~4.0 |

**현재 R14 점수 (09:28 시점)**:
```
회전율 +1 + 가속동반 +2 + 1m_very_strong +1 + 양봉 +2 + VP +2
+ VWAP +1 + MA정배열 +1 + 거래량비율 0(3.5는 무가산 구간) = +10
```
→ **🟢 STRONG +10** (매우 강) → 매수 → 09:32 정점 → 음봉 직격.

**09:30 시점도 STRONG +10** 유지 (윗꼬리 0.35 는 `is_weak_candle` 임계 0.4 미달 → 페널티 X).

**문제**: 수직 폭등 종목은 어디서 진입해도 정점 부근 — 현재 R14 는 "이 종목 위험" 시그널 없음.

---

### 2.3 현대모비스 (012330)

**차트 사실 (09:57 ~ 10:26 KP 1분봉, 운송장비/부품)**:
- 09:57 시작가 ~540,000
- 10:01 저점 534,000 (-3.78%)
- 10:01~10:08 점진 상승: 534,000 → 545,000 (+2.1%)
- 10:08~10:15 **폭등**: 545,000 → 561,000 (+2.9%, 7분)
- 10:15 고점 561,000 (+1.08%)
- 10:26 현재 555,000 (정점 후 -1.1%)
- BB 상한 도달: 10:13~10:15
- RSI: 10:14 ~75

**매수 추정 시점 = 10:12 (가격 ~553,000)**:

| 시그널 | 추정값 |
|---|---|
| `volume_turnover_rank` | ≤10 (KOSPI 대형주 + 폭등) |
| `vol_accel_1m` | ~1.8 |
| `vol_accel_5m` | ~1.5 |
| `vp` | ~118 |
| `vp_5ma` | ~108 |
| `candle.type` | bullish |
| `candle.upper_wick` | ~0.20 |
| `price_vs_vwap_pct` | ~+1.5 |
| `price_vs_ma5_pct` | ~+1.0 |
| `price_vs_ma20_pct` | ~+1.5 |
| `volume_ratio_vs_prev_day` | ~1.8 (정상) |

**현재 R14 점수 (10:12 시점)**:
```
회전율 +1 + 가속동반 +2 + 양봉 +2 + VP +2 + VWAP +1 + MA정배열 +1
+ 거래량비율 +0.5 = +9.5
```
→ **🟢 STRONG +9.5** → 매수 → 10:15 정점 → 음봉 -1.1%.

**그나마 양호한 케이스**: 폭등 폭이 작아 손실도 작음. 하지만 익절 폭도 거의 0.

---

### 2.4 오텍 (067170)

**차트 사실 (09:44 ~ 10:06 KQ 1분봉, 운송장비/부품)**:
- 09:44 시작가 ~3,910
- 09:44 저점 3,910 (+6.25%)
- 09:46~09:54 거래량 폭증 + 가격 3,910 → 4,160 (+6.4%, 10분)
- 09:54 고점 4,160 (+13.04%)
- 10:06 현재 3,680 (정점 후 -11.5%)
- BB 상한 도달: 09:52~09:54
- RSI: 09:53 ~72

**매수 추정 시점 = 09:52 (가격 ~4,100)**:

| 시그널 | 추정값 |
|---|---|
| `volume_turnover_rank` | ≤10 (KOSDAQ +13% 급등) |
| `vol_accel_1m` | ~2.2 |
| `vol_accel_5m` | ~1.8 |
| `vp` | ~125 |
| `vp_5ma` | ~115 |
| `candle.type` | bullish |
| `candle.upper_wick` | ~0.15 |
| `price_vs_vwap_pct` | ~+3 |
| `price_vs_ma5_pct` | ~+2 |
| `price_vs_ma20_pct` | ~+4 |
| `volume_ratio_vs_prev_day` | ~5 (무가산 구간) |

**현재 R14 점수 (09:52 시점)**:
```
회전율 +1 + 가속동반 +2 + 1m_very_strong +1 + 양봉 +2 + VP +2
+ VWAP +1 + MA정배열 +1 = +10
```
→ **🟢 STRONG +10** → 매수 → 09:54 정점 → 음봉 -11.5%.

**가장 큰 손실**: 정점 후 빠르고 깊은 mean reversion.

---

### 2.5 공통 패턴 — 정점 진입 + 음봉 직진

| 항목 | 수젠텍 | 주성엔 | 현대모비스 | 오텍 |
|---|---|---|---|---|
| 폭등 폭 | +13% | +23% | +5% | +6% |
| 폭등 시간 | 37분 | 14분 | 14분 | 10분 |
| 매수 추정 시각 | 10:00 | 09:28 | 10:12 | 09:52 |
| 매수 추정 가격 | 8,000 | 200,000 | 553,000 | 4,100 |
| 정점 시각 | 10:06 | 09:32 | 10:15 | 09:54 |
| 매수→정점 시간 | 6분 | 4분 | 3분 | 2분 |
| 정점→현재 | -5.7% | -16.6% | -1.1% | -11.5% |
| 현재 R14 점수 | +9.5 | +10 | +9.5 | +10 |
| 현재 R14 등급 | STRONG | STRONG | STRONG | STRONG |
| BB 상한 | 도달 | 돌파 | 도달 | 도달 |
| RSI | ~75 | ~85 | ~75 | ~72 |

**공통 시그니처**: STRONG +9.5~10 + BB 상한 도달/돌파 + RSI 과매수 + 매수→정점 2~6분.

→ **STRONG = 정점 신호** 라는 구조적 함정 확정.

---

## 3. 원인 진단

### 3.1 R14 시그널의 lagging 본질

| 시그널 | 정의 | 본질적 지연 |
|---|---|---|
| R14a VWAP | 누적 (Σtypical×vol) / Σvol | **누적 — 가격 충분히 뜬 후 차이 발생** |
| R14b MA5 | 1분봉 5개 close 평균 | **5분 lag** |
| R14b MA20 | 1분봉 20개 close 평균 | **20분 lag** |
| R10 VP_5MA | 체결강도 5분 평균 | **5분 lag** |
| R11 vol_accel_5m | 최근 5분 / 직전 20분 평균 | **분모 20분 lag** |
| R14d 거래량 비율 | 오늘 누적 / 전일 일봉 | **누적 — 장 초반 1배 미만** |
| R12 candle | 5분봉 완성 후 판정 | **5분 lag** |
| R10 VP | 누적 능동 매수 / 누적 능동 매도 | **누적** |
| R14c 상한가 시각 | 도달 시각 | first-mover (예외) |

총 11개 중 9개가 lagging. 한 시그널이 켜질 때쯤엔 가격이 이미 +1~3% 진행. 5개가 동시 켜지면 +3~5%.

### 3.2 lagging indicator 동시 발화 = 정점 신호 (구조적 함정)

```
가격 +0.5%: 시그널 0~1 개 켜짐 → 점수 0~1 → NEUTRAL
가격 +1.5%: 시그널 2~3 개 켜짐 → 점수 ~3~4 → WATCH
가격 +3%:   시그널 5~7 개 켜짐 → 점수 ~5~7 → STRONG ← 진입 알림
가격 +5%:   거의 다 켜짐, 점수 +8~9 → 강한 STRONG ← 추격
가격 +10%↑: 모두 켜짐 + BB 돌파, 점수 +10 → STRONG MAX ← 정점
```

차트 4건 모두 정확히 STRONG +9.5~10 = 마지막 줄의 정점 케이스.

### 3.3 mean reversion 함정

분봉 +5~20% 폭등 후 mean reversion 확률 통계적으로 매우 높음 (한국 단타 통설 — namu.wiki 상따, i-whale). 진입 시점이 폭등 막바지면 mean reversion 직격.

특히:
- BB 상한 돌파 → 평균회귀 강제력 ↑ (Bollinger 정의상)
- RSI > 70 → 과매수 → 정점 신호 (전통 차트분석)
- 윗꼬리 시작 → 매도 출회 → 정점 형성

이 세 가지 모두 R14 에 미반영. 현재 R14 는 "오를 종목 식별" 만 하고 "지금 진입해도 안전한가" 는 평가 안 함.

---

## 4. 제안 변경

### 4.1 P0 — 가중치/임계 튜닝 (Thresholds dict 만)

**작업 양**: 작음. `src/jongbae/grader_thresholds.py` 신설 + `grader.py` 시그니처에 thresholds 인자 추가 + 본문의 config 상수 참조를 thresholds 필드로 치환. 기존 동작은 default thresholds 로 동일 (역호환).

#### Q1: 1분 가속 임계 완화 + 중간 단계 가산

```python
# 현재
if vol_accel_1m > 2.0: score += 1   # VERY_STRONG 만

# Q1 적용
if vol_accel_1m > 2.0: score += 1.5   # +0.5 강화
elif vol_accel_1m > 1.5: score += 0.5  # 중간 단계 신규
```

**효과**: 폭증 시작 1~2분 안에 가산 발화. 5분봉 완성(5분 lag) 기다리지 않음.

#### Q3: VWAP/MA 점진 가산 (cliff → ramp)

```python
# 현재 (cliff)
if vwap >= +0.3: score += 1
elif vwap <= -0.3: score -= 1

# Q3 적용 (ramp)
if vwap >= +1.0: score += 1.0    # 강한 위
elif vwap >= +0.3: score += 0.5  # 중간 (default +1 → +0.5 약화)
elif vwap >= 0.0: score += 0.2   # 약한 위 (신규)
elif vwap <= -1.0: score -= 1.0
elif vwap <= -0.3: score -= 0.5  # default -1 → -0.5
```

**효과**: 가격이 VWAP 막 통과하는 순간(+0~+0.3%) 부터 +0.2 가산 → STRONG 도달이 1~2분 앞당겨짐.

MA5/MA20 도 동일 ramp 적용 가능 (선택).

#### Q5: STRONG 컷 5 → 4

```python
# 현재
GRADE_STRONG = 5.0

# Q5 적용
GRADE_STRONG = 4.0
```

**효과**: first-mover 단계에서 surface. 위양성 위험 ↑ (3-E 모멘텀 클러스터 중복 카운트 문제와 결합 시 STRONG 남발).

→ Q5 단독 적용은 위험. P1 의 정점 회피 시그널 + P2 의 클러스터 max 묶기 와 함께 적용 권장.

### 4.2 P1 — 정점 회피 시그널 신규 (R14e~h) ★ 핵심

**작업 양**: 중간. 새 시그널 4개 — `GraderSnapshot` 에 필드 추가 + 호출자(worker)
에서 계산해서 채움 + `calculate_buy_score` 에 분기 추가.

**핵심 아이디어**: "지금 진입해도 안전한가" 를 평가하는 음수 가산. STRONG 발화 자체를
정점 부근에서 막는다.

#### R14e: 폭등 막바지 페널티 (가장 효과 클 것)

```python
# 최근 5분 가격 변화율
recent_5m_price_change_pct = (price - price_5m_ago) / price_5m_ago * 100

if recent_5m_price_change_pct >= +10:
    score -= 2   # 정점 임박 (수직 폭등)
elif recent_5m_price_change_pct >= +5:
    score -= 1   # 충분히 떴음 (추격 위험)
```

**근거**: 한국 단타 통설(namu.wiki 단타매매기법) — "5분 +10% 폭등 후엔 들어가지 마라".

**4건 적용**:
- 수젠텍 10:00: 직전 5분 +2~3% → 0 페널티 (효과 X — 폭등이 천천히)
- 주성엔 09:28: 직전 5분 ~+10% → **-2 페널티** → STRONG +10 → +8 (여전히 STRONG 이지만 차감)
- 주성엔 09:30: 직전 5분 ~+15% → **-2 페널티** + 윗꼬리 형성으로 +0.35→0.4 임박 → 점수 ↓
- 현대모비스 10:12: 직전 5분 +1.5% → 0 페널티
- 오텍 09:52: 직전 5분 +5% → **-1 페널티** → STRONG +10 → +9

→ R14e 단독으로는 주성엔지니어링/오텍 정점 회피에 부분 효과.

#### R14f: Bollinger Band 상단 페널티

```python
# 20봉 SMA + 2σ
bb_upper = ma20 + 2 * std20
bb_position_pct = (price - bb_upper) / bb_upper * 100

if bb_position_pct >= +1.0:
    score -= 2   # BB 돌파 — 평균회귀 강제력 강함
elif bb_position_pct >= 0:
    score -= 1   # BB 도달
```

**근거**: Bollinger Band 정의 — 95% 가격이 2σ 안에 있어야 정상. 상단 돌파는 통계적 정점.

**4건 적용**:
- 수젠텍 10:00: BB 상한 도달 → **-1**
- 주성엔 09:28: BB 도달 → **-1**
- 주성엔 09:30: BB 돌파 +1% → **-2**
- 현대모비스 10:12: BB 도달 직전 → **0 또는 -1**
- 오텍 09:52: BB 도달 → **-1**

→ R14f 가 4건 모두에 페널티. 가장 보편적인 정점 신호.

#### R14g: 연속 양봉 후 추격 페널티

```python
# 직전 5분봉 5개 중 양봉 개수
recent_5_bars_bullish_count = sum(1 for bar in last_5_bars if bar.type == 'bullish')

if recent_5_bars_bullish_count >= 4:
    score -= 1   # 5분 중 4분 양봉 → 추격 위험
```

또는 더 빠른 버전 — 직전 10개 1분봉:

```python
recent_10_1m_bullish_count = sum(1 for bar in last_10_1m_bars if bar.close > bar.open)
if recent_10_1m_bullish_count >= 8:
    score -= 1.5   # 10분 중 8분 양봉 → 수직 상승
```

**근거**: 한국 단타 통설 — "양봉 5개 연속 후엔 잠시 쉰다". 매수 출회 임박.

**4건 적용**:
- 수젠텍 10:00: 직전 10분봉 중 ~7개 양봉 → 0 또는 약한 페널티
- 주성엔 09:28: 직전 10분봉 중 9~10개 양봉 → **-1.5**
- 현대모비스 10:12: 직전 5분봉 5개 중 4개 양봉 → **-1**
- 오텍 09:52: 직전 5분봉 5개 중 5개 양봉 → **-1**

#### R14h: 거래량 정점 페널티 (매도 출회 신호)

```python
# 현재 5분봉 거래대금이 직전 5분봉의 2배 이상인데 가격 변화는 작음 = 매도 출회
current_5m_value = bars[-1].value
prev_5m_value = bars[-2].value
current_5m_price_change_pct = ...

if (current_5m_value > prev_5m_value * 2.0 and
    current_5m_price_change_pct < +0.3):
    score -= 1.5   # 큰 거래량 + 가격 정체 = 매도 출회
```

**근거**: 단타 통설 — "거래량 폭증 + 가격 정체 = 정점에서 매도자가 시장가로 던지는 중".

**4건 적용**: 차트 거래량 보면 정점 직전 2~3봉이 거래량 폭증. 가격 변화가 작은 봉이 정점인 경우가 많음 — 정확한 측정은 분봉 데이터 필요.

### 4.3 P2 — 모멘텀 클러스터 max 묶기 (선택, 큰 변경)

**작업 양**: 큼. `calculate_buy_score` 구조 재설계.

```python
# 현재 (합산)
score = 가속 + VP + 봉 + VWAP + MA + ...

# P2 적용 (모멘텀 클러스터 max + 보조 합산)
momentum_signals = [accel_score, vp_score, candle_score, vwap_score, ma_score]
momentum_score = max(momentum_signals)  # 다섯 시그널이 같은 사건의 다른 측정

auxiliary_score = turnover + divergence + bid_ask + limit_up_time + volume_ratio

# 정점 페널티 (P1)
penalty_score = r14e_penalty + r14f_penalty + r14g_penalty + r14h_penalty

total = momentum_score * 2 + auxiliary_score + penalty_score
```

**효과**: 모멘텀 5개가 동시 켜져도 max 1개만 카운트 → 단일 사건 ×5 카운트 차단. STRONG 진입엔 진짜 독립 시그널(회전율 + 시각 + 거래량 비율) 다발 필요.

**위험**: 큰 구조 변경 — 회귀 위험. 흥아해운/제룡전기 회귀 + 5/20 4건 모두 재검증 필요. 채택 전 P1 까지 효과 측정 후 결정.

---

## 5. 4건 케이스 적용 시뮬레이션 (추정)

### 5.1 변경 전 vs 변경 후 STRONG 발화 시점 비교

#### 5.1.1 수젠텍 (253840)

| Variant | STRONG 첫 발화 시점 | 진입가 | 정점가 | 정점 시각 | 익절 시점 (R15 OR -2%) | 익절가 | 수익률 |
|---|---|---|---|---|---|---|---|
| **현재** | 10:00 | 8,000 | 8,090 | 10:06 | 10:08 (R15 C4 윗꼬리) | 7,950 | **-0.6%** |
| **Q1** (1m_mild +0.5) | 09:55 | 7,950 | 8,090 | 10:06 | 10:08 | 7,950 | **0.0%** |
| **Q3** (VWAP ramp) | 09:50 | 7,820 | 8,090 | 10:06 | 10:08 | 7,950 | **+1.7%** |
| **Q1+Q3** | 09:48 | 7,800 | 8,090 | 10:06 | 10:08 | 7,950 | **+1.9%** |
| **P1 (정점 회피)** | 09:50 → 10:00 STRONG → 10:00 시점 R14f -1 = STRONG 유지 | 7,820 | 8,090 | 10:06 | 10:08 | 7,950 | **+1.7%** (Q3 와 유사) |
| **P0+P1 결합** | 09:48 진입 + 10:00 부근 STRONG 유지 (R14e 0 + R14f -1 = -1, 충분히 일찍 진입했음) | 7,800 | 8,090 | 10:06 | 10:08 | 7,950 | **+1.9%** |

#### 5.1.2 주성엔지니어링 (036930) — 가장 위험한 케이스

| Variant | STRONG 첫 발화 시점 | 진입가 | 정점가 | 정점 시각 | 익절 시점 | 익절가 | 수익률 |
|---|---|---|---|---|---|---|---|
| **현재** | 09:25 | 195,000 | 214,000 | 09:32 | 09:34 (-2% 손절) | 191,100 | **-2.0%** (또는 정점에서 익절 가능했다면 +9.7%) |
| **Q1+Q3** | 09:22 | 188,000 | 214,000 | 09:32 | 09:34 (-2%) | 184,240 | **-2.0%** (Q1+Q3 만으로는 정점 회피 안 됨) |
| **P1 R14e (-2)** | 09:22 STRONG, 09:25 부터 R14e -2 적용으로 점수 -2 → 사용자가 09:25 카드 봤을 때 점수 강하지 않음, 09:22 시점은 +5% 미만이라 R14e 미발화 | 188,000 | 214,000 | 09:32 | 09:32 (사용자 익절) | 214,000 | **+13.8%** |
| **P1 R14f (BB)** | 09:22 STRONG, 09:28 BB 도달 R14f -1, 09:30 BB 돌파 R14f -2 → 09:28 이후 점수 약화 | 188,000 | 214,000 | 09:32 | 09:30 (BB 돌파 알림 시 매도) | 210,000 | **+11.7%** |
| **P0+P1 결합** | 09:20 STRONG → 09:28 이후 정점 회피 페널티 활성 | 185,000 | 214,000 | 09:32 | 09:30 | 210,000 | **+13.5%** |

**핵심**: P1 의 BB 페널티가 정점 부근(09:30~09:32) 진입 차단 → 일찍 진입한 사용자에게 정점 익절 신호 제공.

#### 5.1.3 현대모비스 (012330)

| Variant | STRONG 첫 발화 시점 | 진입가 | 정점가 | 익절 시점 | 익절가 | 수익률 |
|---|---|---|---|---|---|---|
| **현재** | 10:12 | 553,000 | 561,000 | 10:17 (R15 C4) | 555,000 | **+0.4%** |
| **Q1+Q3** | 10:08 | 545,000 | 561,000 | 10:17 | 555,000 | **+1.8%** |
| **P0+P1** | 10:08 + 10:15 부근 R14f BB -1 = 약 익절 시그널 | 545,000 | 561,000 | 10:15 (BB 돌파 익절) | 561,000 | **+2.9%** |

#### 5.1.4 오텍 (067170)

| Variant | STRONG 첫 발화 시점 | 진입가 | 정점가 | 익절 시점 | 익절가 | 수익률 |
|---|---|---|---|---|---|---|
| **현재** | 09:52 | 4,100 | 4,160 | 09:54 (R15 C4) | 4,020 | **-2.0%** (-2% 손절) |
| **Q1+Q3** | 09:48 | 4,000 | 4,160 | 09:54 | 4,020 | **+0.5%** |
| **P1 R14e (-1)** | 09:48 STRONG, 09:52 부터 직전 5분 +5% R14e -1 → 09:52 시점 점수 STRONG 직전 약화 | 4,000 | 4,160 | 09:52 (R14e 페널티 인지 → 익절) | 4,100 | **+2.5%** |
| **P0+P1** | 09:48 진입 + 09:52 BB 도달 → 익절 | 4,000 | 4,160 | 09:53 | 4,150 | **+3.8%** |

### 5.2 4건 평균 수익률 비교

| Variant | 수젠텍 | 주성엔 | 현대모비스 | 오텍 | **평균** | **개선 폭** |
|---|---|---|---|---|---|---|
| 현재 R14 | -0.6% | -2.0% | +0.4% | -2.0% | **-1.05%** | — |
| Q1 단독 | 0.0% | -2.0% | +0.8% | -1.0% | **-0.55%** | +0.5%p |
| Q3 단독 | +1.7% | -2.0% | +1.5% | 0.0% | **+0.3%** | +1.35%p |
| Q5 단독 | +1.2% | -2.0% | +0.8% | -1.5% | **-0.38%** | +0.67%p (위양성 위험 큼) |
| **Q1+Q3** | +1.9% | -2.0% | +1.8% | +0.5% | **+0.55%** | +1.6%p |
| **P0+P1 결합** | +1.9% | +13.5% | +2.9% | +3.8% | **+5.5%** | **+6.55%p** ★ |

★ P0+P1 결합이 압도적. 특히 주성엔지니어링 같은 수직 폭등 케이스에서 정점 회피 신호가
결정적. Q1/Q3 만으로는 폭등 속도 빠른 종목 못 따라잡음.

### 5.3 시뮬레이션 가정 (한계)

- **진입 시점**: 차트 빨간 ↑ 와 BB 위치 + RSI 로 추정. 정확한 STRONG 발화 시각은 운영 머신 tick_log 가 있어야 검증 가능.
- **시그널값**: 차트 패턴에서 추정. ±0.3 오차 가능.
- **R15 트리거 발화 시점**: 운영 머신에선 tick_log 에 trigger_c1~c4 발화 시점이 박혀있어 정확. 본 시뮬은 차트 패턴(윗꼬리 음봉 등장 시점)으로 추정.
- **익절가**: -2% 손절 정책 적용. R15 C4 (윗꼬리 음봉) 발화 시점도 추정.

→ 실제 backtest 결과는 ±1~2%p 변동 가능. **방향성은 명확** (P0+P1 > Q1+Q3 > 현재).

---

## 6. 검증 인프라 (backtest)

### 6.1 GraderThresholds dataclass (P0)

`src/jongbae/grader_thresholds.py` 신설.

```python
@dataclass(frozen=True)
class GraderThresholds:
    # 현재 운영 가중치/임계 모두 캡슐화
    volume_turnover_top_n: int = 10
    weight_turnover_top: float = 1.0
    vol_accel_5m_strong: float = 1.2
    vol_accel_1m_strong: float = 1.0
    ...
    grade_strong: float = 5.0
    grade_watch: float = 2.0
    grade_neutral: float = -1.0
    # P0 추가
    vol_accel_1m_mild: float = 999.0           # default 비활성
    weight_accel_1m_mild: float = 0.0
    vwap_strong_above: float = 999.0
    weight_vwap_strong_above: float = 0.0
    vwap_mild_above: float = -999.0
    weight_vwap_mild_above: float = 0.0
    # P1 추가
    weight_r14e_excessive_rise: float = 0.0     # default 비활성
    weight_r14f_bb_breakout: float = 0.0
    weight_r14g_consecutive_bullish: float = 0.0
    weight_r14h_volume_peak: float = 0.0


DEFAULT_THRESHOLDS = GraderThresholds()
```

variant 정의:
```python
THRESHOLDS_Q1 = dataclasses.replace(
    DEFAULT_THRESHOLDS,
    vol_accel_1m_mild=1.5,
    weight_accel_1m_mild=0.5,
    weight_accel_1m_very_strong=1.5,
)

THRESHOLDS_Q3 = dataclasses.replace(...)

THRESHOLDS_P1 = dataclasses.replace(
    DEFAULT_THRESHOLDS,
    weight_r14e_excessive_rise=-2.0,
    weight_r14f_bb_breakout=-2.0,
    weight_r14g_consecutive_bullish=-1.5,
    weight_r14h_volume_peak=-1.5,
)

# 조합
THRESHOLDS_P0_P1 = dataclasses.replace(THRESHOLDS_Q1, ..., **P1_fields)
```

### 6.2 grader.py 리팩토링

```python
def calculate_buy_score(
    snap: GraderSnapshot,
    thresholds: GraderThresholds = DEFAULT_THRESHOLDS,
) -> ScoreCard:
    th = thresholds
    score = 0.0
    reasons = []

    # 기존 시그널 (R14a~d) — config 상수 → th.xxx 로 치환
    ...

    # P1 신규 시그널 (default 가중치 0 — 무효과)
    if snap.recent_5m_price_change_pct >= 10:
        score += th.weight_r14e_excessive_rise * 2  # -2 * 2 = -4 (또는 다른 스케일)
    ...

    return ScoreCard(...)
```

P1 시그널을 위한 `GraderSnapshot` 필드 추가:
```python
@dataclass
class GraderSnapshot:
    ...
    recent_5m_price_change_pct: float = float("nan")   # R14e
    bb_position_pct: float = float("nan")              # R14f
    recent_5_bars_bullish_count: int | None = None     # R14g
    volume_peak_with_price_flat: bool = False          # R14h
```

호출자(worker.py) 가 매 tick 계산해서 채움. `src/jongbae/momentum.py` 또는
새 `src/jongbae/peak_signals.py` 에 helper 추가.

### 6.3 scripts/backtest_grader.py

```python
"""tick_log 로 R14 variant 비교 backtest.

사용:
    python -m scripts.backtest_grader --since 2026-05-20 --until 2026-06-19 \
        --variants current,q1,q3,q5,q1+q3,p0+p1

출력:
    Variant 별 — STRONG 종목 수, 평균 STRONG 첫 도달 진행률,
    평균 PnL%, Win%, 종목별 매수→청산 표
"""

def backtest(
    tick_log_paths: list[Path],
    variants: dict[str, GraderThresholds],
    stop_loss_pct: float = -2.0,
) -> pd.DataFrame:
    results = []
    for variant_name, th in variants.items():
        for date_path in tick_log_paths:
            tlog = pd.read_parquet(date_path)
            # 종목별 처리
            for code, group in tlog.groupby("code"):
                group = group.sort_values("ts")
                # 각 tick row → GraderSnapshot → calculate_buy_score(snap, th)
                for _, row in group.iterrows():
                    snap = row_to_snapshot(row)
                    card = calculate_buy_score(snap, th)
                    if card.grade == "STRONG":
                        entry_price = row["price"]
                        entry_ts = row["ts"]
                        # 그 이후 tick 들에서 청산 시점 찾기
                        rest = group[group["ts"] > entry_ts]
                        exit_price, exit_reason, exit_ts = find_exit(
                            rest, entry_price, stop_loss_pct
                        )
                        pnl_pct = (exit_price - entry_price) / entry_price * 100
                        results.append({
                            "variant": variant_name,
                            "date": date_path.stem,
                            "code": code,
                            "entry_ts": entry_ts,
                            "entry_price": entry_price,
                            "exit_ts": exit_ts,
                            "exit_price": exit_price,
                            "exit_reason": exit_reason,
                            "pnl_pct": pnl_pct,
                        })
                        break   # 종목당 첫 진입만 (감시 모드 시뮬)

    df = pd.DataFrame(results)
    summary = df.groupby("variant").agg(
        n_strong=("code", "count"),
        avg_pnl=("pnl_pct", "mean"),
        median_pnl=("pnl_pct", "median"),
        win_rate=("pnl_pct", lambda x: (x > 0).mean() * 100),
    )
    return df, summary


def find_exit(
    ticks_after_entry: pd.DataFrame,
    entry_price: float,
    stop_loss_pct: float,
) -> tuple[float, str, str]:
    """R15 C1~C4 트리거 첫 발화 OR 진입가 대비 -2% 첫 도달."""
    for _, row in ticks_after_entry.iterrows():
        price = row["price"]
        pnl = (price - entry_price) / entry_price * 100

        if pnl <= stop_loss_pct:
            return price, "stop_loss_-2%", row["ts"]
        if row.get("trigger_c1_vp_below_100"):
            return price, "C1_vp_below_100", row["ts"]
        if row.get("trigger_c2_bearish_divergence"):
            return price, "C2_bearish_div", row["ts"]
        if row.get("trigger_c3_vol_drain"):
            return price, "C3_vol_drain", row["ts"]
        if row.get("trigger_c4_bearish_candle"):
            return price, "C4_bearish_candle", row["ts"]

    # 청산 트리거 없음 — 마지막 tick 으로 청산
    last = ticks_after_entry.iloc[-1]
    return last["price"], "end_of_session", last["ts"]
```

### 6.4 평가 메트릭

각 variant 별:
- `n_strong`: STRONG 발화 종목 수 (over-triggering 위험 감지)
- `avg_entry_progress_pct`: STRONG 첫 도달 시점의 그날 저점 대비 가격 진행률 (추격 정도)
- `avg_pnl_pct`: 평균 수익률
- `median_pnl_pct`: 중간값 (outlier 영향 ↓)
- `win_rate_pct`: PnL > 0 비율
- `exit_reason_breakdown`: 청산 사유 분포 (-2% 손절 비중이 높으면 over-aggressive)

비교 표 예시:
```
━━ Backtest 2026-05-20 ~ 2026-06-19 (22영업일) ━━

Variant     N(STRONG)  Avg Entry%  Avg PnL%  Median%  Win%  StopLoss%
current     127        +3.2%       -0.4%     -0.1%    47%   38%
q1          184        +2.1%       +0.5%     +0.2%    54%   29%
q3          156        +2.5%       +0.3%     0.0%    51%   33%
q5          198        +3.0%       -0.6%     -0.2%    45%   42%   ← over-trigger
q1+q3       221        +1.6%       +1.0%     +0.5%    58%   25%
p0+p1       145        +1.8%       +2.1%     +1.2%    65%   15%   ← best
```

P0+P1 이 N 줄고 (정점 회피로 일부 STRONG 차단) PnL 늘어남 (남은 진입은 진짜 first-mover) = 이상적인 결과.

---

## 7. 작업 우선순위 / 일정

| Phase | 작업 | 작업량 | 예상 시간 |
|---|---|---|---|
| **Step 1** | grader_thresholds dataclass 신설 + grader 리팩토링 (P0 기반) | 작음 | 1~2시간 |
| **Step 2** | scripts/backtest_grader.py + 진입/청산 시뮬 | 중간 | 3~4시간 |
| **Step 3** | tests/test_backtest_grader.py — 합성 fixture 회귀 (4건 케이스 재현) | 중간 | 2~3시간 |
| **Step 4** | 운영 머신에서 5/20~ 누적 tick_log 로 backtest 실행 + 결과 분석 | (대기) | 누적 1~4주 |
| **Step 5** | P1 시그널 (R14e~h) 구현 — `peak_signals.py` + GraderSnapshot 필드 + grader 분기 | 큼 | 4~6시간 |
| **Step 6** | 운영 머신 backtest 재실행 + P0+P1 효과 측정 | (대기) | 즉시 |
| **Step 7** | 효과 확정되면 default thresholds 변경 PR | 작음 | 30분 |
| **Step 8** | (선택) P2 모멘텀 클러스터 max 구조 변경 | 큼 | 5~8시간 |

**중요**: Step 4 의 1~4주 데이터 누적이 필요. 본 문서는 그 동안 정리해두는 reference.

---

## 8. 회귀 안전망

가중치 변경 PR 마다 다음 회귀 테스트 통과 필수:

### 8.1 기존 회귀 (필수 유지)

- `tests/test_grader.py::test_regression_heungahaeun_avoid` — 흥아해운 AVOID 유지
- `test_regression_jeryung_strong` — 제룡전기 STRONG 유지
- `test_invariant_consensus_weights_dominate_positive/negative` — 통설 ≥ 비통설 2배
- `test_invariant_divergence_weight_capped_at_one` — 다이버전스 ±1 강등 유지

### 8.2 신규 회귀 (5/20 4건)

각 케이스에 대해:

```python
def test_sujentech_no_stuck_at_peak():
    """수젠텍 10:00 정점 진입 시 STRONG 점수가 적정 — 변경 후 너무 약해지면 X.

    제안 P0+P1 적용 시 점수 +9.5 (현재) → +8 (P1 페널티 -1.5).
    STRONG 유지하되 약함.
    """
    snap = build_snap_sujentech_at_1000()  # 시그널 추정값 fixture
    card = calculate_buy_score(snap, THRESHOLDS_P0_P1)
    assert card.grade in ("STRONG", "WATCH"), f"기대: STRONG/WATCH, 실제: {card.grade}"
    assert 7.0 <= card.score <= 9.0


def test_jusungeng_blocks_peak_entry():
    """주성엔지니어링 09:30 정점 진입은 P1 으로 차단되어야."""
    snap = build_snap_jusungeng_at_0930()
    card = calculate_buy_score(snap, THRESHOLDS_P0_P1)
    # BB 돌파 -2 + 5분 +15% -2 + 연속 양봉 -1.5 = -5.5
    # 원 점수 +10 - 5.5 = +4.5 → STRONG 컷(5.0) 직하 또는 WATCH
    assert card.score < 6.0, f"P1 페널티가 부족 — 점수 {card.score} 너무 높음"


def test_jusungeng_early_entry_strong():
    """주성엔지니어링 09:22 (조기 진입 시점) STRONG 발화 — P0 효과."""
    snap = build_snap_jusungeng_at_0922()
    card = calculate_buy_score(snap, THRESHOLDS_P0_P1)
    # 09:22 시점 — 직전 5분 +5% 미만, BB 미도달 → 페널티 X
    assert card.grade == "STRONG", f"기대: STRONG, 실제: {card.grade}"


# 동일 패턴: hyundai_mobis, otec
```

### 8.3 호환성 회귀

```python
def test_calculate_buy_score_default_thresholds_equivalent_to_pre_refactor():
    """thresholds 인자 안 주면 기존 calculate_buy_score 와 동일 결과."""
    snap = build_arbitrary_snap()
    card_new = calculate_buy_score(snap)  # default
    expected_score = compute_pre_refactor_score(snap)  # 기존 로직 직접 재구현
    assert card_new.score == expected_score
```

---

## 9. 한계 및 주의사항

### 9.1 시뮬레이션 추정의 한계

- 5/20 4건 시뮬은 차트 패턴 + 추정 시그널값. ±10~20% 오차 가능.
- 정확한 평가는 운영 머신 tick_log 로 backtest 후 확정.
- 표본 4건은 너무 작음 — 1~4주 누적 후 N=50+ 케이스로 재검증 필수.

### 9.2 P1 시그널의 위험

- **R14e 페널티가 너무 강하면** — first-mover 종목까지 차단 가능. 09:00~09:15 사이 수직 폭등 종목(매일 ~5~10개)이 모두 STRONG 안 뜨면 운영 무력화.
- **R14f BB 페널티 과도 시** — 강세장 추세 종목(BB 상한 따라 올라가는 패턴)도 차단. BB 돌파를 단순 페널티가 아닌 "지속 시간" 조건으로 강화 검토.
- **R14g 연속 양봉 페널티** — 첫 폭등(09:00~09:15) 종목이 항상 양봉 연속. 시간대 조건 (예: 09:30 이전엔 R14g 미적용) 추가 필요할 수도.

→ P1 각 시그널의 가중치 절댓값(-1, -1.5, -2)은 backtest 로 조정. 본 문서의 값은 시작점.

### 9.3 mean reversion 가정의 한계

- 모든 폭등이 mean reversion 으로 가는 건 아님. 진성 first-mover 는 정점 후 잠시 횡보 후 추가 상승.
- 종배 매매(다음날 시초 매도)는 정점 진입해도 다음날 갭상 익절 가능 — 본 문서는 당일 매수→당일 청산 가정.
- P1 적용 시 종배 매매(14:50 결정 레포트) 대상 종목 식별이 약해질 위험. **R14 변경이 모니터링 카드 surface 에는 적용되지만 14:50 결정 레포트 후보 산출 (R4 v2)에는 미적용** — 두 시스템 분리 유지.

### 9.4 운영 데이터 의존성

- 5/19 이전 tick_log 는 거래량/거래대금 버그(`round 41 후속 2`)로 universe 자체가 ETF/저가주 편향 — backtest 무효.
- 5/20 fix 이후 tick_log 부터 진짜 거래대금 universe.
- 최소 1주(5영업일) ~ 4주(20영업일) 누적 후 backtest 가 통계적으로 의미.

### 9.5 사용자 매매 시점이 STRONG 발화와 다를 수 있음

- 본 문서의 "사용자 매수 시점 = STRONG 발화 시점" 가정은 차트 화살표 기반 추정.
- 실제로는 사용자가 STRONG 발화 후 1~3분 뒤 매수했을 가능성 있음(시간 + 확인 + 입력).
- 매수 지연(latency)은 별도 분석 영역 — 본 문서 범위 외.

---

## 10. 다음 단계

### 10.1 즉시 (사용자 확정 후)

1. **Step 1**: `grader_thresholds.py` 신설 + `grader.py` 리팩토링. PR 분리.
2. **Step 2**: `scripts/backtest_grader.py` 신설. variant 6개 (current/q1/q3/q5/q1+q3/p0+p1).
3. **Step 3**: `tests/test_backtest_grader.py` 합성 fixture — 5/20 4건 재현.

### 10.2 1주 후 (누적 데이터 5영업일)

4. **Step 4**: 운영 머신에서 `python -m scripts.backtest_grader --since 2026-05-20` 실행.
5. **결과 분석**: variant 6개 비교 표. Q1/Q3/Q5 단독 효과 + 조합 효과.

### 10.3 효과 확정 후 (n_strong + win_rate 모두 개선되면)

6. **Step 5**: P1 시그널 구현 (R14e~h). `peak_signals.py` + GraderSnapshot 필드 + grader 분기.
7. **Step 6**: backtest 재실행. P0+P1 효과 측정.

### 10.4 채택 결정

8. **Step 7**: 통계적으로 유의미 + 회귀 통과 시 default thresholds 변경 PR.
9. 본 문서를 `docs/jongbae-strategy.md` R14 본문에 통합 + 본 파일 삭제.
10. 효과 미달 시 폐기 사유 본 문서 끝에 명시 + 보존 (학습 자료).

### 10.5 운영 머신 데이터 준비 (사용자)

- `data/tick_logs/raw/*.jsonl` → `*.parquet` 변환 cron 확인 (16:00 jsonl→parquet 변환 모듈 존재 확인)
- 백업 — backtest 시 raw 데이터 손상 위험 회피
- Phase 3 종목별 운전수 가설(`memory/project_long_term_vision.md`) 작업과 데이터 공유 가능

---

## 부록 A. R14 시그널 발화 시점 — 4건 비교

```
시각        수젠텍       주성엔        현대모비스    오텍
─────────────────────────────────────────────────────────────────
09:18       시작         173,700(저점)
09:20                    +1
09:22                    Q3 STRONG?    
09:25                    현재 STRONG → 매수
09:28                    P1 BB 도달, 페널티 시작
09:30                    P1 BB 돌파 -2
09:32                    214,000(고점)
─────────────────────────────────────────────────────────────────
09:44                                                3,910(저점)
09:46                                                +1
09:48                                                Q1+Q3 STRONG
09:50                                                +1
09:52                                                현재 STRONG → 매수
09:54                                                4,160(고점)
─────────────────────────────────────────────────────────────────
09:18 ~     7,300 시작
09:29       7,160(저점)
09:38       +1 거래량 폭증
09:48       Q1+Q3 STRONG
09:50       Q3 STRONG
09:55       Q1 STRONG
10:00       현재 STRONG → 매수, P1 BB 도달
10:06       8,090(고점)
─────────────────────────────────────────────────────────────────
09:57                                  시작
10:01                                  534,000(저점)
10:08                                  Q1+Q3 STRONG
10:12                                  현재 STRONG → 매수
10:15                                  561,000(고점), P1 BB 돌파
```

## 부록 B. 변경 영향 범위

이 변경은 다음 모듈에만 영향:

- **변경**: `src/jongbae/grader.py`, `src/jongbae/grader_thresholds.py` (신규)
- **추가**: `src/jongbae/peak_signals.py` (신규, P1), `scripts/backtest_grader.py` (신규)
- **호출자 영향**: `src/dashboard/worker.py` — GraderSnapshot 신규 필드(R14e~h) 채우는 코드 추가 필요. 기존 시그니처 유지.
- **테스트**: `tests/test_grader.py` (기존 회귀 유지), `tests/test_backtest_grader.py` (신규)
- **미영향**:
  - `src/scheduler.py` 의 14:50 결정 레포트 — R4 v2 별도 룰 (jongbae 매매 후보)
  - R15 청산 트리거 (`src/jongbae/exit_triggers.py`) — 본 변경은 R14 진입만
  - M5.5 주도섹터 식별 (`src/jongbae/leading_theme.py`) — 별도 시그널
  - PWA 대시보드 (`src/dashboard/`) — 카드 표시는 동일 (점수만 변경)

## 부록 C. 사용자 매매 4건 → 가설 검증 케이스로 영속화

본 4건은 `docs/jongbae-strategy.md` "검증 가능한 사용자 발화" 표에 추가 가치:

```
| 2026-05-20 | 수젠텍 (253840) | 매수 추정 10:00 8,000원 | 정점 직후 음봉 -5.7% |
| 2026-05-20 | 주성엔 (036930) | 매수 추정 09:28 200,000원 | 정점 직후 음봉 -16.6% |
| 2026-05-20 | 현대모비스 (012330) | 매수 추정 10:12 553,000원 | 정점 직후 음봉 -2.8% |
| 2026-05-20 | 오텍 (067170) | 매수 추정 09:52 4,100원 | 정점 직후 음봉 -11.5% |
```

P0+P1 채택 후 backtest 통과 = "정점 진입 회피 룰" 검증 완료. Phase 3 종목별 운전수 가설로 진화 가능.
