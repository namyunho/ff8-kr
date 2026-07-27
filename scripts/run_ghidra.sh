#!/usr/bin/env bash
# Ghidra GUI 를 FF8 프로젝트와 함께 띄운다.
#
# Homebrew 배치상 실행 파일이 bin/ 이 아니라 libexec/ 아래에 있고, JDK 21 을
# JAVA_HOME 으로 지정해야 한다. 설치된 확장은 GhidraMCP 5.14.2 이며 HTTP 서버를
# 8080 이 아니라 127.0.0.1:8089 에 연다(UDS 도 함께 연다). 8080 은 구형
# LaurieWired GhidraMCP 의 포트라 이 설치본에는 해당하지 않는다.
#
#     ./scripts/run_ghidra.sh              프로젝트를 열고 GUI 실행
#     ./scripts/run_ghidra.sh --status     실행 상태와 8089 포트만 확인
#     ./scripts/run_ghidra.sh --headless 0x8002E4A0,0x8002C358
#                                          지정 주소만 디컴파일해 출력

set -euo pipefail

GHIDRA_HOME="${GHIDRA_HOME:-/opt/homebrew/Cellar/ghidra/12.1.2/libexec}"
JAVA_HOME="${JAVA_HOME:-/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home}"
export JAVA_HOME

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_DIR="$PROJECT_ROOT/work/ghidra/proj"
PROJECT_NAME="FF8"
PROGRAM="SLPS_018.80.text.bin"
MCP_PORT=8089

die() { printf '%s\n' "$*" >&2; exit 1; }

ghidra_pid() {
    pgrep -f 'ghidra\.GhidraRun' | head -1
}

mcp_up() {
    lsof -nP -iTCP:"$MCP_PORT" -sTCP:LISTEN >/dev/null 2>&1
}

status() {
    local pid
    pid="$(ghidra_pid || true)"
    if [ -n "$pid" ]; then
        printf 'Ghidra 실행 중 (PID %s)\n' "$pid"
    else
        printf 'Ghidra 실행 중 아님\n'
    fi
    if mcp_up; then
        printf 'GhidraMCP 열림 (127.0.0.1:%s)\n' "$MCP_PORT"
    else
        printf 'GhidraMCP 닫힘 (%s 미개방)\n' "$MCP_PORT"
    fi
}

require_install() {
    [ -d "$GHIDRA_HOME" ] || die "Ghidra 를 찾을 수 없다: $GHIDRA_HOME
GHIDRA_HOME 을 지정하거나 'brew install ghidra' 로 설치한다."
    [ -x "$JAVA_HOME/bin/java" ] || die "JDK 21 을 찾을 수 없다: $JAVA_HOME
'brew install openjdk@21' 로 설치하거나 JAVA_HOME 을 지정한다."
}

headless() {
    local targets="${1:-}"
    [ -n "$targets" ] || die "디컴파일할 주소를 쉼표로 준다. 예: 0x8002E4A0,0x8002C358"
    require_install
    [ -d "$PROJECT_DIR/$PROJECT_NAME.rep" ] || die "프로젝트가 없다: $PROJECT_DIR
docs/reverse-engineering-mcp.md 의 import 절을 먼저 수행한다."
    # 헤드리스는 GUI 와 같은 프로젝트를 동시에 열 수 없다.
    if [ -n "$(ghidra_pid || true)" ]; then
        die "GUI 가 같은 프로젝트를 열고 있다. 먼저 Ghidra 를 닫는다."
    fi
    FF8_DECOMP_TARGETS="$targets" "$GHIDRA_HOME/support/analyzeHeadless" \
        "$PROJECT_DIR" "$PROJECT_NAME" \
        -process "$PROGRAM" -noanalysis \
        -scriptPath "$PROJECT_ROOT/scripts/ghidra" \
        -postScript DecompileTargets.java
}

launch() {
    require_install
    [ -f "$PROJECT_DIR/$PROJECT_NAME.gpr" ] || die "프로젝트가 없다: $PROJECT_DIR/$PROJECT_NAME.gpr
docs/reverse-engineering-mcp.md 의 import 절을 먼저 수행한다."

    local pid
    pid="$(ghidra_pid || true)"
    if [ -n "$pid" ]; then
        printf '이미 실행 중이다 (PID %s). 새로 띄우지 않는다.\n' "$pid"
        status
        return 0
    fi

    # GUI 는 터미널을 붙잡지 않도록 분리해 띄운다.
    nohup "$GHIDRA_HOME/ghidraRun" "$PROJECT_DIR/$PROJECT_NAME.gpr" \
        >/dev/null 2>&1 &
    disown || true

    printf 'Ghidra 실행 중... (프로젝트 %s)\n' "$PROJECT_NAME"
    printf '창이 뜨면 %s 를 열어야 GhidraMCP 가 %s 포트를 연다.\n' "$PROGRAM" "$MCP_PORT"
}

case "${1:-}" in
    --status)   status ;;
    --headless) headless "${2:-}" ;;
    "")         launch ;;
    *)          die "알 수 없는 인자: $1
사용법: run_ghidra.sh [--status | --headless <주소,주소>]" ;;
esac
