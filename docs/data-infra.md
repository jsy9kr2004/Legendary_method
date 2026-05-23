# 데이터 인프라 (data-infra.md)

## 데이터 소스 및 갱신 주기

| 데이터 | 소스 | 갱신 주기 | 비용 |
|---|---|---|---|
| 일봉 OHLCV | KIS API (M0에서 단일출처화) | 매일 16:00 | KIS 계좌 필요 |
| 종목 마스터 (코드/이름/시장구분) | KIS mst part1 | 매일 | KIS 계좌 필요 |
| **시총 / 상장주식수** | 거래대금 순위 스냅샷 역산 (`시총=거래대금/(회전율/100)`, 2026-05-24) | 매일 16:30 (update_master) | 무료 (KIS 스냅샷 재사용) |
| ~~시총 / 상장일 / 액면가 (M5.5)~~ | ~~KIS mst part2~~ | — | mst char[172:181] 0 파싱 결함 — 스냅샷 역산으로 대체 |
| WICS 섹터 매핑 | wiseindex.com 크롤링 | 월 1회 | 무료 |
| 네이버 금융 테마 | 네이버 금융 크롤링 | 월 1회 (7일 신선도 체크) | 무료 |
| 장중 거래대금 순위 | KIS API `FHPST01710000` | 정기 4회 + M6 `/on` 상태일 때 1~2초 | KIS 계좌 필요 |
| 장중 종목 시세 | KIS API `FHKST01010100` | 정기 4회 + 상한가 폴링 + M6 `/on` 상태 1~2초 | KIS 계좌 필요 |
| **분봉 시계열 OHLC** (M6, Buy.Candle 봉 패턴 / Buy.Accel 가속) | KIS API `FHKST03010200` | M6 `/on` 상태 모니터링 종목 1~2초 | KIS 계좌 필요 |
| **체결강도 VP** (M6, Buy.VP) | KIS API `inquire-ccnl` `체결강도` 필드 | M6 `/on` 상태 모니터링 종목 1~2초 | KIS 계좌 필요 |
| **호가잔량** (M6, Buy.VP 보조 강등) | KIS API `inquire-asking-price-exp-ccn` | M6 `/on` 상태 모니터링 종목 1~2초 | KIS 계좌 필요 |
| **투자자별 순매수** (M6) | KIS API `inquire-investor` | M6 `/on` 상태 모니터링 종목 1~2초 | KIS 계좌 필요 |
| **VI 발동 시각** (M6, Buy.Position) | KIS endpoint 미확정 — v0 분봉 ±10% 휴리스틱, v1 정밀 | 이벤트 | KIS 계좌 필요 |
| **매수가/보유 상태** (M6, Exit.Triggers) | 텔레그램 `/buy` 명령 → 메모리 + JSON 영속 | 명령 시점 | — |
| 시간외 단일가 | KIS API | 16:00~18:00 폴링 | KIS 계좌 필요 |
| KRX 휴장일 | weekday 기반 (v0) → 정밀 (v1 TODO) | 연 1회 | 무료 |

## API 한계 — 알아두기

### ★ 거래량(volume) vs 거래대금(trading_value) — 절대 헷갈리지 말 것

KIS volume-rank (`FHPST01710000`) 는 `FID_BLNG_CLS_CODE` 파라미터로 정렬축을 결정한다.

| 값 | 의미 | 종배 universe 적합성 |
|---|---|---|
| `"0"` | 평균거래량 (주) | ❌ KODEX/TIGER 인버스류가 1위 점령. 종배엔 부적합 |
| `"1"` | 거래증가율 | — |
| `"2"` | 평균거래회전율 | — |
| `"3"` | **거래금액순 (거래대금/원)** | ✅ 종배/주도섹터 universe 정답. 보통 삼성전자 1위 |
| `"4"` | 평균거래금액회전율 | — |

- **거래량 (volume)** = `acml_vol`, 단위 주. 누적 체결주식수.
- **거래대금 (trading_value)** = `acml_tr_pbmn`, 단위 원. 누적 체결금액. = price × volume 의 합.

