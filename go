#!/usr/bin/env bash
# go — Legendary Method 간편 실행 스크립트
#
# 사용법:
#   ./go              도움말
#   ./go start        venv 세팅 + 일봉 incremental + 스케줄러 백그라운드 실행 (watchdog 포함)
#   ./go stop         백그라운드 스케줄러 + watchdog 종료
#   ./go status       실행 상태 확인
#   ./go tel          텔레그램 연결 테스트
#   ./go update       일봉 incremental (어제까지)
#   ./go init         5년치 일봉 backfill (이미 있는 건 자동 skip)
#   ./go setup        venv 생성 + 의존성 설치
#   ./go logs         최근 로그 출력
#   ./go test         pytest 실행
#   ./go decision-rerun [--date YYYY-MM-DD] [--snapshot 14:50]
#                     저장된 스냅샷으로 결정 레포트 재발송 (DRY_RUN=1 로 preview)

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

VENV="$ROOT/venv"
PY="$VENV/bin/python"
PIP="$VENV/bin/pip"
LOG_DIR="$ROOT/logs"
PID_FILE="$ROOT/.go.pid"
CHILD_PID_FILE="$ROOT/.go.child.pid"
WATCHDOG_LOG="$LOG_DIR/watchdog.log"
DEMO_PID_FILE="$ROOT/.go.pwa-demo.pid"
DEMO_LOG="$LOG_DIR/pwa-demo.log"

# ──────────────────── .env 헬퍼 (PWA 상태 표시용) ────────────────────

# .env 에서 키 값을 추출. 없으면 빈 문자열.
env_get() {
    local key="$1"
    [[ -f "$ROOT/.env" ]] || { echo ""; return; }
    grep -E "^${key}=" "$ROOT/.env" 2>/dev/null | tail -1 | cut -d= -f2- \
        | sed 's/^[[:space:]]*//; s/[[:space:]]*$//; s/^"//; s/"$//'
}

pwa_enabled() {
    local v
    v="$(env_get DASHBOARD_PWA_ENABLED | tr '[:upper:]' '[:lower:]')"
    [[ "$v" == "1" || "$v" == "true" || "$v" == "yes" ]]
}

pwa_url() {
    local host port
    host="$(env_get DASHBOARD_PWA_HOST)"; host="${host:-127.0.0.1}"
    port="$(env_get DASHBOARD_PWA_PORT)"; port="${port:-8000}"
    # bind=0.0.0.0 이면 로컬 curl 은 127.0.0.1 이 자연스러움
    [[ "$host" == "0.0.0.0" ]] && host="127.0.0.1"
    echo "http://${host}:${port}"
}

# ──────────────────── venv / 의존성 ────────────────────

ensure_venv() {
    if [[ ! -x "$PY" ]]; then
        echo "[go] venv 없음 → 생성 ($VENV)"
        python3 -m venv "$VENV"
    fi
    # editable install 여부로 의존성 설치 판단 (pyproject.toml 기반)
    if ! "$PIP" show legendary-method >/dev/null 2>&1; then
        echo "[go] 의존성 설치 (editable, 최초 1회)"
        "$PIP" install --quiet --upgrade pip
        "$PIP" install --quiet -e .
    fi
}

# ──────────────────── 헬퍼 ────────────────────

is_alive() {
    local pid_file="$1"
    [[ -f "$pid_file" ]] || return 1
    local pid
    pid="$(cat "$pid_file" 2>/dev/null || true)"
    [[ -n "$pid" ]] || return 1
    kill -0 "$pid" 2>/dev/null
}

# ──────────────────── 서브커맨드 ────────────────────

cmd_setup() {
    ensure_venv
    echo "[go] setup 완료. python=$PY"
}

cmd_init() {
    ensure_venv
    echo "[go] 5년치 일봉 backfill (이미 적재된 종목은 자동 skip)"
    "$PY" -m src.data.init_daily --years 5 "$@"
}

cmd_update() {
    ensure_venv
    echo "[go] 일봉 incremental update"
    "$PY" -m src.data.incremental_daily "$@"
}

cmd_init_index() {
    ensure_venv
    echo "[go] KOSPI/KOSDAQ 일봉 3년치 백필 (historical layer3_strong_mkt 매칭용)"
    "$PY" -m src.data.update_index --init --years 3 "$@"
}

cmd_update_index() {
    ensure_venv
    echo "[go] KOSPI/KOSDAQ 일봉 incremental"
    "$PY" -m src.data.update_index "$@"
}

cmd_tel() {
    ensure_venv
    "$PY" -m src.notify.test_send "$@"
}

