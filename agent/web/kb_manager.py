"""知识库管理 Web 模块（Gradio Tab）。

作为 app.py 的第 7 个 Tab，提供:
  1. 文档上传 — PDF/Word/TXT/Markdown，分块策略 + 固定分块参数（大小/重叠）
  2. 检索测试 — 可选检索模式、召回数、相似度阈值
  3. 文档列表 — 已上传文档概览
  4. 查看/编辑内容 — 列出分块，支持逐块修改并重新向量化
  5. 删除文档 / 清空知识库
  6. 统计信息 — 文档数 / 向量数

参考 dify 等平台优秀设计，暴露核心参数供调优。
KB Service 延迟加载：首次操作才初始化；模型进一步延迟到真正向量化/重排时才加载，
故纯数据库操作（统计/列表/删除/清空/查看）不会触发模型加载。
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

# 分类改为文本框自由输入，这里仅作为占位提示与检索过滤的常用选项参考
COMMON_CATEGORIES = "安全规定 / 操作规程 / 应急预案 / 管理制度 / 其他"

_kb = None


def _get_kb():
    """延迟获取 KB Service 单例"""
    global _kb
    if _kb is None:
        from skills.kb.skill import get_kb_service
        _kb = get_kb_service()
    return _kb


def kb_upload(file, title, category, chunk_strategy, chunk_size, chunk_overlap, custom_sep):
    if file is None:
        return "❌ 请先选择文件"
    try:
        from skills.kb import ChunkStrategy
        kb = _get_kb()
        path = file.name
        strategy = ChunkStrategy(chunk_strategy)
        kwargs = {}
        # 按策略传参
        if strategy == ChunkStrategy.FIXED_SIZE:
            kwargs["chunk_size"] = int(chunk_size)
            kwargs["chunk_overlap"] = int(chunk_overlap)
        elif strategy == ChunkStrategy.BY_SEPARATOR:
            sep = (custom_sep or "").strip()
            if not sep:
                return "❌ 选择'按特殊标记符分割'时，必须输入分隔符（如 ****）"
            kwargs["custom_separator"] = sep
        r = kb.upload_document(
            file_path=path,
            metadata={
                "title": title.strip() or Path(path).stem,
                "category": (category or "").strip() or "其他",
                "filename": Path(path).name,
            },
            chunk_strategy=strategy,
            **kwargs,
        )
        extra = ""
        if strategy == ChunkStrategy.FIXED_SIZE:
            extra = f"\n分块大小: {int(chunk_size)} 字 | 重叠: {int(chunk_overlap)} 字"
        elif strategy == ChunkStrategy.BY_SEPARATOR:
            extra = f"\n分隔符: {repr(sep)}"
        return f"✅ 上传成功\n文档ID: {r['doc_id']}\n分块数: {r['chunks_count']}\n分块策略: {chunk_strategy}{extra}"
    except Exception as e:
        return f"❌ 上传失败: {e}"


def kb_search(query, top_k, category, retrieval_mode, score_threshold):
    if not query or not query.strip():
        return "请输入查询内容"
    try:
        from skills.kb import RetrievalMode
        kb = _get_kb()
        cat = (category or "").strip() or None
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
        lines = ["| 标题 | 分类 | 分块数 | 文档ID |", "|---|---|---|---|"]
        for d in docs:
            lines.append(f"| {d['title']} | {d['category']} | {d['chunks_count']} | `{d['doc_id']}` |")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ 获取列表失败: {e}"


def kb_load_chunks(doc_id):
    """加载某文档的所有分块，填充编辑下拉框。

    返回: (下拉框 update, point_id→text 映射 state, 状态提示, 清空编辑框)
    """
    if not doc_id or not doc_id.strip():
        return gr.update(choices=[], value=None), {}, "❌ 请输入文档ID", ""
    try:
        kb = _get_kb()
        chunks = kb.get_document_chunks(doc_id.strip())
        if not chunks:
            return gr.update(choices=[], value=None), {}, "❌ 文档不存在或无内容", ""
        mapping = {}
        choices = []
        for ch in chunks:
            pid = ch["point_id"]
            preview = ch["text"][:40].replace("\n", " ")
            label = f"分块 {ch['chunk_index']}: {preview}…"
            choices.append((label, pid))
            mapping[pid] = ch["text"]
        title = chunks[0]["title"]
        return (
            gr.update(choices=choices, value=choices[0][1]),
            mapping,
            f"✅ 已加载《{title}》共 {len(chunks)} 个分块，选择某块后可编辑保存",
            mapping[choices[0][1]],
        )
    except Exception as e:
        return gr.update(choices=[], value=None), {}, f"❌ 加载失败: {e}", ""


def kb_pick_chunk(point_id, mapping):
    """选中某分块 → 把其文本填入编辑框"""
    if not point_id or not mapping:
        return ""
    return mapping.get(point_id, "")


def kb_save_chunk(point_id, new_text, mapping):
    """保存分块修改（重新向量化）"""
    if not point_id:
        return "❌ 请先选择要修改的分块", mapping
    if not new_text or not new_text.strip():
        return "❌ 分块内容不能为空", mapping
    try:
        kb = _get_kb()
        kb.update_chunk(point_id, new_text.strip())
        if isinstance(mapping, dict):
            mapping[point_id] = new_text.strip()
        return f"✅ 已保存分块 `{point_id}` 并重新向量化", mapping
    except Exception as e:
        return f"❌ 保存失败: {e}", mapping


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
        gr.Markdown(
            "**规章制度知识库**：上传规章文档（PDF/Word/Markdown/TXT），供智能体检索引用条文。"
            "参考 dify 设计，暴露分块与检索参数供调优。"
        )

        with gr.Row():
            # ---------------- 左列：上传 + 删除/清空 ----------------
            with gr.Column(scale=1):
                gr.Markdown("### 📤 文档上传")
                file_in = gr.File(label="选择文件", file_types=[".pdf", ".docx", ".doc", ".txt", ".md"])
                title_in = gr.Textbox(label="文档标题", placeholder="留空则用文件名")
                cat_in = gr.Textbox(
                    label="分类",
                    placeholder=f"自由输入，常用: {COMMON_CATEGORIES}",
                    value="安全规定",
                    info="自定义分类名，检索时可按此过滤",
                )
                chunk_strategy_in = gr.Dropdown(
                    choices=["fixed_size", "by_paragraph", "by_title", "by_separator"],
                    value="fixed_size",
                    label="分块策略",
                    info="fixed_size=固定字数 | by_paragraph=按段落 | by_title=按标题 | by_separator=按特殊标记符",
                )
                custom_sep_in = gr.Textbox(
                    label="特殊分隔符",
                    placeholder="仅 by_separator 策略生效，如: ****",
                    value="",
                    info="选择'按特殊标记符分割'时必填，文档中出现此标记即分块",
                )
                with gr.Row():
                    chunk_size_in = gr.Number(
                        value=300, precision=0, label="分块大小(字)",
                        info="仅 fixed_size 生效。推荐: 条文密集 300 / 一般 500 / 长段落 800",
                    )
                    chunk_overlap_in = gr.Number(
                        value=50, precision=0, label="重叠字数",
                        info="仅 fixed_size 生效。相邻块叠加，避免语义被硬切断。推荐: 块大小的 10%-20%（如 300→50）",
                    )
                upload_btn = gr.Button("上传", variant="primary")
                upload_out = gr.Textbox(label="上传结果", lines=5)

                gr.Markdown("### 🗑️ 删除/清空")
                del_in = gr.Textbox(label="文档ID", placeholder="粘贴完整 doc_id")
                with gr.Row():
                    del_btn = gr.Button("删除文档", variant="stop")
                    clear_btn = gr.Button("清空知识库", variant="stop")
                del_out = gr.Textbox(label="操作结果")

            # ---------------- 右列：检索测试 ----------------
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
                    catf_in = gr.Textbox(label="分类过滤", placeholder="留空=全部，或输入分类名", value="")
                    thresh_in = gr.Textbox(value="0.5", label="相似度阈值", info="0.5-0.7，低了不相关内容会出现")
                search_btn = gr.Button("检索", variant="primary")
                search_out = gr.Markdown()

        gr.Markdown("---")

        # ---------------- 文档列表 + 统计 ----------------
        with gr.Row():
            with gr.Column():
                gr.Markdown("### 📋 文档列表")
                list_btn = gr.Button("刷新列表")
                list_out = gr.Markdown()
            with gr.Column():
                gr.Markdown("### 📊 统计")
                stats_btn = gr.Button("刷新统计")
                stats_out = gr.Markdown()

        gr.Markdown("---")

        # ---------------- 查看/编辑内容 ----------------
        gr.Markdown("### 📝 查看 / 编辑内容")
        gr.Markdown("输入文档ID加载分块，选择某块即可编辑文本，保存后会自动重新向量化。")
        chunk_state = gr.State({})
        with gr.Row():
            view_id_in = gr.Textbox(label="文档ID", placeholder="从列表复制 doc_id", scale=3)
            load_btn = gr.Button("加载分块", variant="primary", scale=1)
        load_status = gr.Markdown()
        with gr.Row():
            chunk_select = gr.Dropdown(label="选择分块", choices=[], interactive=True, scale=2)
        chunk_edit = gr.Textbox(label="分块内容（可编辑）", lines=8)
        with gr.Row():
            save_chunk_btn = gr.Button("保存修改", variant="primary")
        save_status = gr.Markdown()

        # ---------------- 事件绑定 ----------------
        upload_btn.click(
            kb_upload,
            [file_in, title_in, cat_in, chunk_strategy_in, chunk_size_in, chunk_overlap_in, custom_sep_in],
            upload_out,
        )
        del_btn.click(kb_delete, del_in, del_out)
        clear_btn.click(kb_clear_all, None, del_out)
        search_btn.click(kb_search, [q_in, topk_in, catf_in, mode_in, thresh_in], search_out)
        list_btn.click(kb_list, None, list_out)
        stats_btn.click(kb_stats, None, stats_out)

        load_btn.click(
            kb_load_chunks, view_id_in,
            [chunk_select, chunk_state, load_status, chunk_edit],
        )
        chunk_select.change(kb_pick_chunk, [chunk_select, chunk_state], chunk_edit)
        save_chunk_btn.click(
            kb_save_chunk, [chunk_select, chunk_edit, chunk_state],
            [save_status, chunk_state],
        )