저가주(KODEX 200선물인버스2X 등 주당 100~1,500원대) 는 거래량 96억주가 나와도 거래대금은 1,000억 수준. 삼성전자 (주당 27만원) 는 거래량 2,500만주만 돼도 거래대금 6.8조원. **종배는 자금 쏠림 = 거래대금 기준**. 거래량으로 잡으면 universe 가 ETF/저가주로 무너진다.

코드 진입점: `src/data/intraday._VOLUME_RANK_BLNG_CLS_TRADING_VALUE = "3"` 상수 박혀 있음. 회귀 테스트 `tests/test_intraday.test_fetch_volume_rank_sends_trading_value_sort_axis` 가 누가 임의로 `"0"` 으로 바꾸면 즉시 실패하도록 검증.

**사고 이력 (2026-05-19 round 41 후속 2):** `"0"` 으로 잘못 박혀 있어 5/12~5/18 5일 연속 종배 후보 0종목. universe 1~5위가 KODEX 인버스류로 도배되고 삼성전자가 15위까지 밀림. round 41 의 Eod.Pick v2 backtest 결과 5일 17종목도 모두 거래량 universe 기준이라 무효화 → 거래대금 universe 로 재실행 필요 (plan.md 기술 부채).

### KIS volume-rank 30개 상한 — 가격 분할로 우회 (round 41 후속 2 후속)

이 endpoint 는 한 호출당 30개 반환 상한. 2026-05-19 진단 (`scripts/diag_volume_rank.py --plan-b`) 결과:

- ❌ **페이지네이션**: 응답에 `ctx_area_fk100/nk100` 없음, header `tr_cont=None`. ctx 동봉 호출도 같은 1~30위 반복.
- ❌ **시장 분리**: `FID_COND_MRKT_DIV_CODE` 를 "J" 외 "0"/"1"/"K"/"Q"/"00"/"01" 모두 시도 — 전부 `ERROR INVALID FID_COND_MRKT_DI`.
- ✅ **가격 범위 분할**: `FID_INPUT_PRICE_1/_2` 가 실제 가격 필터로 작동. 3회 호출 합집합 90 고유 종목 → 거래대금 desc top 50 = 1위 삼성전자(8.4조) ~ 50위 대우건설(2,195억) 완벽 cover.

코드 구현: `src/data/intraday.fetch_volume_rank` 가 `top_n > 30` 일 때 자동으로 가격 버킷 모드로 전환.

```python
_PRICE_BUCKETS: list[tuple[int, int]] = [
    (0, 10_000),         # 저가: KODEX 인버스류 + 저가 단타주
    (10_001, 100_000),   # 중가: 일반 단타주 + 일부 ETF
    (100_001, 9_999_999),# 고가: 삼성전자/SK하이닉스 + 대형주
]
```

호출 모드:
- `top_n ≤ 30`: 단일 호출. KIS data_rank 그대로 (HTS 거래대금 순위 1:1).
- `top_n > 30`: 3회 호출 → 합집합 (중복 시 trading_value 큰 쪽 채택) → trading_value desc 정렬 → top_n 컷 → rank 글로벌 재부여 (1..top_n).

운영 영향:
- 14:50 cron / M6 funnel tick: KIS 호출 1회 → **3회**. duals key rate limit ~40 req/s 한도 안. round 40 `parallel_fetch` ThreadPool 안에서 동시 호출 가능.
- 가격 버킷 중 일부 실패 (HTTP 5xx) 시 살아남은 버킷 결과로 부분 응답 — 호출부가 `len(df)` 로 정상/부분/실패 구분.
- 버킷 모드의 rank 는 KIS 가 매긴 절대 순위가 아닌 union 정렬 추정치. 정확한 시장 순위는 KIS 가 1회 30개만 제공하는 한 v0 한계.

회귀 테스트: `tests/test_intraday.test_fetch_volume_rank_price_bucket_mode_when_top_n_over_30` + 부분 실패 / 중복 제거 / master 필터 / 단일 호출 모드 4건.

### 분봉 히스토리는 사실상 불가

- **키움 OpenAPI+**: 분봉 1년치만 제공, 특정 기간 명시 지정 불가
- **대신 CYBOS+**: 1분봉 약 2년치 (Windows 환경 한정)
- **KIS API**: 분봉은 짧음, 초당 20회 호출 제한
- **pykrx**: 일봉만 (분봉 X)

