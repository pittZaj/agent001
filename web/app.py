"""智能体调试平台 Web 控制台（Gradio）。

页面（2 个 Tab）：
  1. Agent 对话测试 — ChatGPT 风格：左侧会话导航栏（新建会话 + 历史会话列表，
     可点击切换继续聊），右侧对话窗。每个会话独立 thread_id，后端 checkpointer
     按 thread_id 维护**短期记忆**（同一会话内多轮连贯，切换/新建会话相互隔离）。
     仅保留「主智能体(增强)」。
  2. 知识库管理 — 上传规章文档、检索测试、分块查看/编辑（RAG）。

短期记忆说明：
  - 记忆是"会话级/短期"的：仅在当前会话窗口（thread_id）内生效；
  - 新建会话 = 新 thread_id = 干净记忆；点历史会话 = 切回该 thread_id 的记忆继续；
  - 轮数达到软上限（默认 15 轮）时顶部提示「建议新建会话」，避免历史过长拖慢响应。

启动:
  conda activate agent
  cd /mnt/data3/clip/LangGraph/agent
  bash restart_web.sh          # 端口 7860
"""
from __future__ import annotations

import base64
import mimetypes
import os
import sys
import uuid
from pathlib import Path

import gradio as gr

# 本文件所在目录 agent/web/ 加入 sys.path，以导入同目录的 agent_chat / kb_manager。
WEB_DIR = Path(__file__).resolve().parent
if str(WEB_DIR) not in sys.path:
    sys.path.insert(0, str(WEB_DIR))

import agent_chat  # noqa: E402
from kb_manager import build_kb_tab  # noqa: E402

# 本平台只调试主智能体（增强主图，背后即短期记忆 + 真实平台对接）
MAIN_AGENT = agent_chat.MAIN_AGENT_NAME


# ============================================================
# 工具函数
# ============================================================
def _file_to_data_url(path: str) -> str | None:
    """把本地图片文件读成 data URL（data:image/xxx;base64,...）。"""
    try:
        mime, _ = mimetypes.guess_type(path)
        if not mime or not mime.startswith("image"):
            mime = "image/jpeg"
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        return f"data:{mime};base64,{b64}"
    except Exception:
        return None


def _new_thread_id() -> str:
    """生成新会话 thread_id（前端无感知，用户不需手填）。"""
    return f"sess_{uuid.uuid4().hex[:12]}"


def _derive_title(text: str) -> str:
    """用首条用户消息生成会话标题（截断）。"""
    t = (text or "").strip().replace("\n", " ")
    if not t:
        return "新会话"
    return t[:18] + ("…" if len(t) > 18 else "")


# ============================================================
# 会话状态模型（存于 gr.State，前端内存）
#   sessions: { thread_id: {"title": str, "history": [chat messages]} }
#   current_tid: 当前激活的 thread_id
#   后端短期记忆由 checkpointer 按 thread_id 维护；这里只存"展示用"对话气泡。
# ============================================================
def _empty_session() -> dict:
    return {"title": "新会话", "history": []}


def _history_of(sessions: dict, tid: str) -> list:
    return (sessions.get(tid) or _empty_session())["history"]


def _turn_banner(tid: str) -> str:
    """顶部状态条：显示当前会话已聊轮数；达软上限提示新建会话。"""
    turns, over = agent_chat.session_turn_info(tid)
    if over:
        return (f"### 💬 当前会话已 **{turns}** 轮 ⚠️ "
                f"建议点左侧「➕ 新建会话」开新话题，避免历史过长拖慢响应。")
    if turns > 0:
        return f"### 💬 当前会话已 **{turns}** 轮（短期记忆生效中）"
    return "### 💬 新会话（短期记忆将在本会话窗口内生效）"


def init_state():
    """首次加载：建一个空会话并激活。"""
    tid = _new_thread_id()
    sessions = {tid: _empty_session()}
    return sessions, tid, [], _turn_banner(tid), 0


