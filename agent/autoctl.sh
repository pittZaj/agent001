#!/usr/bin/env bash
# Agent-of-Agent autoctl.sh — 守护进程式控制脚本（仿 ConvNeXt-V2-wc/autoctl.sh）
# 用法:
#   bash autoctl.sh start <spec.md> [--dry-run]
#   bash autoctl.sh status <job_id>
#   bash autoctl.sh logs   <job_id>
#   bash autoctl.sh list
#   bash autoctl.sh stop   <job_id>

set -euo pipefail
cd "$(dirname "$0")"
PROJECT_ROOT="$(pwd)"
LOG_ROOT="$PROJECT_ROOT/logs/jobs"
ART_ROOT="$PROJECT_ROOT/artifacts"
mkdir -p "$LOG_ROOT" "$ART_ROOT"

usage() {
    cat <<EOF
用法:
  bash autoctl.sh start <spec.md> [--dry-run] [--max-iter N]
       后台运行 Agent-of-Agent 流水线 (Claude 生成 + Qwen3-VL 测试)
  bash autoctl.sh status <job_id>
  bash autoctl.sh logs   <job_id>
  bash autoctl.sh list
  bash autoctl.sh stop   <job_id>

环境变量 (start 必需, 除非 --dry-run):
  ANTHROPIC_AUTH_TOKEN  IMDS 代理 token
  ANTHROPIC_BASE_URL    默认 https://imds.ai/
  ANTHROPIC_MODEL       默认 claude-sonnet-4-6
EOF
}

ensure_env() {
    local dry_run="$1"
    if [[ "$dry_run" == "no" && -z "${ANTHROPIC_AUTH_TOKEN:-}" ]]; then
        echo "[ERR] 缺少 ANTHROPIC_AUTH_TOKEN; --dry-run 可绕开" >&2
        exit 1
    fi
    # 检查 vLLM
    if ! curl -sf http://127.0.0.1:8004/v1/models >/dev/null 2>&1; then
        echo "[WARN] vLLM 8004 未响应；生成的 Agent 将无法做 LLM 调用" >&2
    fi
}

ensure_conda() {
    # shellcheck disable=SC1091
    source "$(conda info --base)/etc/profile.d/conda.sh"
    conda activate agent
}

cmd_start() {
    if [[ $# -lt 1 ]]; then echo "缺少 spec 路径" >&2; usage; exit 1; fi
    local spec="$1"; shift
    if [[ ! -f "$spec" ]]; then echo "spec not found: $spec" >&2; exit 1; fi

    local dry_run="no"
    local extra_args=()
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --dry-run) dry_run="yes"; extra_args+=("--dry-run"); shift ;;
            --max-iter) extra_args+=("--max-iterations" "$2"); shift 2 ;;
            *) extra_args+=("$1"); shift ;;
        esac
    done

    ensure_env "$dry_run"
    ensure_conda

    local agent_name
    agent_name=$(grep -m1 -E '^- *name *[:：]' "$spec" | sed -E 's/.*[:：] *//; s/ *$//' || echo "unknown")
    local ts
    ts=$(date +%Y%m%d_%H%M%S)
    local job_id="${agent_name}_${ts}_$(head -c 4 /dev/urandom | xxd -p)"
    local job_dir="$LOG_ROOT/$job_id"
    mkdir -p "$job_dir"
    local logfile="$job_dir/run.log"

    echo "[INFO] starting job=$job_id (agent=$agent_name, dry_run=$dry_run)"

    # 后台启动；setsid 让进程独立于本 shell
    nohup setsid bash -c "
        cd '$PROJECT_ROOT'
        source \"\$(conda info --base)/etc/profile.d/conda.sh\"
        conda activate agent
        python run_meta_agent.py --spec '$spec' --job-id '$job_id' ${extra_args[*]:-}
    " >"$logfile" 2>&1 </dev/null &
    local pid=$!
    disown "$pid" 2>/dev/null || true
    echo "$pid" > "$job_dir/pid"

    sleep 1
    if ! kill -0 "$pid" 2>/dev/null; then
        echo "[ERR] 启动失败，看日志: $logfile" >&2
        tail -n 30 "$logfile" >&2 || true
        exit 1
    fi

    echo "job_id=$job_id"
    echo "pid=$pid"
    echo "log=$logfile"
    echo ""
    echo "查看日志: bash autoctl.sh logs $job_id"
    echo "查看状态: bash autoctl.sh status $job_id"
}

cmd_status() {
    local job_id="$1"
    local job_dir="$LOG_ROOT/$job_id"
    if [[ ! -d "$job_dir" ]]; then echo "no such job: $job_id" >&2; exit 1; fi
    local pid
    pid=$(cat "$job_dir/pid" 2>/dev/null || echo "")
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
        echo "status: running (pid=$pid)"
    else
        echo "status: finished"
    fi
    if [[ -f "$ART_ROOT/$job_id/REGISTER.json" ]]; then
        echo "--- REGISTER.json ---"
        cat "$ART_ROOT/$job_id/REGISTER.json"
    fi
}

cmd_logs() {
    local job_id="$1"
    local logfile="$LOG_ROOT/$job_id/run.log"
    [[ -f "$logfile" ]] || { echo "no log: $logfile" >&2; exit 1; }
    tail -F "$logfile"
}

cmd_list() {
    if [[ ! -d "$LOG_ROOT" ]]; then echo "(empty)"; return; fi
    printf '%-50s %-10s %s\n' "job_id" "pid_alive" "register"
    for d in $(ls -1 -t "$LOG_ROOT" 2>/dev/null); do
        local pid="-"
        local alive="no"
        if [[ -f "$LOG_ROOT/$d/pid" ]]; then
            pid=$(cat "$LOG_ROOT/$d/pid")
            if kill -0 "$pid" 2>/dev/null; then alive="yes"; fi
        fi
        local reg="missing"
        [[ -f "$ART_ROOT/$d/REGISTER.json" ]] && reg="yes"
        printf '%-50s %-10s %s\n' "$d" "$alive" "$reg"
    done
}

cmd_stop() {
    local job_id="$1"
    local pid_file="$LOG_ROOT/$job_id/pid"
    [[ -f "$pid_file" ]] || { echo "no pid file" >&2; exit 1; }
    local pid; pid=$(cat "$pid_file")
    if kill -0 "$pid" 2>/dev/null; then
        kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" || true
        sleep 2
        kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
        echo "stopped pid=$pid"
    else
        echo "not running"
    fi
}

case "${1:-}" in
    start)  shift; cmd_start  "$@" ;;
    status) shift; cmd_status "$@" ;;
    logs)   shift; cmd_logs   "$@" ;;
    list)   shift; cmd_list   "$@" ;;
    stop)   shift; cmd_stop   "$@" ;;
    *) usage; exit 1 ;;
esac