→ **결론:** v0 백테스트는 포기. 매일 데이터 적재하면서 6개월~1년 후 미니 백테스트.

### 장중 데이터는 매일 새로 모아야 함

거래대금 순위 historical은 어디서도 제공하지 않는다. 따라서 매일 정해진 시점에 스냅샷을 찍어 누적해야 한다.

## 저장 구조

### Phase 1 (단순): 파일 기반

```
data/
├── daily/
│   └── ohlcv.parquet                # 전종목 일봉 (long format)
├── intraday/
│   └── snapshots/
│       └── 2025-05-04/
│           ├── 11_00.parquet        # 11시 거래대금 30위 + 시세
│           ├── 13_00.parquet
│           ├── 14_00.parquet
│           └── 14_50.parquet
├── meta/
│   ├── stocks.parquet               # 종목 마스터
│   ├── wics_sectors.parquet         # WICS 분류
│   └── naver_themes.parquet         # 네이버 테마 (다중)
└── reports/
    └── 2025-05-04/
        ├── 09_30_morning.md
        ├── 14_50_decision.md
        └── ...
```

### Phase 2 (확장): SQLite

데이터 누적되어 100MB 이상 되면 SQLite로 마이그레이션. 인덱스 + 조인 효율.

```sql
CREATE TABLE daily_ohlcv (
    code TEXT,
    date DATE,
    open INTEGER,
    high INTEGER,
    low INTEGER,
    close INTEGER,
    volume BIGINT,
    trading_value BIGINT,
    PRIMARY KEY (code, date)
);
CREATE INDEX idx_daily_date ON daily_ohlcv(date);

CREATE TABLE intraday_snapshot (
    snapshot_time TIMESTAMP,
    rank INTEGER,
    code TEXT,
    name TEXT,
    price INTEGER,
    daily_return REAL,
    intraday_high INTEGER,
    cumulative_volume BIGINT,
    cumulative_value BIGINT,
    PRIMARY KEY (snapshot_time, code)
);

CREATE TABLE stock_themes (
    code TEXT,
    theme TEXT,
    source TEXT,  -- 'naver' or 'wics'
    crawled_at DATE,
    PRIMARY KEY (code, theme, source)
);
```

## 데이터 수집 워크플로우

### 일일 (장 마감 후)

```
16:00 → pykrx로 오늘 일봉 OHLCV 추가
16:30 → 종목 마스터 (시총, 상장일 등) 업데이트
17:00 → 시간외 단일가 데이터 수집 (다음날 갭 예측 시그널)
```

### 장중

```
/on ~ /off   → 모니터링 worker (M6, 24h 사용자 토글):
               평일 09:00 자동 ON. /off 로만 종료 — 10:30 자동 OFF 폐지
               (round 18). 사용자가 임의 시점에 /on/off 가능.
               주도주 + 사용자 추가 종목에 대해 1~2초 간격으로
                 - 분봉 거래대금 (FHKST03010200)
                 - 체결강도 (inquire-ccnl)
                 - 호가잔량 (inquire-asking-price-exp-ccn)
                 - 투자자별 순매수 (inquire-investor)
               수집 → editMessageText로 텔레그램 메시지 갱신.
               전체 거래대금 30위 (Theme v1: 50위) 갱신은 30~60초 주기.
               봇 명령 polling thread 는 데몬 시작 시 1회 띄워 24h 상시.
               휴장일/주말 /on 도 허용되나 KIS 시세는 변동 없음 → 카드 정적.

11:00         → 거래대금 50위 + 각 종목 시세 → 스냅샷 (정기 1차)
13:00         → 동일 (정기 2차)
14:00         → 동일 (정기 3차)
14:50         → 동일 + 결정 레포트 (★ 가장 중요)

장중 상시     → 주도테마 후보 종목 폴링 (1~5분 간격)
                상한가 진입 감지 시 즉시 알림 트리거
```

### KIS API 호출수 예산 (M6 운영 시)

KIS rate limit: real **초당 20콜**. 모니터링 worker 호출수:

| 동시 모니터링 종목 수 | 갱신 간격 | 종목당 4지표 | 초당 호출수 | 한계 대비 |
|---|---|---|---|---|
| 1~2 | 2초 | 4 | 4~8 | 여유 |
| 3~5 | 3초 | 4 | 4~7 | 여유 |
| 6~10 | 5초 | 4 | 5~8 | 여유 |
| 11+ | (거부) | — | — | 보호 |

