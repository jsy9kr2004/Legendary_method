# 1주일 Dry-Run 검증 가이드

운영 투입 전 `DRY_RUN=true` 모드로 1주일간 검증한다.  
실제 매매 X, 텔레그램 발송 X — 로그와 파일만 생성.

---

## 0. 전제 조건

```
git clone <repo> ~/Legendary_method
cd ~/Legendary_method
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # 실제 KIS API 키 / 텔레그램 토큰 입력
```

`.env` 에서 반드시 설정:

```dotenv
KIS_APP_KEY=...
KIS_APP_SECRET=...
KIS_ACCOUNT_NO=...
KIS_API_MODE=real          # 실제 API 응답 확인 (또는 mock)
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
DRY_RUN=true               # ★ Dry-Run 모드
DATA_DIR=~/Legendary_method/data
LOG_DIR=~/Legendary_method/logs
```

---

## 1. 데이터 초기 적재 (1회)

```bash
# 일봉 1년치 백필 (처음 한 번만)
python -m src.data.daily_fetcher --years 1

# 종목 마스터 갱신
python -m src.data.update_master

# 네이버 테마 크롤링
python -m src.data.update_themes
```

---

## 2. Demo 파이프라인 수동 실행

스케줄러 띄우기 전에 파이프라인이 정상 동작하는지 확인:

```bash
# 제룡전기 2025-05-04 사례로 E2E 검증 (KIS API 불필요)
python -m src.pipeline --date 2025-05-04 --demo

# 오늘 날짜, 실제 데이터 (스냅샷 없으면 경고만)
python -m src.pipeline

# 실제 데이터 + 파일 저장
python -m src.pipeline --save
```

레포트가 터미널에 출력되고 (--save 시) `data/reports/<날짜>/` 에 파일이 생기면 OK.

---

## 3. 스케줄러 foreground 실행 (하루 모니터링)

```bash
python -m src.scheduler
```

**확인 사항 (당일):**

| 시각 | 기대 로그 |
|---|---|
| 11:00 | `[스냅샷] 11:00 수집 시작` → 종목 수 30개 |
| 13:00 | `[스냅샷] 13:00 수집 시작` |
| 14:00 | `[스냅샷] 14:00 수집 시작` |
| 14:50 | `[스냅샷] 14:50 수집 시작` |
| 매 60초 | `[상한가 폴링]` (로그 없으면 정상 — 상한가 없을 때 로그 X) |
| 주말 | `스킵 — 주말/휴장일` 로그 → **발송 없음** |

DRY_RUN=true 이므로 텔레그램 발송 시 `[DRY_RUN] 텔레그램 발송 스킵` 로그가 보여야 함.

---

## 4. systemd 데몬으로 전환 (foreground 검증 후)

```bash
sudo bash deploy/install.sh <유저명>

# 상태 확인
sudo systemctl status jongbae@<유저명>
journalctl -u jongbae@<유저명> -f

# 재시작 테스트
sudo systemctl restart jongbae@<유저명>

# 부팅 후 자동 시작 확인
sudo systemctl is-enabled jongbae@<유저명>
```

---

## 5. 헬스체크 cron 등록

```bash
# crontab -e 에 추가
# 매일 09:00 KST 헬스체크 실행 (이상 시 텔레그램 발송)
0 9 * * 1-5 cd ~/Legendary_method && .venv/bin/python -m src.ops.health --send >> logs/health.log 2>&1
```

수동 테스트:

```bash
python -m src.ops.health
python -m src.ops.health --json | jq .
```

---

## 6. 1주일 체크리스트

| 일자 | 스냅샷 수집 | 헬스체크 | 레포트 생성 | 발송 로그 (DRY_RUN) |
|---|---|---|---|---|
| Day 1 (월) | ☐ | ☐ | ☐ | ☐ |
| Day 2 (화) | ☐ | ☐ | ☐ | ☐ |
| Day 3 (수) | ☐ | ☐ | ☐ | ☐ |
| Day 4 (목) | ☐ | ☐ | ☐ | ☐ |
| Day 5 (금) | ☐ | ☐ | ☐ | ☐ |
| Day 6 (토) | 스킵 확인 ☐ | ☐ | — | — |
| Day 7 (일) | 스킵 확인 ☐ | ☐ | — | — |

모든 체크박스가 채워지면 `DRY_RUN=false` 로 전환하여 실제 발송 시작.

---

## 7. 실제 발송으로 전환

```bash
# .env 수정
DRY_RUN=false

# 서비스 재시작
sudo systemctl restart jongbae@<유저명>

# 14:50에 첫 실제 텔레그램 메시지 수신 확인
```

---

## 트러블슈팅

| 증상 | 확인 항목 |
|---|---|
| 스냅샷 데이터 없음 | `KIS_API_MODE`, API 키 유효성, 장 중 시간인지 확인 |
| 텔레그램 발송 안 됨 | `DRY_RUN=false` 확인, `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` 확인 |
| 서비스 재시작 루프 | `journalctl -u jongbae@<유저명> -n 50` 로 에러 확인 |
| 디스크 경고 | `python -m src.ops.health` 로 상세 확인, logrotate 동작 점검 |
| 주말에도 폴링 | `is_business_day` 가드 미적용 — scheduler.py 버전 확인 |