cmd_decision_rerun() {
    ensure_venv
    "$PY" -m src.rerun_decision "$@"
}

cmd_test() {
    ensure_venv
    "$PY" -m pytest "$@"
}

cmd_status() {
    if is_alive "$PID_FILE"; then
        local wpid cpid
        wpid="$(cat "$PID_FILE")"
        cpid="$(cat "$CHILD_PID_FILE" 2>/dev/null || echo '?')"
        echo "[go] running. watchdog PID=$wpid, scheduler PID=$cpid"
        if pwa_enabled; then
            local url health
            url="$(pwa_url)"
            health="$(curl -fsS --max-time 2 "$url/api/health" 2>/dev/null || echo '')"
            if [[ -n "$health" ]]; then
                echo "[go] PWA  ENABLED → $url/  (health: $health)"
            else
                echo "[go] PWA  ENABLED → $url/  (health 응답 없음 — 워밍업 중일 수 있음)"
            fi
        else
            echo "[go] PWA  DISABLED  (.env 의 DASHBOARD_PWA_ENABLED=1 로 켜기)"
        fi
        if is_alive "$DEMO_PID_FILE"; then
            echo "[go] DEMO 별도 실행 중 (PID=$(cat "$DEMO_PID_FILE")) — ./go demo stop 으로 종료"
        fi
        if [[ -f "$WATCHDOG_LOG" ]]; then
            echo "--- 최근 watchdog 로그 ---"
            tail -n 5 "$WATCHDOG_LOG"
        fi
        return 0
    fi
    echo "[go] not running"
    if is_alive "$DEMO_PID_FILE"; then
        echo "[go] DEMO 만 실행 중 (PID=$(cat "$DEMO_PID_FILE")) — ./go demo stop 으로 종료"
    fi
    return 1
}

cmd_stop() {
    local stopped_any=0
    if is_alive "$PID_FILE"; then
        local wpid cpid
        wpid="$(cat "$PID_FILE")"
        cpid="$(cat "$CHILD_PID_FILE" 2>/dev/null || true)"
        echo "[go] stopping watchdog PID=$wpid (scheduler PID=${cpid:-?})"
        # scheduler 의 SIGTERM 핸들러가 PWA uvicorn daemon thread 도 함께 정리.
        kill -TERM "$wpid" 2>/dev/null || true
        [[ -n "$cpid" ]] && kill -TERM "$cpid" 2>/dev/null || true
        local i
        for i in $(seq 1 10); do
            is_alive "$PID_FILE" || break
            sleep 1
        done
        if is_alive "$PID_FILE"; then
            echo "[go] grace 만료, SIGKILL"
            kill -9 "$wpid" 2>/dev/null || true
            [[ -n "$cpid" ]] && kill -9 "$cpid" 2>/dev/null || true
        fi
        rm -f "$PID_FILE" "$CHILD_PID_FILE"
        echo "[go] stopped"
        stopped_any=1
    fi
    if is_alive "$DEMO_PID_FILE"; then
        echo "[go] PWA 데모도 함께 종료"
        cmd_demo_stop
        stopped_any=1
    fi
    if (( stopped_any == 0 )); then
        echo "[go] not running"
        rm -f "$PID_FILE" "$CHILD_PID_FILE" "$DEMO_PID_FILE"
    fi
}

# 분리된 셸에서 setsid + nohup으로 실행됨. 직접 호출 X.
run_watchdog() {
    mkdir -p "$LOG_DIR"
    echo $$ > "$PID_FILE"

    # 종료 신호 받으면 자식 죽이고 PID 파일 정리
    trap '
        cpid=$(cat "'"$CHILD_PID_FILE"'" 2>/dev/null || true)
        [[ -n "$cpid" ]] && kill -TERM "$cpid" 2>/dev/null || true
        sleep 2
        [[ -n "$cpid" ]] && kill -9 "$cpid" 2>/dev/null || true
        rm -f "'"$PID_FILE"'" "'"$CHILD_PID_FILE"'"
        exit 0
    ' TERM INT HUP

    local crashes=0
    local window_start
    window_start="$(date +%s)"

    while :; do
        echo "[$(date '+%F %T')] starting scheduler" >> "$WATCHDOG_LOG"
        "$PY" -m src.scheduler >> "$WATCHDOG_LOG" 2>&1 &
        local cpid=$!
        echo "$cpid" > "$CHILD_PID_FILE"

        # 자식 종료 대기 (TERM 받으면 wait가 즉시 리턴해서 trap이 처리)
        wait "$cpid" || true
        local rc=$?
        rm -f "$CHILD_PID_FILE"
        echo "[$(date '+%F %T')] scheduler exit=$rc, restarting in 5s" >> "$WATCHDOG_LOG"

        # 크래시 루프 가드: 10분 안에 5회 초과 시 중단 (systemd 정책과 동일)
        local now
        now="$(date +%s)"
        if (( now - window_start > 600 )); then
            crashes=0
            window_start="$now"
        fi
        crashes=$(( crashes + 1 ))
        if (( crashes > 5 )); then
            echo "[$(date '+%F %T')] crash loop (>5 in 10min), giving up" >> "$WATCHDOG_LOG"
            break
        fi

        sleep 5
    done
    rm -f "$PID_FILE" "$CHILD_PID_FILE"
}

