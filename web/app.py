"""智能体调试平台 Web 控制台（Gradio）- 优化版

新增功能：
  1. 用户选择下拉框 - 模拟多租户（演示 Redis 持久化）
  2. 会话管理 - 删除和重命名按钮
  3. 自动加载 - 用户切换时从 Redis 加载历史会话

页面（2 个 Tab）：
  1. Agent 对话测试 — ChatGPT 风格 + 多用户模拟
  2. 知识库管理 — RAG 文档管理

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

# 本文件所在目录 agent/web/ 加入 sys.path
WEB_DIR = Path(__file__).resolve().parent
if str(WEB_DIR) not in sys.path:
    sys.path.insert(0, str(WEB_DIR))

import agent_chat  # noqa: E402
from kb_manager import build_kb_tab  # noqa: E402

MAIN_AGENT = agent_chat.MAIN_AGENT_NAME

# 模拟用户列表（演示多租户）
DEMO_USERS = [
    ("默认用户", "default"),
    ("张三（工程师）", "zhangsan"),
    ("李四（管理员）", "lisi"),
    ("王五（安全员）", "wangwu"),
]


# ============================================================
# 工具函数
# ============================================================
def _file_to_data_url(path: str) -> str | None:
    """把本地图片文件读成 data URL"""
    try:
        mime, _ = mimetypes.guess_type(path)
        if not mime or not mime.startswith("image"):
            mime = "image/jpeg"
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        return f"data:{mime};base64,{b64}"
    except Exception:
        return None


def _new_thread_id(user_id: str = "default") -> str:
    """生成新会话 thread_id（带用户前缀，便于过滤）"""
    return f"sess_{user_id}_{uuid.uuid4().hex[:12]}"


def _derive_title(text: str) -> str:
    """用首条用户消息生成会话标题（截断）"""
    t = (text or "").strip().replace("\n", " ")
    if not t:
        return "新会话"
    return t[:18] + ("…" if len(t) > 18 else "")


# ============================================================
# 会话状态模型
# ============================================================
def _empty_session() -> dict:
    return {"title": "新会话", "history": []}


def _history_of(sessions: dict, tid: str) -> list:
    return (sessions.get(tid) or _empty_session())["history"]


def _turn_banner(tid: str, user_id: str) -> str:
    """顶部状态条：显示当前用户和会话轮数"""
    user_name = dict(DEMO_USERS).get(user_id, user_id)
    turns, over = agent_chat.session_turn_info(tid)

    if over:
        return (f"### 👤 {user_name} | 💬 当前会话已 **{turns}** 轮 ⚠️ "
                f"建议点「➕ 新建会话」开新话题")
    if turns > 0:
        return f"### 👤 {user_name} | 💬 当前会话已 **{turns}** 轮（Redis 持久化记忆生效中）"
    return f"### 👤 {user_name} | 💬 新会话（记忆将持久化到 Redis）"


def init_state():
    """首次加载：默认用户，创建新会话"""
    user_id = "default"
    tid = _new_thread_id(user_id)
    sessions = {tid: _empty_session()}
    return sessions, tid, user_id, [], _turn_banner(tid, user_id), 0


def switch_user(user_id: str, tick: int):
    """切换用户：从 Redis 加载该用户的所有会话"""
    # 加载该用户的历史会话
    redis_sessions = agent_chat.list_user_sessions(user_id)

    sessions = {}
    for s in redis_sessions:
        tid = s["thread_id"]
        title = s.get("name", "新会话")
        # 预加载会话元数据（历史在切换到具体会话时再加载）
        sessions[tid] = {
            "title": title,
            "history": [],  # 占位，切换时才加载
        }

    # 如果该用户没有历史会话，创建一个新的
    if not sessions:
        tid = _new_thread_id(user_id)
        sessions[tid] = _empty_session()
        current_tid = tid
        history = []
    else:
        # 选择最新的会话并加载历史
        current_tid = list(sessions.keys())[0]
        history = agent_chat.load_session_history(current_tid)
        sessions[current_tid]["history"] = history

    return sessions, current_tid, user_id, history, "", _turn_banner(current_tid, user_id), tick + 1


def new_session(sessions: dict, user_id: str, tick: int):
    """新建会话：生成新 thread_id（带用户前缀）"""
    sessions = dict(sessions or {})
    tid = _new_thread_id(user_id)
    sessions[tid] = _empty_session()
    return sessions, tid, [], "", _turn_banner(tid, user_id), tick + 1


def switch_session(sessions: dict, user_id: str, tid: str, tick: int):
    """切换到历史会话：从 Redis 加载该会话的完整历史"""
    sessions = sessions or {}

    # 从 Redis 加载该会话的标题和历史
    title = agent_chat.get_session_title(tid)
    history = agent_chat.load_session_history(tid)

    if tid not in sessions:
        sessions[tid] = {"title": title, "history": history}
    else:
        sessions[tid]["title"] = title
        sessions[tid]["history"] = history

    return tid, history, "", _turn_banner(tid, user_id), tick + 1


def delete_session(sessions: dict, user_id: str, tid: str, current_tid: str, tick: int):
    """删除会话：从 Redis 和前端状态中移除"""
    sessions = dict(sessions or {})

    # 从 Redis 删除
    success, msg = agent_chat.delete_user_session(tid)

    if success:
        # 从前端状态移除
        sessions.pop(tid, None)

        # 如果删除的是当前会话，切换到其他会话或新建
        if tid == current_tid:
            if sessions:
                current_tid = list(sessions.keys())[0]
                history = _history_of(sessions, current_tid)
            else:
                current_tid = _new_thread_id(user_id)
                sessions[current_tid] = _empty_session()
                history = []

            gr.Info(msg)
            return (sessions, current_tid, history, "",
                    _turn_banner(current_tid, user_id), tick + 1)
        else:
            gr.Info(msg)
            return (sessions, current_tid, _history_of(sessions, current_tid), "",
                    _turn_banner(current_tid, user_id), tick + 1)
    else:
        gr.Warning(msg)
        return (sessions, current_tid, _history_of(sessions, current_tid), "",
                _turn_banner(current_tid, user_id), tick)


def rename_session_dialog(tid: str):
    """打开重命名对话框：返回当前标题供编辑"""
    current_title = agent_chat.get_session_title(tid)
    return gr.update(visible=True), current_title


def confirm_rename(sessions: dict, user_id: str, tid: str, new_title: str, tick: int):
    """确认重命名：更新 Redis 和前端状态"""
    sessions = dict(sessions or {})

    success, msg = agent_chat.rename_user_session(tid, new_title)

    if success:
        # 更新前端状态
        if tid in sessions:
            sessions[tid]["title"] = new_title.strip()

        gr.Info(msg)
        return (sessions, tick + 1, gr.update(visible=False))
    else:
        gr.Warning(msg)
        return (sessions, tick, gr.update(visible=True))


def prepare_send(sessions: dict, user_id: str, current_tid: str, mm_input, tick: int):
    """发送前置步骤：登记会话/标题、把用户消息渲染进对话窗"""
    sessions = dict(sessions or {})
    text = (mm_input or {}).get("text", "") or ""
    files = (mm_input or {}).get("files", []) or []

    # 空输入：原样返回
    if not text.strip() and not files:
        return (sessions, current_tid, _history_of(sessions, current_tid),
                mm_input, tick, _turn_banner(current_tid, user_id))

    # 兜底：无激活会话则新建
    if not current_tid or current_tid not in sessions:
        current_tid = _new_thread_id(user_id)
        sessions[current_tid] = _empty_session()

    history = sessions[current_tid]["history"]
    for f in files:
        history.append({"role": "user", "content": {"path": f}})
    if text.strip():
        history.append({"role": "user", "content": text})

    # 首条用户消息 → 作为会话标题
    if sessions[current_tid]["title"] == "新会话" and text.strip():
        sessions[current_tid]["title"] = _derive_title(text)

    # 占位 assistant 气泡
    history.append({"role": "assistant", "content": "⏳ 处理中…"})
    return sessions, current_tid, history, None, tick + 1, _turn_banner(current_tid, user_id)


def stream_reply(sessions: dict, current_tid: str):
    """流式回复：把 token 实时写进当前会话最后一条 assistant 气泡"""
    sessions = sessions or {}
    history = _history_of(sessions, current_tid)
    if not history or history[-1].get("role") != "assistant":
        yield history, ""
        return

    # 收集本轮输入
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
            text = c

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
        gr.Markdown("# 智能体调试平台 · Redis 持久化演示")

        # 会话状态（前端内存）
        sessions_st = gr.State({})      # {tid: {title, history}}
        current_tid_st = gr.State("")   # 当前会话 thread_id
        user_id_st = gr.State("default")  # 当前用户 ID
        sidebar_tick = gr.State(0)      # 侧栏刷新计数器

        with gr.Tabs():
            # ===================== Tab 1: Agent 对话测试 =====================
            with gr.Tab("1. Agent 对话测试"):
                with gr.Row():
                    # ---------- 左侧：会话导航栏 ----------
                    with gr.Column(scale=1, min_width=240):
                        gr.Markdown("### 🧑 用户选择")
                        user_dropdown = gr.Dropdown(
                            choices=[(name, uid) for name, uid in DEMO_USERS],
                            value="default",
                            label="模拟用户登录",
                            info="切换用户查看各自的会话记忆",
                        )

                        gr.Markdown("---")
                        gr.Markdown("### 💬 会话")
                        new_btn = gr.Button("➕ 新建会话", variant="primary", size="sm")
                        gr.Markdown("---")

                        # 历史会话列表
                        @gr.render(inputs=[sessions_st, current_tid_st, user_id_st, sidebar_tick])
                        def _render_sessions(sessions, cur_tid, user_id, _tick):
                            sessions = sessions or {}
                            if not sessions:
                                gr.Markdown("_暂无会话，点上方新建_")
                                return

                            # 新会话在上
                            for tid in reversed(list(sessions.keys())):
                                meta = sessions[tid]
                                title = meta.get("title") or "新会话"
                                is_current = tid == cur_tid

                                with gr.Row():
                                    # 会话按钮
                                    btn = gr.Button(
                                        ("🟢 " if is_current else "💬 ") + title,
                                        size="sm",
                                        variant="primary" if is_current else "secondary",
                                        scale=3,
                                    )
                                    btn.click(
                                        switch_session,
                                        inputs=[sessions_st, user_id_st, gr.State(tid), sidebar_tick],
                                        outputs=[current_tid_st, chatbot, debug_md,
                                                 turn_banner, sidebar_tick],
                                    )

                                    # 重命名按钮
                                    rename_btn = gr.Button("✏️", size="sm", scale=1)
                                    rename_btn.click(
                                        lambda t=tid: rename_session_dialog(t),
                                        outputs=[rename_modal, rename_input],
                                    )

                                    # 删除按钮
                                    delete_btn = gr.Button("🗑️", size="sm", scale=1)
                                    delete_btn.click(
                                        delete_session,
                                        inputs=[sessions_st, user_id_st, gr.State(tid),
                                                current_tid_st, sidebar_tick],
                                        outputs=[sessions_st, current_tid_st, chatbot, debug_md,
                                                 turn_banner, sidebar_tick],
                                    )

                    # ---------- 右侧：对话窗 ----------
                    with gr.Column(scale=4):
                        gr.Markdown(
                            "**ChatGPT 风格 · Redis 持久化记忆**：切换用户体验多租户隔离；"
                            "同一用户的会话记忆持久化到 Redis，重启服务后仍保留。"
                            "支持上传图片提问（多模态）。"
                        )
                        turn_banner = gr.Markdown("### 👤 默认用户 | 💬 新会话")

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

                # ---------- 重命名对话框（Modal）----------
                with gr.Row(visible=False) as rename_modal:
                    with gr.Column():
                        gr.Markdown("### ✏️ 重命名会话")
                        rename_input = gr.Textbox(label="新标题", placeholder="输入新的会话标题")
                        with gr.Row():
                            rename_confirm_btn = gr.Button("确认", variant="primary")
                            rename_cancel_btn = gr.Button("取消")

                # ---------- 事件绑定 ----------
                # 用户切换
                user_dropdown.change(
                    switch_user,
                    inputs=[user_dropdown, sidebar_tick],
                    outputs=[sessions_st, current_tid_st, user_id_st, chatbot,
                             debug_md, turn_banner, sidebar_tick],
                )

                # 发送消息
                send_evt = msg_box.submit(
                    prepare_send,
                    inputs=[sessions_st, user_id_st, current_tid_st, msg_box, sidebar_tick],
                    outputs=[sessions_st, current_tid_st, chatbot, msg_box,
                             sidebar_tick, turn_banner],
                ).then(
                    stream_reply,
                    inputs=[sessions_st, current_tid_st],
                    outputs=[chatbot, debug_md],
                ).then(
                    lambda uid, tid: _turn_banner(tid, uid),
                    inputs=[user_id_st, current_tid_st],
                    outputs=[turn_banner],
                )

                send_click = send_btn.click(
                    prepare_send,
                    inputs=[sessions_st, user_id_st, current_tid_st, msg_box, sidebar_tick],
                    outputs=[sessions_st, current_tid_st, chatbot, msg_box,
                             sidebar_tick, turn_banner],
                ).then(
                    stream_reply,
                    inputs=[sessions_st, current_tid_st],
                    outputs=[chatbot, debug_md],
                ).then(
                    lambda uid, tid: _turn_banner(tid, uid),
                    inputs=[user_id_st, current_tid_st],
                    outputs=[turn_banner],
                )

                # 新建会话
                new_btn.click(
                    new_session,
                    inputs=[sessions_st, user_id_st, sidebar_tick],
                    outputs=[sessions_st, current_tid_st, chatbot, debug_md,
                             turn_banner, sidebar_tick],
                )

                # 重命名确认
                rename_confirm_btn.click(
                    confirm_rename,
                    inputs=[sessions_st, user_id_st, current_tid_st, rename_input, sidebar_tick],
                    outputs=[sessions_st, sidebar_tick, rename_modal],
                )

                # 重命名取消
                rename_cancel_btn.click(
                    lambda: gr.update(visible=False),
                    outputs=[rename_modal],
                )

                # 首次加载
                demo.load(
                    init_state,
                    outputs=[sessions_st, current_tid_st, user_id_st, chatbot,
                             turn_banner, sidebar_tick],
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