def new_session(sessions: dict, tick: int):
    """新建会话：生成新 thread_id，清空对话窗，刷新侧栏。"""
    sessions = dict(sessions or {})
    tid = _new_thread_id()
    sessions[tid] = _empty_session()
    return sessions, tid, [], "", _turn_banner(tid), tick + 1


def switch_session(sessions: dict, tid: str, tick: int):
    """切换到历史会话：载入其对话气泡，继续聊（短期记忆按该 thread_id 延续）。"""
    sessions = sessions or {}
    history = _history_of(sessions, tid)
    return tid, history, "", _turn_banner(tid), tick + 1


def prepare_send(sessions: dict, current_tid: str, mm_input, tick: int):
    """发送前置步骤：登记会话/标题、把用户消息渲染进对话窗（只触发一次侧栏刷新）。"""
    sessions = dict(sessions or {})
    text = (mm_input or {}).get("text", "") or ""
    files = (mm_input or {}).get("files", []) or []

    # 空输入：原样返回，不动
    if not text.strip() and not files:
        return (sessions, current_tid, _history_of(sessions, current_tid),
                mm_input, tick, _turn_banner(current_tid))

    # 兜底：无激活会话则新建
    if not current_tid or current_tid not in sessions:
        current_tid = _new_thread_id()
        sessions[current_tid] = _empty_session()

    history = sessions[current_tid]["history"]
    for f in files:
        history.append({"role": "user", "content": {"path": f}})
    if text.strip():
        history.append({"role": "user", "content": text})

    # 首条用户消息 → 作为会话标题
    if sessions[current_tid]["title"] == "新会话" and text.strip():
        sessions[current_tid]["title"] = _derive_title(text)

    # 占位 assistant 气泡，待流式填充
    history.append({"role": "assistant", "content": "⏳ 处理中…"})
    # 清空输入框；bump tick 刷新侧栏标题
    return sessions, current_tid, history, None, tick + 1, _turn_banner(current_tid)


def stream_reply(sessions: dict, current_tid: str):
    """流式回复：把 token 实时写进当前会话最后一条 assistant 气泡（不刷侧栏）。"""
    sessions = sessions or {}
    history = _history_of(sessions, current_tid)
    if not history or history[-1].get("role") != "assistant":
        yield history, ""
        return

    # 收集"本轮"输入：history[-1] 是 assistant 占位，向前取连续的 user 气泡
    # （遇到更早的 assistant 即停，避免把上一轮内容当本轮）。
    text = ""
    images = []
    for m in reversed(history[:-1]):
        if m.get("role") != "user":
            break
        c = m.get("content")
        if isinstance(c, dict) and "path" in c:
            u = _file_to_data_url(c["path"])
            if u:
                images.insert(0, u)
        elif isinstance(c, str):
            text = c  # 最靠近 assistant 的那条文本即本轮问题

    last_out = None
    for partial, out in agent_chat.chat_stream(MAIN_AGENT, text, images=images, thread_id=current_tid):
        history[-1]["content"] = partial
        last_out = out
        debug = agent_chat.format_debug_panel(out) if out else ""
        yield history, debug