→ **종목 10개 이내** 운영 정책으로 rate limit 보호. 거래대금 50위 갱신과 충돌 없도록 worker 분리.

### 월 1회 (테마/섹터 갱신)

```
매월 1일 → wiseindex 크롤링: WICS 중분류 매핑 갱신
매월 1일 → 네이버 금융 크롤링: 테마별 종목 갱신
```

## KIS API 사용 가이드

### 인증

- App Key / App Secret 발급 (한국투자증권 OpenAPI 포털)
- Access Token: 24시간 만료 → 자동 갱신 로직 필요
- 환경변수로 보관 (`.env`)

### Rate Limit 대응

- **초당 최대 20회** 호출 제한
- 토큰 버킷 또는 leaky bucket 패턴
- 지연 시간 = `(필요 호출 수 ÷ 20)` 초 분산

```python
class KISRateLimiter:
    def __init__(self, calls_per_sec=20):
        self.interval = 1.0 / calls_per_sec  # 0.05초
        self.last_call = 0
    
    def wait(self):
        elapsed = time.time() - self.last_call
        if elapsed < self.interval:
            time.sleep(self.interval - elapsed)
        self.last_call = time.time()
```

### 주요 엔드포인트

| 용도 | TR ID / endpoint | 비고 |
|---|---|---|
| 거래대금 상위 | FHPST01710000 | 30~50위 한 번에 |
| 주식 현재가 | FHKST01010100 | 종목별 |
| 일봉 시세 | FHKST03010100 | 일봉 적재 (M0 단일출처) |
| **분봉 시세** (M6) | FHKST03010200 | 종목별 1/3/5/10/15/30/60분봉 |
| **체결강도** (M6) | `inquire-ccnl` | 매수/매도 체결 비율 |
| **호가잔량** (M6) | `inquire-asking-price-exp-ccn` | 매수/매도 10단계 호가 잔량 |
| **투자자별 순매수** (M6) | `inquire-investor` | 외국인/기관/프로그램 순매수 |
| 종목 마스터 (mst) | mst zip download | part1 기본정보 + part2 시총/상장일 |

## pykrx 사용 가이드

### 설치

```bash
pip install pykrx
```

### 주요 함수

```python
from pykrx import stock
from datetime import datetime

# 1. 종목 코드 리스트
kospi_tickers = stock.get_market_ticker_list("20250504", market="KOSPI")
kosdaq_tickers = stock.get_market_ticker_list("20250504", market="KOSDAQ")

# 2. 종목명
name = stock.get_market_ticker_name("005930")  # '삼성전자'

# 3. 일봉 OHLCV
df = stock.get_market_ohlcv_by_date("20240101", "20250504", "005930")

# 4. 전 종목 일봉 (특정 날짜)
df = stock.get_market_ohlcv_by_ticker("20250504", market="KOSPI")

# 5. 휴장일 체크 (영업일 캘린더)
business_days = stock.get_previous_business_days(year=2025, month=5)

# 6. 시총 (랭킹용)
df = stock.get_market_cap("20250504")
```

### 주의

- 네이버 크롤링 기반이라 호출 빈도 너무 빠르면 차단
- 권장: 1초당 1회 이하 (전종목 받을 땐 시간 걸림)
- 수정주가 옵션: `adjusted=True` (기본값)

## 네이버 금융 테마 크롤링

### URL 패턴

```
https://finance.naver.com/sise/theme.naver?&page=1
→ 전체 테마 리스트

https://finance.naver.com/sise/sise_group_detail.naver?type=theme&no={theme_id}
→ 특정 테마의 구성 종목
```

### 크롤링 주의

- robots.txt 준수
- User-Agent 헤더 명시
- 1초 이상 간격
- 월 1회만 갱신하면 부담 적음

### 결과 형식

```python
# 종목 → 테마 (다중)
{
    "075180": ["전기/전선", "원자력", "데이터센터"],   # 제룡전기
    "005930": ["반도체", "AI 칩", "삼성그룹"],          # 삼성전자
    ...
}
```

