#!/usr/bin/env bash
# Gradio Web 控制台重启脚本
# 用法: bash restart_web.sh

set -euo pipefail

echo "=== Gradio Web 控制台重启 ==="
echo ""

# 1. 停止旧进程
echo "1. 停止所有旧 Web 进程..."
pkill -f "python.*web/app.py" 2>/dev/null || true
sleep 2
# 强制清理仍存活的
if pgrep -f "python.*web/app.py" > /dev/null 2>&1; then
    echo "   强制停止残留进程..."
    pkill -9 -f "python.*web/app.py" 2>/dev/null || true
    sleep 1
fi

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
if [ -f "agent/web/web.pid" ]; then
    rm -f agent/web/web.pid
fi

# 3. 激活 conda 环境
echo ""
echo "2. 激活 conda 环境..."
source /root/anaconda3/bin/activate agent
echo "   ✅ 环境已激活: $(conda info --envs | grep '*' | awk '{print $1}')"

# 4. 验证依赖版本
echo ""
echo "3. 验证关键依赖..."
echo "   sentence-transformers: $(pip show sentence-transformers 2>/dev/null | grep Version | awk '{print $2}')"
echo "   torch: $(pip show torch 2>/dev/null | grep Version | awk '{print $2}')"
echo "   transformers: $(pip show transformers 2>/dev/null | grep Version | awk '{print $2}')"

# 5. 启动新进程
echo ""
echo "4. 启动 Gradio Web 控制台..."
cd /mnt/data3/clip/LangGraph/agent/agent
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
