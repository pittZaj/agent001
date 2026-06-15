#!/usr/bin/env bash
# 智能体调试平台 Web 重启脚本
# 用法: cd /mnt/data3/clip/LangGraph/agent && bash restart_web.sh
#
# 迁移说明（2026-06-15）：
#   原「Agent-of-Agent 控制台」(agent/agent/web/app.py，7 个 Tab) 已停用。
#   新「智能体调试平台」(agent/web/app.py) 只保留 2 个 Tab：
#     1. Agent 对话测试   2. 知识库管理
#   端口仍为 7860。

set -euo pipefail

LANGGRAPH_ROOT="/mnt/data3/clip/LangGraph/agent"
# 进程匹配串：启动命令是 `python web/app.py`（cwd=agent/），命令行里是相对路径 web/app.py。
# 旧控制台命令行同样是 web/app.py 但 cwd=agent/agent/，仅凭命令行无法区分，
# 故用 pgrep 拿到 PID 后再核对其 cwd 是否为本 LANGGRAPH_ROOT，精确只杀本页面进程。
APP_REL="web/app.py"

echo "=== 智能体调试平台 Web 重启 ==="
echo ""

# 找出 cwd 确实在本根目录、且运行 web/app.py 的进程 PID
_find_web_pids() {
    for pid in $(pgrep -f "python.*${APP_REL}" 2>/dev/null || true); do
        cwd=$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)
        if [ "$cwd" = "$LANGGRAPH_ROOT" ]; then
            echo "$pid"
        fi
    done
}

# 1. 停止旧进程（只杀 cwd 在本根目录的，避免误杀旧 Agent-of-Agent 控制台）
echo "1. 停止旧 Web 进程..."
for pid in $(_find_web_pids); do
    echo "   停止 PID=$pid"
    kill "$pid" 2>/dev/null || true
done
sleep 2
# 强制清理仍存活的
for pid in $(_find_web_pids); do
    echo "   强制停止残留 PID=$pid"
    kill -9 "$pid" 2>/dev/null || true
done
sleep 1

# 等待端口释放（最多 10 秒），避免 "Cannot find empty port" 报错
PORT="${AOA_WEB_PORT:-7860}"
echo "   等待端口 $PORT 释放..."
for i in $(seq 1 10); do
    if ! python3 -c "import socket,sys; s=socket.socket(); sys.exit(0 if s.connect_ex(('127.0.0.1',$PORT))==0 else 1)" 2>/dev/null; then
        echo "   ✅ 端口 $PORT 已释放"
        break
    fi
    sleep 1
done

# 2. 清理 PID 文件
if [ -f "$LANGGRAPH_ROOT/web/web.pid" ]; then
    rm -f "$LANGGRAPH_ROOT/web/web.pid"
fi

# 3. 激活 conda 环境
echo ""
echo "2. 激活 conda 环境..."
source /root/anaconda3/bin/activate agent
echo "   ✅ 环境已激活: $(conda info --envs | grep '*' | awk '{print $1}')"

# 4. 验证关键依赖
echo ""
echo "3. 验证关键依赖..."
echo "   gradio: $(pip show gradio 2>/dev/null | grep Version | awk '{print $2}')"
echo "   sentence-transformers: $(pip show sentence-transformers 2>/dev/null | grep Version | awk '{print $2}')"
echo "   torch: $(pip show torch 2>/dev/null | grep Version | awk '{print $2}')"
echo "   transformers: $(pip show transformers 2>/dev/null | grep Version | awk '{print $2}')"

# 5. 启动新进程（工作目录 = LangGraph 根 agent/，新页面在 web/app.py）
echo ""
echo "4. 启动智能体调试平台..."
cd "$LANGGRAPH_ROOT"
export AOA_WEB_PORT="${AOA_WEB_PORT:-7860}"

# 前台模式（推荐，便于查看日志）
python web/app.py

# 如需后台模式，取消下面的注释并注释掉上面的 python 命令
# LOG=web/web.log
# nohup setsid python web/app.py >"$LOG" 2>&1 </dev/null &
# PID=$!
# echo "$PID" > web/web.pid
# echo "   ✅ Web 已后台启动 PID=$PID"
# echo "   日志: $LOG"
# echo "   浏览器: http://$(hostname -I | awk '{print $1}'):$AOA_WEB_PORT"
# echo ""
# echo "查看日志: tail -f $LOG"
# echo "停止服务: kill $PID"