cmd_start() {
    ensure_venv
    if is_alive "$PID_FILE"; then
        echo "[go] already running (watchdog PID=$(cat "$PID_FILE"))"
        return 0
    fi
    # stale PID 파일 정리
    rm -f "$PID_FILE" "$CHILD_PID_FILE"

    mkdir -p "$LOG_DIR"

    # 일봉 incremental (실패해도 스케줄러는 띄움)
    echo "[go] 일봉 incremental update (어제까지)"
    "$PY" -m src.data.incremental_daily || \
        echo "[go] WARN: incremental 실패. 그래도 scheduler 시작" >&2

    echo "[go] starting watchdog → $WATCHDOG_LOG"
    # setsid: 새 세션 → 부모 셸 종료에도 살아남음
    # nohup: SIGHUP 무시
    # </dev/null, >/dev/null: 셸 fd 분리
    setsid nohup bash "$ROOT/go" __watchdog__ </dev/null >/dev/null 2>&1 &
    disown $! 2>/dev/null || true

    # watchdog가 PID 파일 쓸 때까지 잠깐 대기
    local i
    for i in 1 2 3 4 5; do
        is_alive "$PID_FILE" && break
        sleep 1
    done

    if is_alive "$PID_FILE"; then
        echo "[go] running. watchdog PID=$(cat "$PID_FILE")"
        if pwa_enabled; then
            echo "[go] PWA 대시보드 → $(pwa_url)/  (텔레그램과 동일 데이터, 카드 그리드)"
        else
            echo "[go] PWA 대시보드 OFF — .env 에 DASHBOARD_PWA_ENABLED=1 추가 후 재시작 시 자동 기동"
        fi
        echo "[go] 상태: ./go status   |   로그: ./go logs   |   중지: ./go stop"
    else
        echo "[go] FAILED to start. $WATCHDOG_LOG 확인" >&2
        return 1
    fi
}

# ──────────────────── PWA 데모 (검증용) ────────────────────

cmd_demo_start() {
    ensure_venv
    if is_alive "$DEMO_PID_FILE"; then
        echo "[go] 데모 이미 실행 중 (PID=$(cat "$DEMO_PID_FILE"))"
        return 0
    fi
    mkdir -p "$LOG_DIR"
    echo "[go] 데모 시작 (mock 데이터 — KIS/텔레그램 무관) → $DEMO_LOG"
    setsid nohup "$PY" -m src.dashboard.serve_demo </dev/null >"$DEMO_LOG" 2>&1 &
    local pid=$!
    echo "$pid" > "$DEMO_PID_FILE"
    disown $pid 2>/dev/null || true
    local i
    for i in 1 2 3 4 5; do
        if curl -fsS --max-time 1 "http://127.0.0.1:8000/api/health" >/dev/null 2>&1; then
            echo "[go] 데모 → http://127.0.0.1:8000/  PID=$pid"
            echo "[go] 중지: ./go demo stop   로그: tail -f $DEMO_LOG"
            return 0
        fi
        sleep 1
    done
    echo "[go] 데모 부팅 실패. $DEMO_LOG 확인" >&2
    return 1
}

cmd_demo_stop() {
    if ! is_alive "$DEMO_PID_FILE"; then
        echo "[go] 데모 실행 중 아님"
        rm -f "$DEMO_PID_FILE"
        return 0
    fi
    local pid
    pid="$(cat "$DEMO_PID_FILE")"
    echo "[go] 데모 종료 PID=$pid"
    kill -TERM "$pid" 2>/dev/null || true
    local i
    for i in 1 2 3 4 5; do
        is_alive "$DEMO_PID_FILE" || break
        sleep 1
    done
    if is_alive "$DEMO_PID_FILE"; then
        kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$DEMO_PID_FILE"
    echo "[go] 종료됨"
}

cmd_demo() {
    local sub="${1:-help}"
    shift || true
    case "$sub" in
        start)          cmd_demo_start ;;
        stop)           cmd_demo_stop ;;
        help|-h|--help)
            cat <<'EOF'
