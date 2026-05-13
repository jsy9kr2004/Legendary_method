# 데이터 인프라 (data-infra.md)

## 데이터 소스 및 갱신 주기

| 데이터 | 소스 | 갱신 주기 | 비용 |
|---|---|---|---|
| 일봉 OHLCV | KIS API (M0에서 단일출처화) | 매일 16:00 | KIS 계좌 필요 |
| 종목 마스터 (코드/이름/시장구분) | KIS mst part1 | 매일 | KIS 계좌 필요 |
| **시총 / 상장일 / 액면가** (M5.5) | KIS mst part2 | 매일 | KIS 계좌 필요 |
| WICS 섹터 매핑 | wiseindex.com 크롤링 | 월 1회 | 무료 |
| 네이버 금융 테마 | 네이버 금융 크롤링 | 월 1회 (7일 신선도 체크) | 무료 |
| 장중 거래대금 순위 | KIS API `FHPST01710000` | 정기 4회 + 09:00~10:30 1~2초 | KIS 계좌 필요 |
| 장중 종목 시세 | KIS API `FHKST01010100` | 정기 4회 + 상한가 폴링 + M6 모니터링 | KIS 계좌 필요 |
| **분봉 시계열 OHLC** (M6, R12 봉 패턴 / R11 가속) | KIS API `FHKST03010200` | 모니터링 종목 1~2초 | KIS 계좌 필요 |
| **체결강도 VP** (M6, R10) | KIS API `inquire-ccnl` `체결강도` 필드 | 모니터링 종목 1~2초 | KIS 계좌 필요 |
| **호가잔량** (M6, R10 보조 강등) | KIS API `inquire-asking-price-exp-ccn` | 모니터링 종목 1~2초 | KIS 계좌 필요 |
| **투자자별 순매수** (M6) | KIS API `inquire-investor` | 모니터링 종목 1~2초 | KIS 계좌 필요 |
| **VI 발동 시각** (M6, R12.5) | KIS endpoint 미확정 — v0 분봉 ±10% 휴리스틱, v1 정밀 | 이벤트 | KIS 계좌 필요 |
| **매수가/보유 상태** (M6, R15) | 텔레그램 `/buy` 명령 → 메모리 + JSON 영속 | 명령 시점 | — |
| 시간외 단일가 | KIS API | 16:00~18:00 폴링 | KIS 계좌 필요 |
| KRX 휴장일 | weekday 기반 (v0) → 정밀 (v1 TODO) | 연 1회 | 무료 |

## API 한계 — 알아두기

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
09:00~10:30  → 모니터링 worker (M6, 평일만 자동 ON):
               주도주 + 사용자 추가 종목에 대해 1~2초 간격으로
                 - 분봉 거래대금 (FHKST03010200)
                 - 체결강도 (inquire-ccnl)
                 - 호가잔량 (inquire-asking-price-exp-ccn)
                 - 투자자별 순매수 (inquire-investor)
               수집 → editMessageText로 텔레그램 메시지 갱신.
               전체 거래대금 30위 (R3 v1: 50위) 갱신은 30~60초 주기.

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

R10~R15 지표 계산에 필요한 장중 시계열은 **메모리 deque + JSON 스냅샷**으로 운영. 영구 적재(parquet)는 v1.

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

### KIS API 호출수 영향 (R10~R15 추가)

기존 M6 표(종목당 4지표)에 변화 없음. R10 체결강도/R12 분봉 OHLC/R10 호가잔량/투자자별 순매수 모두 기존 4 fetcher 결과 재사용. 추가 호출 X.

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
| 모니터링 1초 로그 (M6, 09:00~10:30, 종목 5개 평균) | ~2MB | ~500MB |
| 레포트 마크다운 | ~30KB | ~8MB |
| 메타 데이터 | 0 (월 1회) | ~5MB |

→ M0~M5 기준 연간 약 300MB. M6 모니터링 로그 추가 시 연간 ~800MB. 5년 4GB 수준. 디스크 부담 여전히 작음. 단 1초 로그는 압축/롤오버 정책 필요.

## ETF/펀드/리츠 필터링 (M5.5)

R2 강화 — 단타 유니버스에서 다음을 제외:

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
