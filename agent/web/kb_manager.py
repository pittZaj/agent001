"""知识库管理 Web 模块（Gradio Tab）。

作为 app.py 的第 7 个 Tab，提供:
  1. 文档上传 — PDF/Word/TXT/Markdown，可选分块策略
  2. 检索测试 — 可选检索模式、召回数、相似度阈值
  3. 文档列表 — 已上传文档概览，可查看/编辑内容
  4. 删除文档 / 清空知识库
  5. 统计信息 — 文档数 / 向量数

参考 dify 等平台优秀设计，暴露核心参数供调优。
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


def kb_upload(file, title, category, chunk_strategy):
    if file is None:
        return "❌ 请先选择文件"
    try:
        from skills.kb import ChunkStrategy
        kb = _get_kb()
        path = file.name
        strategy = ChunkStrategy(chunk_strategy)
        r = kb.upload_document(
            file_path=path,
            metadata={
                "title": title.strip() or Path(path).stem,
                "category": category or "其他",
                "filename": Path(path).name,
            },
            chunk_strategy=strategy,
        )
        return f"✅ 上传成功\n文档ID: {r['doc_id']}\n分块数: {r['chunks_count']}\n分块策略: {chunk_strategy}"
    except Exception as e:
        return f"❌ 上传失败: {e}"


def kb_search(query, top_k, category, retrieval_mode, score_threshold):
    if not query or not query.strip():
        return "请输入查询内容"
    try:
        from skills.kb import RetrievalMode
        kb = _get_kb()
        cat = None if category == "全部" else category
        mode = RetrievalMode(retrieval_mode)
        thresh = float(score_threshold) if score_threshold else None
        results = kb.search(
            query.strip(), top_k=int(top_k), category=cat,
            retrieval_mode=mode, score_threshold=thresh,
        )
        if not results:
            return "未找到相关结果"
        out = [f"**检索模式**: {retrieval_mode} | **相似度阈值**: {score_threshold}\n"]
        for i, r in enumerate(results, 1):
            out.append(f"### 结果 {i}（相关度 {r['score']:.4f}）")
            out.append(f"**来源**: {r['title']} ｜ 分类: {r['category']} ｜ 文档ID: `{r['doc_id']}`")
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
        lines = ["| 标题 | 分类 | 分块数 | 文档ID | 操作 |", "|---|---|---|---|---|"]
        for d in docs:
            lines.append(f"| {d['title']} | {d['category']} | {d['chunks_count']} | `{d['doc_id']}` | [查看内容](#) |")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ 获取列表失败: {e}"


def kb_view_content(doc_id):
    if not doc_id or not doc_id.strip():
        return "❌ 请输入文档ID"
    try:
        kb = _get_kb()
        chunks = kb.get_document_chunks(doc_id.strip())
        if not chunks:
            return "❌ 文档不存在或无内容"
        out = [f"**文档**: {chunks[0]['title']} | **分类**: {chunks[0]['category']} | **分块数**: {len(chunks)}\n"]
        for ch in chunks:
            out.append(f"### 分块 {ch['chunk_index']} (ID: `{ch['point_id']}`)")
            out.append(f"{ch['text']}\n")
        return "\n".join(out)
    except Exception as e:
        return f"❌ 获取内容失败: {e}"


def kb_delete(doc_id):
    if not doc_id or not doc_id.strip():
        return "❌ 请输入文档ID"
    try:
        kb = _get_kb()
        n = kb.delete_document(doc_id.strip())
        return f"✅ 已删除，移除 {n} 个分块"
    except Exception as e:
        return f"❌ 删除失败: {e}"


def kb_clear_all():
    try:
        kb = _get_kb()
        n = kb.clear_all()
        return f"✅ 已清空知识库，删除 {n} 个向量"
    except Exception as e:
        return f"❌ 清空失败: {e}"


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
        gr.Markdown("**规章制度知识库**：上传安全规章文档（PDF/Word/Markdown/TXT），供智能体检索引用条文。参考 dify 设计，暴露分块与检索参数供调优。")

        with gr.Row():
            with gr.Column(scale=1):
                gr.Markdown("### 📤 文档上传")
                file_in = gr.File(label="选择文件", file_types=[".pdf", ".docx", ".doc", ".txt", ".md"])
                title_in = gr.Textbox(label="文档标题", placeholder="留空则用文件名")
                cat_in = gr.Dropdown(choices=CATEGORIES, value="安全规定", label="分类")
                chunk_strategy_in = gr.Dropdown(
                    choices=["fixed_size", "by_paragraph", "by_title"],
                    value="fixed_size",
                    label="分块策略",
                    info="fixed_size=固定300字 | by_paragraph=按段落 | by_title=按标题层级（Markdown）",
                )
                upload_btn = gr.Button("上传", variant="primary")
                upload_out = gr.Textbox(label="上传结果", lines=4)

                gr.Markdown("### 🗑️ 删除/清空")
                del_in = gr.Textbox(label="文档ID", placeholder="粘贴完整 doc_id")
                with gr.Row():
                    del_btn = gr.Button("删除文档", variant="stop")
                    clear_btn = gr.Button("清空知识库", variant="stop")
                del_out = gr.Textbox(label="操作结果")

            with gr.Column(scale=2):
                gr.Markdown("### 🔍 检索测试")
                q_in = gr.Textbox(label="查询内容", placeholder="例：未戴安全帽违反哪些规定？")
                with gr.Row():
                    topk_in = gr.Slider(1, 10, value=5, step=1, label="召回数量", info="3-5 条为佳，太多引入噪声")
                    mode_in = gr.Dropdown(
                        choices=["hybrid", "semantic"],
                        value="hybrid",
                        label="检索模式",
                        info="hybrid=语义+关键词（效果最好）",
                    )
                with gr.Row():
                    catf_in = gr.Dropdown(choices=["全部"] + CATEGORIES, value="全部", label="分类过滤")
                    thresh_in = gr.Textbox(value="0.5", label="相似度阈值", info="0.5-0.7，低了不相关内容会出现")
                search_btn = gr.Button("检索", variant="primary")
                search_out = gr.Markdown()

        gr.Markdown("---")
        with gr.Row():
            with gr.Column():
                gr.Markdown("### 📋 文档列表")
                list_btn = gr.Button("刷新列表")
                list_out = gr.Markdown()
            with gr.Column():
                gr.Markdown("### 📄 查看内容")
                view_id_in = gr.Textbox(label="文档ID", placeholder="从列表复制 doc_id")
                view_btn = gr.Button("查看")
                view_out = gr.Markdown()
        with gr.Row():
            with gr.Column():
                gr.Markdown("### 📊 统计")
                stats_btn = gr.Button("刷新统计")
                stats_out = gr.Markdown()

        upload_btn.click(kb_upload, [file_in, title_in, cat_in, chunk_strategy_in], upload_out)
        del_btn.click(kb_delete, del_in, del_out)
        clear_btn.click(kb_clear_all, None, del_out)
        search_btn.click(kb_search, [q_in, topk_in, catf_in, mode_in, thresh_in], search_out)
        list_btn.click(kb_list, None, list_out)
        view_btn.click(kb_view_content, view_id_in, view_out)
        stats_btn.click(kb_stats, None, stats_out)
