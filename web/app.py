"""智能体调试平台 Web 控制台（Gradio）。

从原「Agent-of-Agent 控制台」（agent/agent/web/app.py，7 个 Tab）抽离出
真正用于日常调试的两个功能，组合为精简的智能体调试页面：

  1. Agent 对话测试 — 选主智能体 / 已发布 Agent → ChatGPT 风格调测真实平台 MCP 工具
  2. 知识库管理     — 上传规章文档、检索测试、分块查看/编辑（RAG）

背景（2026-06-15）：当前重点是打通真实平台对接并稳定运行，
Agent-of-Agent 生成式流水线（原 Tab 1-5）暂缓，后续智能体拓展时再重启。
故此页面只保留对话测试与知识库两个稳定可用的调试入口。

启动:
  conda activate agent
  cd /mnt/data3/clip/LangGraph/agent
  bash restart_web.sh          # 端口 7860
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import gradio as gr

# 本文件所在目录 agent/web/ 加入 sys.path，以导入同目录的 agent_chat / kb_manager。
WEB_DIR = Path(__file__).resolve().parent
if str(WEB_DIR) not in sys.path:
    sys.path.insert(0, str(WEB_DIR))

import agent_chat  # noqa: E402
from kb_manager import build_kb_tab  # noqa: E402


# ============================================================
# Tab 1: Agent 对话测试（原控制台 Tab 6）
# ============================================================
def chat_send(history, agent_name, message):
    """ChatGPT 风格：每条都独立调用 agent.run。"""
    history = history or []
    if not message or not message.strip():
        return history, "", ""
    out = agent_chat.chat_once(agent_name, message)
    history.append({"role": "user", "content": message})
    history.append({"role": "assistant", "content": out.get("response", "")})
    debug = agent_chat.format_debug_panel(out)
    return history, "", debug


def chat_clear():
    return [], ""


def refresh_published_dropdown():
    names = agent_chat.published_agent_names()
    # gr.update 同时更新 choices 与默认值
    return gr.update(choices=names, value=names[0] if names else None)


def reload_agent_action(name: str):
    return agent_chat.reload_agent(name)


# ============================================================
# UI
# ============================================================
def build_ui():
    published_now = agent_chat.published_agent_names()

    with gr.Blocks(title="智能体调试平台", theme=gr.themes.Soft()) as demo:
        gr.Markdown("# 智能体调试平台")
        gr.Markdown(
            "**对话测试 + 知识库管理**，用于日常调试已对接真实平台的主智能体。\n\n"
            "**已对接真实平台**：MCP Server `192.168.1.199:6620/mcp`"
            "（19 个工具：ai_event_* / video_* / system_*），鉴权由 MCP 服务进程内部处理，本端无需密钥。\n\n"
            "默认基座 `Qwen3-VL-4B-Instruct-FP8` (vLLM 8004)。"
        )

        with gr.Tabs():
            # ----- Tab 1: Agent 对话测试 -----
            with gr.Tab("1. Agent 对话测试"):
                gr.Markdown(
                    "**ChatGPT 风格调试智能体**。每条消息独立调用 `agent.run`，"
                    "Agent 本身不带会话上下文（多轮对话只是 UI 展示）。"
                    "默认选「主智能体(增强)」即走真实平台对接的 Plan-Execute 主图。"
                )
                with gr.Row():
                    agent_dd = gr.Dropdown(
                        choices=published_now,
                        value=published_now[0] if published_now else None,
                        label="选择 Agent（主智能体 或 已发布 Agent）",
                        scale=3,
                    )
                    refresh_agents_btn = gr.Button("刷新列表", scale=0)
                    reload_btn = gr.Button("重新加载该 Agent", scale=0,
                                           variant="secondary")
                reload_msg = gr.Markdown()

                chatbot = gr.Chatbot(
                    label="对话窗口",
                    type="messages",
                    height=420,
                    show_copy_button=True,
                )
                with gr.Row():
                    msg_box = gr.Textbox(
                        label="给 Agent 发消息（调用真实平台 MCP 工具）",
                        placeholder="例：查询最近 5 条 AI 告警 | 统计告警类型并画饼图 | 统计最近 7 天每天告警数画折线图 | 查询视频设备列表",
                        scale=4,
                    )
                    send_btn = gr.Button("发送", variant="primary", scale=1)
                    clear_btn = gr.Button("清空", scale=0)

                with gr.Accordion("🔍 最近一次调用的 plan / tool_results / 耗时", open=True):
                    debug_md = gr.Markdown()

                send_btn.click(chat_send, inputs=[chatbot, agent_dd, msg_box],
                               outputs=[chatbot, msg_box, debug_md])
                msg_box.submit(chat_send, inputs=[chatbot, agent_dd, msg_box],
                               outputs=[chatbot, msg_box, debug_md])
                clear_btn.click(chat_clear, outputs=[chatbot, debug_md])
                refresh_agents_btn.click(refresh_published_dropdown, outputs=agent_dd)
                reload_btn.click(reload_agent_action, inputs=agent_dd, outputs=reload_msg)

            # ----- Tab 2: 知识库管理（原控制台 Tab 7）-----
            build_kb_tab()

    return demo


if __name__ == "__main__":
    demo = build_ui()
    port = int(os.environ.get("AOA_WEB_PORT", "7860"))
    demo.queue().launch(
        server_name="0.0.0.0",
        server_port=port,
        share=False,
        show_error=True,
    )