## WICS 섹터 크롤링

### 출처

```
https://www.wiseindex.com/Index/IndexList?ftype=WICS
```

### 분류 체계

- 대분류 (10개): 에너지, 소재, 산업재, 자유소비재, 필수소비재, 건강관리, 금융, IT, 통신서비스, 유틸리티
- 중분류 (24개): 본 프로젝트 사용
- 소분류

## 장중 메모리 시계열 (M6 매수 점수/매도 트리거용)

Buy.VP~Exit.Triggers 지표 계산에 필요한 장중 시계열은 **메모리 deque + JSON 스냅샷**으로 운영. 영구 적재(parquet)는 v1.

### 메모리 캐시 (worker process)

```
intraday_series[code] = {
    'vp':         deque(maxlen=1200)   # (timestamp, vp) — 20분 1초 단위
    'vol_1m':     deque(maxlen=30)     # (timestamp, value)  — 30분
    'vol_5m':     deque(maxlen=12)     # (timestamp, value)  — 60분
    'minute_ohlc':deque(maxlen=20)     # (timestamp, o,h,l,c) — 5분봉 직전 100분
    'price':      deque(maxlen=600)    # (timestamp, price) — 10분 1초 단위
    'high_since_entry': float          # 보유 모드 전용
    'vi_triggered_at':  datetime|None
}
```

→ 종목 10개 × 평균 항목 1KB ≈ 10KB 상주. RSS 부담 없음.

### 보유 상태 영속화 (재시작 대비)

```
data/state/holdings.json
{
  "091340": {
    "entry_price": 91300,
    "entry_time":  "2026-05-13T13:42:11+09:00",
    "entry_bar_low": 90800,
    "time_stop_minutes": 10,
    "triggers_fired": ["B1"]   # 멱등성: 익절 1차는 1회만
  }
}
```

- `/buy` / `/sell` 시 atomic write (tmp file + rename)
- worker 재시작 시 load → 메모리 복원. 시계열은 비어 있음 → 5MA/20MA는 워밍업 후 사용

### KIS API 호출수 영향 (Buy.VP~Exit.Triggers 추가)

기존 M6 표(종목당 4지표)에 변화 없음. Buy.VP 체결강도/Buy.Candle 분봉 OHLC/Buy.VP 호가잔량/투자자별 순매수 모두 기존 4 fetcher 결과 재사용. 추가 호출 X.

### 투자자별 순매수 Buy.Score 추가 가능성 (round 29, P3-1 조사)

**현황**:
- 코드 `src/data/intraday_realtime.py:fetch_investor_flow` 이미 구현 (KIS `inquire-investor`, TR `FHKST01010900`).
- 응답 필드: `foreign_net_buy`(외인), `institution_net_buy`(기관), `program_net_buy`, 거래대금 단위도 별도 제공.
- 호출 비용: 0 (M6 4 fetcher 중 1개로 이미 종목당 1회/tick 호출 중).

**제약 (round 22 정정 이력)**:
- 사용자 정정: "외국인/기관/프로그램 수치는 KIS 응답 신뢰도 낮음 (데이터 검증 안 됨)". 모니터링 카드에서 라인 제거됨.
- 의심 사유: ①KIS 장중 추정치는 거래원 20여 개 합산이라 외국계 창구 누락/오집계 가능 (출처: KIS API 도움말). ②일자별 KRX 공시(t+1) 와 실시간 추정값 간 괴리 보고된 사례 존재.

**Buy.Score 가산 도입 위험 분석**:
- 통설(한국경제, 키움 거래원 분석): "외국인 + 기관 동반 순매수 = 강한 매수 시그널". 그러나 이는 **종가 기준 일자별** 통설이며, 장중 1~2초 추정치 적용은 검증 필요.
- 시간대 종속성: 9:00~9:30 누적 100주는 의미 없을 수 있음 (워밍업), 14:00 누적 100만주는 결정적. 가중치 단일화 어려움.
- 함정: 세력 창구 분산 시 외인 창구로도 매도 위장 가능 (통설 함정).

