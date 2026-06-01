#!/bin/bash
# KSAgent 启动脚本
set -e

HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE"

# 检查 vLLM 服务
echo "[INFO] 检查 vLLM 后端..."
if curl -s -f http://127.0.0.1:8002/v1/models > /dev/null 2>&1; then
    echo "[OK] vLLM 服务正常"
else
    echo "[WARN] vLLM 服务未启动 (http://127.0.0.1:8002)"
    echo "[WARN] 请先启动 /mnt/data3/clip/LangGraph/VLLM/start_server.sh"
    echo "[WARN] 继续启动 KSAgent，但 LLM 调用会失败"
fi

# 检查依赖
if ! python -c "import fastapi, langgraph, langchain_openai" 2>/dev/null; then
    echo "[ERR] 依赖未安装，请先运行: pip install -r requirements.txt"
    exit 1
fi

# 启动 KSAgent
echo "[INFO] 启动 KSAgent 服务..."
nohup python main.py > ksagent.log 2>&1 &
PID=$!
echo $PID > ksagent.pid
sleep 2

if kill -0 $PID 2>/dev/null; then
    echo "[OK] KSAgent 已启动 PID=$PID"
    echo "[INFO] 监听端口: 8000"
    echo "[INFO] 日志文件: $HERE/ksagent.log"
    echo "[INFO] 测试命令: python tests/test_e2e.py"
    echo "[INFO] 停止服务: kill $PID  或  bash stop.sh"
else
    echo "[ERR] 启动失败，查看日志: $HERE/ksagent.log"
    tail -20 ksagent.log
    exit 1
fi
