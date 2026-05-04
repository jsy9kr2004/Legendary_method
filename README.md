# Trader Assistant

한국 주식 시장 매매를 도와주는 레포트 작성 프로그램.

## 개요

여러 매매 전략에 대한 의사결정 보조 레포트를 텔레그램과 이메일로 자동 발송한다. 매수/매도 실행은 모두 사람이 수동으로 한다. 프로그램의 역할은 **데이터 수집 → 분석 → 레포트 생성 → 알림 발송**까지다.

## 지원 매매 전략 (예정)

| # | 전략 | 시간 프레임 | 상태 |
|---|---|---|---|
| 1 | 주도주 매매 | 수 주 ~ 수 개월 | TODO |
| 2 | **종배 매매** | 1일 (오버나잇) | **개발 중 (v0)** |
| 3 | 스윙 매매 | 수 일 ~ 수 주 | TODO |

현재는 **종배 매매(2번)**만 구현한다. 1, 3번은 종배 모듈이 안정화된 후 같은 데이터 인프라를 재사용해서 추가한다.

## 종배 매매 (Jongbae) 모듈 — 한 줄 요약

장중 거래대금 30위 내에서 같은 테마가 3개 이상 출현하면 **주도테마**로 인식하고, 그 테마 안에서 일봉 +20% 이상 마감하는 종목을 **종배 후보**로 추적해 다음날 갭상승 차익을 노린다. 진입 1순위는 상한가 도달 순간, 2순위는 종가 매수.

자세한 매매 정의와 구현 계획은 [`docs/plan.md`](docs/plan.md)와 [`docs/jongbae-strategy.md`](docs/jongbae-strategy.md) 참고.

## 기술 스택

- **언어**: Python 3.12
- **OS**: WSL2 Ubuntu (가정 서버, RTX 2080 환경)
- **데이터**: pykrx (일봉 히스토리), KIS API (장중 실시간), SQLite (저장)
- **알림**: 텔레그램 봇 (즉시 알림), Gmail SMTP (사후 상세 레포트)
- **스케줄**: cron 또는 systemd timer

## 프로젝트 구조

```
trader-assistant/
├── README.md              # 본 파일 - 프로젝트 개요
├── CLAUDE.md              # AI 코딩 어시스턴트용 컨텍스트
├── docs/
│   ├── plan.md            # 전체 개발 계획 / 마일스톤
│   ├── jongbae-strategy.md  # 종배 전략 정의 (정량 룰)
│   ├── data-infra.md      # 데이터 인프라 설계
│   └── report-spec.md     # 레포트 포맷 명세
├── src/
│   ├── data/              # 데이터 수집/저장
│   ├── jongbae/           # 종배 전략 모듈
│   ├── report/            # 레포트 생성
│   └── notify/            # 알림 발송
├── data/
│   ├── daily/             # 일봉 데이터 (parquet)
│   ├── intraday/          # 장중 스냅샷
│   └── meta/              # 종목/테마 매핑
└── tests/
```

## 빠른 시작 (WIP)

```bash
# 환경 설정
cd ~/trader-assistant
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 환경변수 설정 (.env 파일)
cp .env.example .env
# .env 파일에 텔레그램 토큰, KIS API 키 등 입력

# 일봉 데이터 초기 적재 (5년치, 1~2시간 소요)
python -m src.data.init_daily

# 데몬 실행 (장 시작부터 마감까지 자동 동작)
python -m src.main
```

## 라이선스

개인 사용. 외부 배포 금지.

## 작성자

Zeta (jsy9kr2004)