# ============================================================
# UI
# ============================================================
def build_ui():
    with gr.Blocks(title="智能体调试平台", theme=gr.themes.Soft()) as demo:
        gr.Markdown("# 智能体调试平台")

        # 会话状态（前端内存）
        sessions_st = gr.State({})      # {tid: {title, history}}
        current_tid_st = gr.State("")   # 当前会话 thread_id
        sidebar_tick = gr.State(0)      # 侧栏刷新计数器（仅在会话列表变化时 bump）

        with gr.Tabs():
            # ===================== Tab 1: Agent 对话测试（ChatGPT 风格）=====================
            with gr.Tab("1. Agent 对话测试"):
                with gr.Row():
                    # ---------- 左侧：会话导航栏 ----------
                    with gr.Column(scale=1, min_width=220):
                        gr.Markdown("### 会话")
                        new_btn = gr.Button("➕ 新建会话", variant="primary", size="sm")
                        gr.Markdown("---")

                        # 历史会话列表：用 @gr.render 动态渲染为可点击按钮
                        @gr.render(inputs=[sessions_st, current_tid_st, sidebar_tick])
                        def _render_sessions(sessions, cur_tid, _tick):
                            sessions = sessions or {}
                            if not sessions:
                                gr.Markdown("_暂无会话，点上方新建_")
                                return
                            # 新会话在上（dict 插入序的逆序）
                            for tid in reversed(list(sessions.keys())):
                                meta = sessions[tid]
                                mark = "🟢 " if tid == cur_tid else "💬 "
                                btn = gr.Button(
                                    mark + (meta.get("title") or "新会话"),
                                    size="sm",
                                    variant="secondary" if tid != cur_tid else "primary",
                                )
                                btn.click(
                                    switch_session,
                                    inputs=[sessions_st, gr.State(tid), sidebar_tick],
                                    outputs=[current_tid_st, chatbot, debug_md,
                                             turn_banner, sidebar_tick],
                                )

                    # ---------- 右侧：对话窗 ----------
                    with gr.Column(scale=4):
                        gr.Markdown(
                            "**ChatGPT 风格 · 短期记忆**：同一会话内多轮连贯（记得上文）；"
                            "「➕ 新建会话」开新话题、点左侧历史会话可切回继续。"
                            "默认基座 `Qwen3-VL-4B-Instruct-FP8`，支持上传图片提问（多模态）。"
                        )
                        turn_banner = gr.Markdown("### 💬 新会话（短期记忆将在本会话窗口内生效）")

                        chatbot = gr.Chatbot(
                            label="对话窗口",
                            type="messages",
                            height=460,
                            show_copy_button=True,
                        )
                        with gr.Row():
                            msg_box = gr.MultimodalTextbox(
                                label="发消息（可附图）",
                                placeholder="例：查询最近5条AI告警 | 统计告警类型并画饼图 | "
                                            "（追问）把上面那个再画成柱状图 | （上传照片）有人没戴安全帽吗？",
                                file_types=["image"],
                                file_count="multiple",
                                scale=4,
                            )
                            send_btn = gr.Button("发送", variant="primary", scale=1)

                        with gr.Accordion("🔍 最近一次调用的 plan / tool_results / 耗时", open=False):
                            debug_md = gr.Markdown()

                # ---------- 事件绑定 ----------
                # 发送：两段式（prepare 刷侧栏+渲染用户气泡 → stream 流式填充答案）
                send_evt = msg_box.submit(
                    prepare_send,
                    inputs=[sessions_st, current_tid_st, msg_box, sidebar_tick],
                    outputs=[sessions_st, current_tid_st, chatbot, msg_box,
                             sidebar_tick, turn_banner],
                ).then(
                    stream_reply,
                    inputs=[sessions_st, current_tid_st],
                    outputs=[chatbot, debug_md],
                ).then(
                    lambda tid: _turn_banner(tid),
                    inputs=[current_tid_st], outputs=[turn_banner],
                )

                send_click = send_btn.click(
                    prepare_send,
                    inputs=[sessions_st, current_tid_st, msg_box, sidebar_tick],
                    outputs=[sessions_st, current_tid_st, chatbot, msg_box,
                             sidebar_tick, turn_banner],
                ).then(
                    stream_reply,
                    inputs=[sessions_st, current_tid_st],
                    outputs=[chatbot, debug_md],
                ).then(
                    lambda tid: _turn_banner(tid),
                    inputs=[current_tid_st], outputs=[turn_banner],
                )

                # 新建会话
                new_btn.click(
                    new_session,
                    inputs=[sessions_st, sidebar_tick],
                    outputs=[sessions_st, current_tid_st, chatbot, debug_md,
                             turn_banner, sidebar_tick],
                )

                # 首次加载初始化一个空会话
                demo.load(
                    init_state,
                    outputs=[sessions_st, current_tid_st, chatbot, turn_banner, sidebar_tick],
                )

            # ===================== Tab 2: 知识库管理 =====================
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



