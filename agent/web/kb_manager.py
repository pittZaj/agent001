"""知识库管理 Web 模块（Gradio Tab）。

作为 app.py 的第 7 个 Tab，提供:
  1. 文档上传 — PDF/Word/TXT，填写标题与分类
  2. 检索测试 — 输入查询词预览召回+重排结果
  3. 文档列表 — 已上传文档概览
  4. 删除文档 — 按 doc_id 删除
  5. 统计信息 — 文档数 / 向量数

KB Service 延迟加载：首次操作才初始化模型，避免 Web 启动即占显存。
"""
from __future__ import annotations

import sys
from pathlib import Path

import gradio as gr

# RAG skills 位于 LangGraph 根目录（agent/），而本 Web 应用根目录是 agent/agent/，
# 故需把 LangGraph 根加入 sys.path 才能 import skills.kb
_LANGGRAPH_ROOT = Path(__file__).resolve().parents[2]
if str(_LANGGRAPH_ROOT) not in sys.path:
    sys.path.insert(0, str(_LANGGRAPH_ROOT))

CATEGORIES = ["安全规定", "操作规程", "应急预案", "管理制度", "其他"]

_kb = None


def _get_kb():
    """延迟获取 KB Service 单例"""
    global _kb
    if _kb is None:
        from skills.kb.skill import get_kb_service
        _kb = get_kb_service()
    return _kb


def kb_upload(file, title, category):
    if file is None:
        return "❌ 请先选择文件"
    try:
        kb = _get_kb()
        path = file.name
        r = kb.upload_document(
            file_path=path,
            metadata={
                "title": title.strip() or Path(path).stem,
                "category": category or "其他",
                "filename": Path(path).name,
            },
        )
        return f"✅ 上传成功\n文档ID: {r['doc_id']}\n分块数: {r['chunks_count']}"
    except Exception as e:
        return f"❌ 上传失败: {e}"


def kb_search(query, top_k, category):
    if not query or not query.strip():
        return "请输入查询内容"
    try:
        kb = _get_kb()
        cat = None if category == "全部" else category
        results = kb.search(query.strip(), top_k=int(top_k), category=cat)
        if not results:
            return "未找到相关结果"
        out = []
        for i, r in enumerate(results, 1):
            out.append(f"### 结果 {i}（相关度 {r['score']:.4f}）")
            out.append(f"**来源**: {r['title']} ｜ 分类: {r['category']}")
            out.append(f"\n> {r['text']}\n")
        return "\n".join(out)
    except Exception as e:
        return f"❌ 检索失败: {e}"


def kb_list():
    try:
        kb = _get_kb()
        docs = kb.list_documents()
        if not docs:
            return "暂无文档"
        lines = ["| 标题 | 分类 | 分块数 | 文档ID |", "|---|---|---|---|"]
        for d in docs:
            lines.append(f"| {d['title']} | {d['category']} | {d['chunks_count']} | `{d['doc_id']}` |")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ 获取列表失败: {e}"


def kb_delete(doc_id):
    if not doc_id or not doc_id.strip():
        return "❌ 请输入文档ID"
    try:
        kb = _get_kb()
        n = kb.delete_document(doc_id.strip())
        return f"✅ 已删除，移除 {n} 个分块"
    except Exception as e:
        return f"❌ 删除失败: {e}"


def kb_stats():
    try:
        kb = _get_kb()
        s = kb.get_stats()
        return (f"📊 知识库统计\n\n"
                f"- 文档总数: **{s['total_documents']}**\n"
                f"- 向量总数: **{s['total_vectors']}**\n"
                f"- Collection: `{s['collection_name']}`")
    except Exception as e:
        return f"❌ 获取统计失败: {e}"


def build_kb_tab():
    """构建知识库管理 Tab（在 app.py 的 gr.Tabs() 内调用）"""
    with gr.Tab("7. 知识库管理"):
        gr.Markdown("**规章制度知识库**：上传安全规章文档，供智能体检索引用条文。")

        with gr.Row():
            with gr.Column(scale=1):
                gr.Markdown("### 📤 文档上传")
                file_in = gr.File(label="选择文件", file_types=[".pdf", ".docx", ".doc", ".txt"])
                title_in = gr.Textbox(label="文档标题", placeholder="留空则用文件名")
                cat_in = gr.Dropdown(choices=CATEGORIES, value="安全规定", label="分类")
                upload_btn = gr.Button("上传", variant="primary")
                upload_out = gr.Textbox(label="上传结果", lines=3)

                gr.Markdown("### 🗑️ 删除文档")
                del_in = gr.Textbox(label="文档ID", placeholder="粘贴完整 doc_id")
                del_btn = gr.Button("删除", variant="stop")
                del_out = gr.Textbox(label="删除结果")

            with gr.Column(scale=2):
                gr.Markdown("### 🔍 检索测试")
                q_in = gr.Textbox(label="查询内容", placeholder="例：未戴安全帽违反哪些规定？")
                with gr.Row():
                    topk_in = gr.Slider(1, 10, value=5, step=1, label="返回数量")
                    catf_in = gr.Dropdown(choices=["全部"] + CATEGORIES, value="全部", label="分类过滤")
                search_btn = gr.Button("检索", variant="primary")
                search_out = gr.Markdown()

        gr.Markdown("---")
        with gr.Row():
            with gr.Column():
                gr.Markdown("### 📋 文档列表")
                list_btn = gr.Button("刷新列表")
                list_out = gr.Markdown()
            with gr.Column():
                gr.Markdown("### 📊 统计")
                stats_btn = gr.Button("刷新统计")
                stats_out = gr.Markdown()

        upload_btn.click(kb_upload, [file_in, title_in, cat_in], upload_out)
        del_btn.click(kb_delete, del_in, del_out)
        search_btn.click(kb_search, [q_in, topk_in, catf_in], search_out)
        list_btn.click(kb_list, None, list_out)
        stats_btn.click(kb_stats, None, stats_out)
