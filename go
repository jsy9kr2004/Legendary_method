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

cmd_tel() {
    ensure_venv
    "$PY" -m src.notify.test_send "$@"
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
        if [[ -f "$WATCHDOG_LOG" ]]; then
            echo "--- 최근 watchdog 로그 ---"
            tail -n 5 "$WATCHDOG_LOG"
        fi
        return 0
    fi
    echo "[go] not running"
    return 1
}

cmd_stop() {
    if ! is_alive "$PID_FILE"; then
        echo "[go] not running"
        rm -f "$PID_FILE" "$CHILD_PID_FILE"
        return 0
    fi
    local wpid cpid
    wpid="$(cat "$PID_FILE")"
    cpid="$(cat "$CHILD_PID_FILE" 2>/dev/null || true)"
    echo "[go] stopping watchdog PID=$wpid (scheduler PID=${cpid:-?})"
    # wrapper 먼저 → 재시작 안 함. trap이 자식도 정리하지만 명시적으로 한 번 더.
    kill -TERM "$wpid" 2>/dev/null || true
    [[ -n "$cpid" ]] && kill -TERM "$cpid" 2>/dev/null || true
    # grace 10초
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
        echo "[go] 상태: ./go status   |   로그: ./go logs   |   중지: ./go stop"
    else
        echo "[go] FAILED to start. $WATCHDOG_LOG 확인" >&2
        return 1
    fi
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
  start     setup + 일봉 incremental + 스케줄러 백그라운드 실행 (watchdog 포함)
  stop      백그라운드 스케줄러 + watchdog 종료
  status    실행 상태 확인
  logs      최근 로그 출력 (watchdog + 오늘자 trader)

  tel       텔레그램 연결 테스트  (예: ./go tel "안녕")
  update    일봉 incremental만 (어제까지)
  init      5년치 일봉 backfill (이미 있는 건 자동 skip)
  setup     venv 생성 + 의존성 설치 (idempotent)
  test      pytest 실행

처음 실행:
  cp .env.example .env       # 토큰 등 입력
  ./go tel                   # 텔레그램 연결 확인
  ./go init                  # (선택) 5년치 백필 — 시간 걸림
  ./go start                 # 스케줄러 데몬 시작

watchdog 정책:
  - scheduler가 죽으면 5초 후 자동 재시작
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
    tel)                cmd_tel "$@" ;;
    start)              cmd_start ;;
    stop)               cmd_stop ;;
    status)             cmd_status ;;
    logs)               cmd_logs ;;
    test)               cmd_test "$@" ;;
    help|-h|--help|"")  cmd_help ;;
    __watchdog__)       run_watchdog ;;
    *)
        echo "[go] unknown command: $cmd" >&2
        cmd_help
        exit 2
        ;;
esac