./go demo start    PWA 데모 시작 (mock 데이터 — KIS/텔레그램 무관)
./go demo stop     PWA 데모 종료
EOF
            ;;
        *)
            echo "[go] unknown: ./go demo $sub" >&2
            echo "    사용법: ./go demo start | stop" >&2
            return 2
            ;;
    esac
}

cmd_logs() {
    mkdir -p "$LOG_DIR"
    if [[ -f "$WATCHDOG_LOG" ]]; then
        echo "=== watchdog.log (마지막 20줄) ==="
        tail -n 20 "$WATCHDOG_LOG"
        echo
    fi
    local today_log="$LOG_DIR/trader-$(date +%F).log"
    if [[ -f "$today_log" ]]; then
        echo "=== $(basename "$today_log") (마지막 40줄) ==="
        tail -n 40 "$today_log"
        echo
        echo "(실시간 follow: tail -f $today_log)"
    fi
}

cmd_help() {
    cat <<'EOF'
Legendary Method — 간편 실행 스크립트

사용법:
  ./go <command> [args...]

명령:
  start     setup + 일봉 incremental + 스케줄러 백그라운드 실행 (watchdog + PWA)
  stop      백그라운드 스케줄러 + watchdog + PWA 일괄 종료
  status    실행 상태 확인 (PWA 헬스체크 포함)
  logs      최근 로그 출력 (watchdog + 오늘자 trader)

  tel         텔레그램 연결 테스트  (예: ./go tel "안녕")
  update      일봉 incremental만 (어제까지)
  init        5년치 일봉 backfill (이미 있는 건 자동 skip)
  init-index  KOSPI/KOSDAQ 3년치 백필 (시장 국면 매칭용, 1회)
  update-index KOSPI/KOSDAQ incremental
  setup       venv 생성 + 의존성 설치 (idempotent)
  test        pytest 실행
  decision-rerun [--date YYYY-MM-DD] [--snapshot 14:50]
              저장된 스냅샷으로 결정 레포트 재생성 + 텔레그램 재발송.
              cron 시점 이후 코드 fix 를 오늘 데이터에 적용할 때.
              preview 만: DRY_RUN=1 ./go decision-rerun

  demo start     PWA UI 검증용 mock 서버 (KIS/텔레그램 무관, 백그라운드)
  demo stop      PWA 데모 종료

처음 실행:
  cp .env.example .env       # 토큰 + DASHBOARD_PWA_ENABLED=1 등 입력
  ./go tel                   # 텔레그램 연결 확인
  ./go init                  # (선택) 5년치 종목 일봉 백필 — 시간 걸림
  ./go init-index            # (선택) 지수 3년치 백필 — historical 시장 국면 매칭
  ./go start                 # 스케줄러 + PWA 데몬 시작

PWA 대시보드:
  .env 에 다음 3 줄 추가 시 ./go start 가 PWA 도 같이 띄움:
    DASHBOARD_PWA_ENABLED=1
    DASHBOARD_PWA_HOST=127.0.0.1      # Tailscale 시 0.0.0.0 또는 100.x.x.x
    DASHBOARD_PWA_PORT=8000

  검증 (KIS/텔레그램 안 만지고 UI 만 보기):
    ./go demo start           # 백그라운드 mock 서버 시작
    ./go demo stop            # 종료

watchdog 정책:
  - scheduler가 죽으면 5초 후 자동 재시작 (PWA daemon thread 도 함께 재기동)
  - 10분 안에 5회 초과 재시작 시 watchdog도 중단 (crash loop 방지)
  - 셸을 닫아도 백그라운드 유지 (setsid + nohup)
EOF
}

# ──────────────────── 디스패치 ────────────────────

cmd="${1:-help}"
shift || true

case "$cmd" in
    setup)              cmd_setup ;;
    init)               cmd_init "$@" ;;
    update)             cmd_update "$@" ;;
    init-index)         cmd_init_index "$@" ;;
    update-index)       cmd_update_index "$@" ;;
    tel)                cmd_tel "$@" ;;
    decision-rerun)     cmd_decision_rerun "$@" ;;
    start)              cmd_start ;;
    stop)               cmd_stop ;;
    status)             cmd_status ;;
    logs)               cmd_logs ;;
    test)               cmd_test "$@" ;;
    demo)               cmd_demo "$@" ;;
    help|-h|--help|"")  cmd_help ;;
    __watchdog__)       run_watchdog ;;
    *)
        echo "[go] unknown command: $cmd" >&2
        cmd_help
        exit 2
        ;;
esac