**P3-1 결론**:
- ✅ **API 가용성 자체는 확보** — fetch_investor_flow 그대로 사용 가능, 추가 호출 비용 0.
- ⚠️ **Buy.Score 가산 도입 전 검증 작업 필수**:
  - (1) 종목 3~5개 × 5거래일 동안 fetch_investor_flow 1분 단위 로그 수집
  - (2) 익일 KRX 공시 (`finance.daum.net/domestic/influential_investors`) 일자별 외인/기관 합과 fetch 값 비교
  - (3) 괴리 ±10% 이내면 R14e 분기로 채택: 외인 + 기관 동시 양수 + 누적 거래대금 ≥ 임계 → +0.5
  - (4) 괴리 ±10% 초과면 v1 연기 (분봉 히스토리 누적과 같이)
- 🚫 **현 단계 Buy.Score 가산 도입 X** — 검증 데이터 없이 가중치 추가는 CLAUDE.md "검증 안 된 자작 가중합 X" 원칙 위반.

**대안 가벼운 적용 (v0)**:
- 카드 표시는 회복 X (round 22 정정 유지).
- 단, **Buy.Score 점수에 영향 주지 않는 "참고용 메타 라인"** 으로 fetch_investor_flow 결과를 reasons 외 별도 필드 (`ScoreCard.notes` 등) 로 노출 — 사용자가 카드에서 컨텍스트 정보로만 활용. 점수에 영향 X 라 신뢰도 검증 부담 없음.
- 이 변경은 P3-1 범위 외, 추후 사용자 동의 시 별도 라운드.

---

## 데이터 무결성 체크

매일 적재 후 자동 검증:

- [ ] 오늘자 일봉 데이터 종목 수 ≥ 어제 데이터의 95% (대량 누락 감지)
- [ ] 가격 이상치 (전일 대비 ±50% 초과는 경고)
- [ ] 시총 0인 종목 (관리종목/거래정지 가능성)
- [ ] 휴장일에 데이터 들어왔는지 체크 (있으면 버그)

검증 실패 시 텔레그램 에러 알림.

## 백업 전략

### 일일

- parquet 파일들을 매일 16:30에 외장 디스크 복사 (rsync)

### 주간

- 전체 데이터 디렉토리 압축 → Google Drive 업로드 (gdrive CLI)
- 최근 4주만 유지

### 코드

- Git push (개인 GitHub repo)
- API 키 등 민감 정보는 `.gitignore`

## 디스크 사용량 예상

| 항목 | 일일 추가 | 연간 누적 |
|---|---|---|
| 일봉 OHLCV (전종목) | ~1MB | ~250MB |
| 장중 스냅샷 (4회) | ~50KB | ~12MB |
| 모니터링 1초 로그 (M6 `/on` 상태, 일 평균 90분 가동 기준, 종목 5개 평균) | ~2MB | ~500MB |
| 레포트 마크다운 | ~30KB | ~8MB |
| 메타 데이터 | 0 (월 1회) | ~5MB |

→ M0~M5 기준 연간 약 300MB. M6 모니터링 로그 추가 시 연간 ~800MB. 5년 4GB 수준. 디스크 부담 여전히 작음. 단 1초 로그는 압축/롤오버 정책 필요.

## ETF/펀드/리츠 필터링 (M5.5)

Universe 강화 — 단타 유니버스에서 다음을 제외:

```
1. 코드 패턴 차단:
   - 1XXXXX  → 펀드/리츠 다수
   - 5XXXXX  → 스팩
   - 9XXXXX  → 일부 ETF/ETN
2. 종목명 prefix 차단:
   KODEX, TIGER, KBSTAR, ARIRANG, KINDEX, HANARO, RISE,
   ACE, SOL, WOORI, PLUS, KOSEF, ITF, SMART, FOCUS, PARAMOUNT,
   TIMEFOLIO, TREX, TRUSTON, MASTER, BNK, HK, MAESTRO, KOACT,
   FREEDOM, MIRAE, NH-Amundi, 신한, 흥국, 한국투자
3. KIS 종목분류 코드 활용 (mst part1 그룹코드):
   'EF' (ETF), 'EN' (ETN), 'EW' (ELW), 'RT' (REIT) 등 차단
4. 기존 보통주 'S' prefix 필터 + 위 컷의 AND 조합
```

→ `src/data/master.py` `is_tradable_for_jongbae(code, name, group_code) -> bool` 단일 진입점.
