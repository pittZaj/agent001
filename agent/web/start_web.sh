#!/usr/bin/env bash
# 启动 Agent-of-Agent Gradio Web 控制台
set -euo pipefail
cd "$(dirname "$0")/.."

# shellcheck disable=SC1091
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate agent

export AOA_WEB_PORT="${AOA_WEB_PORT:-7860}"

# 后台模式（设 BACKGROUND=1）
if [[ "${BACKGROUND:-0}" == "1" ]]; then
    LOG=web/web.log
    nohup setsid python web/app.py >"$LOG" 2>&1 </dev/null &
    PID=$!
    echo "$PID" > web/web.pid
    echo "[OK] web 已后台启动 PID=$PID, log=$LOG"
    echo "浏览器: http://<host>:$AOA_WEB_PORT"
else
    python web/app.py
fi
