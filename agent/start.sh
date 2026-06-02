#!/bin/bash
# Agent-of-Agent 元智能体启动脚本

set -e

echo "============================================================"
echo "🚀 启动 Agent-of-Agent 元智能体"
echo "============================================================"

# 1. 激活 conda 环境
echo "1️⃣  激活 conda 环境: agent"
source $(conda info --base)/etc/profile.d/conda.sh
conda activate agent

# 2. 检查 vLLM 服务
echo "2️⃣  检查 vLLM 服务 (端口 8004)..."
if curl -s http://127.0.0.1:8004/v1/models > /dev/null 2>&1; then
    echo "   ✅ vLLM 服务正常"
else
    echo "   ❌ vLLM 服务未启动！"
    echo "   请先启动 vLLM 服务："
    echo "   pkill -f 'vllm serve.*Qwen3-VL-4B-Instruct-FP8'; sleep 5; \\"
    echo "   cd /mnt/data3/clip/LangGraph/VLLM/Qwen3-VL-4B-Instruct-FP8 && \\"
    echo "   nohup bash start_server_4b_fp8.sh 5880 8004 > vllm_server_4b_fp8.log 2>&1 & \\"
    echo "   echo \$! > vllm_server_4b_fp8.pid"
    exit 1
fi

# 3. 运行模式选择
MODE="${1:-test}"

case "$MODE" in
    test)
        echo "3️⃣  运行简化测试（推荐，节省 token）"
        python test_mvp.py
        ;;
    full)
        echo "3️⃣  运行完整迭代（会消耗较多 token）"
        python run_meta_agent.py
        ;;
    *)
        echo "❌ 未知模式: $MODE"
        echo "用法: $0 [test|full]"
        echo "  test - 简化测试（默认）"
        echo "  full - 完整迭代"
        exit 1
        ;;
esac

echo ""
echo "============================================================"
echo "✅ 执行完成"
echo "============================================================"
