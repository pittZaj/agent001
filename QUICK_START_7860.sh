#!/usr/bin/env bash
# 快速启动 7860 智能体调试平台（单命令版）
#
# 用法：
#   cd /mnt/data3/clip/LangGraph/agent
#   bash QUICK_START_7860.sh
#
# 说明：
#   - 会占据当前终端（前台运行）
#   - 按 Ctrl+C 停止
#   - 启动后访问 http://192.168.1.90:7860

set -euo pipefail

echo "========================================="
echo "🚀 启动 7860 智能体调试平台"
echo "========================================="
echo ""

# 1. 检查依赖
echo "1️⃣ 检查依赖..."
if ! redis-cli ping &>/dev/null; then
    echo "   ⚠️  Redis 未运行，7860 会话记忆功能可能异常"
    echo "   建议: sudo systemctl start redis 或 redis-server &"
fi

if ! curl -s -m 2 http://localhost:8004/v1/models &>/dev/null; then
    echo "   ⚠️  模型服务 8004 未响应，多模态对话可能失败"
fi

# 2. 清理旧进程
echo ""
echo "2️⃣ 清理旧进程..."
for pid in $(pgrep -f "python.*web/app\.py" 2>/dev/null || true); do
    cwd=$(readlink -f "/proc/$pid/cwd" 2>/dev/null || echo "")
    if [ "$cwd" = "$(pwd)" ]; then
        echo "   停止 PID=$pid"
        kill "$pid" 2>/dev/null || true
    fi
done
sleep 1

# 3. 等待端口释放
echo ""
echo "3️⃣ 等待端口 7860 释放..."
for i in $(seq 1 10); do
    if ! python3 -c "import socket,sys; s=socket.socket(); sys.exit(0 if s.connect_ex(('127.0.0.1',7860))==0 else 1)" 2>/dev/null; then
        echo "   ✅ 端口 7860 已就绪"
        break
    fi
    sleep 1
done

# 4. 激活环境
echo ""
echo "4️⃣ 激活 conda 环境..."
source /root/anaconda3/bin/activate agent
echo "   ✅ 已激活: agent"

# 5. 验证关键包
echo ""
echo "5️⃣ 验证依赖..."
python3 << 'EOF'
import sys
try:
    import gradio
    print(f"   ✅ gradio {gradio.__version__}")
except ImportError:
    print("   ❌ gradio 未安装，执行: pip install gradio")
    sys.exit(1)

try:
    import sentence_transformers
    print(f"   ✅ sentence-transformers 已安装")
except ImportError:
    print("   ⚠️  sentence-transformers 未安装（RAG 知识库功能需要）")
EOF

# 6. 启动服务（前台模式）
echo ""
echo "========================================="
echo "🎉 启动中..."
echo "========================================="
echo ""
echo "📍 访问地址: http://192.168.1.90:7860"
echo "📍 停止服务: 按 Ctrl+C"
echo ""
echo "----------------------------------------"
echo ""

export AOA_WEB_PORT=7860
python web/app.py
