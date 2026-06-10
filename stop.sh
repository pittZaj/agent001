#!/bin/bash
# KSAgent 停止脚本
HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE"

if [ -f ksagent.pid ]; then
    PID=$(cat ksagent.pid)
    if kill -0 $PID 2>/dev/null; then
        kill $PID
        echo "[OK] 已停止 KSAgent PID=$PID"
    else
        echo "[INFO] PID=$PID 进程已不存在"
    fi
    rm -f ksagent.pid
else
    echo "[WARN] 未找到 ksagent.pid"
    pkill -f "python main.py" && echo "[OK] 已强制终止" || echo "[INFO] 无进程在跑"
fi
