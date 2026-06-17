#!/usr/bin/env bash
# KSAgent FastAPI 服务（8001）后台重启脚本
# 用法: cd /mnt/data3/clip/LangGraph/agent && bash restart_api.sh
#
# 说明：
#   - 这是供综合管理平台对接的生产 API（POST /api/v1/chat 等），默认后台运行，7×24 稳定。
#   - 与「智能体调试平台」(web/app.py, 7860) 是两个独立进程，互不影响。
#   - 启动命令是 `python main.py`（cwd=agent/）。

set -euo pipefail

LANGGRAPH_ROOT="/mnt/data3/clip/LangGraph/agent"
APP="main.py"
PORT=8001
LOG="$LANGGRAPH_ROOT/api.log"
PIDFILE="$LANGGRAPH_ROOT/api.pid"

echo "=== KSAgent FastAPI（$PORT）后台重启 ==="
echo ""

# 找出 cwd 确实在本根目录、且运行 main.py 的进程 PID（精确匹配，避免误杀别的 main.py）
_find_api_pids() {
    for pid in $(pgrep -f "python.*${APP}" 2>/dev/null || true); do
        cwd=$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)
        if [ "$cwd" = "$LANGGRAPH_ROOT" ]; then
            echo "$pid"
        fi
    done
}

# 1. 停止旧进程
echo "1. 停止旧 API 进程..."
for pid in $(_find_api_pids); do
    echo "   停止 PID=$pid"
    kill "$pid" 2>/dev/null || true
done
sleep 2
for pid in $(_find_api_pids); do
    echo "   强制停止残留 PID=$pid"
    kill -9 "$pid" 2>/dev/null || true
done
sleep 1

# 2. 等待端口释放（最多 10 秒）
echo "   等待端口 $PORT 释放..."
for i in $(seq 1 10); do
    if ! python3 -c "import socket,sys; s=socket.socket(); sys.exit(0 if s.connect_ex(('127.0.0.1',$PORT))==0 else 1)" 2>/dev/null; then
        echo "   ✅ 端口 $PORT 已释放"
        break
    fi
    sleep 1
done
rm -f "$PIDFILE"

# 3. 激活 conda 环境
echo ""
echo "2. 激活 conda 环境..."
source /root/anaconda3/bin/activate agent
echo "   ✅ 环境已激活: agent"

# 4. 后台启动（setsid 完全脱离终端，关掉 SSH 也不影响）
echo ""
echo "3. 后台启动 FastAPI..."
cd "$LANGGRAPH_ROOT"
nohup setsid python "$APP" >"$LOG" 2>&1 </dev/null &
PID=$!
echo "$PID" > "$PIDFILE"
echo "   ✅ 已后台启动 PID=$PID"

# 5. 等待并做健康检查（最多 60 秒，含 Skill Registry/图初始化时间）
echo ""
echo "4. 等待服务就绪（健康检查）..."
OK=0
for i in $(seq 1 60); do
    if curl -s -m 3 "http://127.0.0.1:$PORT/health" 2>/dev/null | grep -q '"status"'; then
        OK=1
        break
    fi
    sleep 1
done

echo ""
if [ "$OK" = "1" ]; then
    echo "   ✅ 服务已就绪"
    echo "   健康检查: $(curl -s -m 3 http://127.0.0.1:$PORT/health)"
    echo "   对接地址: http://$(hostname -I | awk '{print $1}'):$PORT"
    echo "   接口:     POST /api/v1/chat"
else
    echo "   ⚠️ 60 秒内未就绪，请查看日志排查："
    echo "   tail -50 $LOG"
fi

echo ""
echo "查看日志: tail -f $LOG"
echo "停止服务: kill \$(cat $PIDFILE)"
