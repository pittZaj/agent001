"""Agent-of-Agent 任务管理 Web 控制台（Gradio）。

6 个 Tab:
  1. 新建任务 — 表单 + 真实候选项提示, 提交后自动轮询日志
  2. 任务列表 — 所有 job 概览
  3. 任务详情 — 看 log + 产出
  4. 归档发布 — 把通过的 job 注册到 FastAPI
  5. 数据库浏览 / CRUD — 浏览/查询/增删改, 含一键重置
  6. Agent 对话测试 — 选已发布 Agent → ChatGPT 风格调测

启动:
  conda activate agent
  bash /mnt/data3/clip/LangGraph/agent/agent/web/start_web.sh
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import gradio as gr

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from web import agent_chat, db_admin, job_manager, spec_helper  # noqa: E402
from registry import list_agents, publish, unpublish  # noqa: E402

ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"
LOGS_DIR = PROJECT_ROOT / "logs" / "jobs"
TEMPLATES_DIR = PROJECT_ROOT / "templates"
TEMPLATE_PATH = TEMPLATES_DIR / "AGENT_SPEC_TEMPLATE.md"


# ============================================================
# Tab 1: 新建任务
# ============================================================
def _form_to_spec_md(*, name, version, owner, business_goal, scenarios_text,
                    tools_text, db_engine, db_path, table_list, knowledge_base,
                    test_cases_text, target_score, max_iter,
                    max_input_tokens, max_output_tokens) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    scenarios = [l.strip() for l in (scenarios_text or "").splitlines() if l.strip()]
    scenarios_md = "\n".join(f"- {s}" for s in scenarios) or "- (待填写)"

    md = []
    md.append(f"# Agent Spec: {name}")
    md.append("")
    md.append("## 1. 元数据")
    md.append(f"- name: {name}")
    md.append(f"- version: {version}")
    md.append(f"- owner: {owner}")
    md.append(f"- created_at: {today}")
    md.append("")
    md.append("## 2. 业务目标")
    md.append(business_goal or "(待填写)")
    md.append("")
    md.append("## 3. 用户场景")
    md.append(scenarios_md)
    md.append("")
    md.append("## 4. 可用工具")
    md.append("| name | description | parameters | data_source |")
    md.append("|---|---|---|---|")
    for line in (tools_text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        md.append(line if line.startswith("|") else f"| {line} |")
    md.append("")
    md.append("## 5. 数据访问")
    md.append(f"- 数据库: {db_engine}")
    md.append(f"- 路径: {db_path}")
    md.append(f"- 表: {table_list}")
    md.append("- 只读: true")
    md.append("")
    md.append("## 6. 知识库")
    md.append(knowledge_base or "暂未支持，后续 RAG 阶段填充。")
    md.append("")
    md.append("## 7. 测试用例")
    md.append("| input | expected_tool | expected_args_contains | expected_output_contains |")
    md.append("|---|---|---|---|")
    for line in (test_cases_text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        md.append(line if line.startswith("|") else f"| {line} |")
    md.append("")
    md.append("## 8. 验收指标")
    md.append("- tool_accuracy >= 0.8")
    md.append("- execution_success >= 0.9")
    md.append(f"- overall_score >= {target_score}")
    md.append("")
    md.append("## 9. Token 预算")
    md.append(f"- max_iterations: {max_iter}")
    md.append(f"- max_input_tokens: {max_input_tokens}")
    md.append(f"- max_output_tokens: {max_output_tokens}")
    return "\n".join(md) + "\n"


def _parse_pid_jobid(stdout: str) -> tuple[str | None, int | None]:
    job_id = None
    pid = None
    for line in stdout.splitlines():
        if line.startswith("job_id="):
            job_id = line.split("=", 1)[1].strip()
        elif line.startswith("pid="):
            try:
                pid = int(line.split("=", 1)[1].strip())
            except ValueError:
                pass
    return job_id, pid


def submit_task(name, version, owner, business_goal, scenarios_text, tools_text,
                db_engine, db_path, table_list, knowledge_base, test_cases_text,
                target_score, max_iter, max_input_tokens, max_output_tokens,
                dry_run, anthropic_token):
    if not name or not name.replace("_", "").isalnum():
        return ("❌ name 必填且只能含字母/数字/下划线", "", "", "", "")

    spec_md = _form_to_spec_md(
        name=name, version=version, owner=owner, business_goal=business_goal,
        scenarios_text=scenarios_text, tools_text=tools_text,
        db_engine=db_engine, db_path=db_path, table_list=table_list,
        knowledge_base=knowledge_base, test_cases_text=test_cases_text,
        target_score=target_score, max_iter=max_iter,
        max_input_tokens=max_input_tokens, max_output_tokens=max_output_tokens,
    )

    spec_dir = PROJECT_ROOT / "specs"
    spec_dir.mkdir(exist_ok=True)
    spec_path = spec_dir / f"{name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    spec_path.write_text(spec_md, encoding="utf-8")

    extra = ["--dry-run"] if dry_run else []
    cmd = ["bash", str(PROJECT_ROOT / "autoctl.sh"), "start", str(spec_path)] + extra

    env = os.environ.copy()
    if anthropic_token and not dry_run:
        env["ANTHROPIC_AUTH_TOKEN"] = anthropic_token.strip()

    result = subprocess.run(cmd, capture_output=True, text=True, env=env,
                            cwd=str(PROJECT_ROOT))
    if result.returncode != 0:
        return (f"❌ 启动失败:\n{result.stderr}\n{result.stdout}",
                "", spec_md, str(spec_path), "")

    job_id, pid = _parse_pid_jobid(result.stdout)
    if not job_id:
        return (f"❌ 未解析到 job_id:\n{result.stdout}",
                "", spec_md, str(spec_path), "")

    job_manager.insert_job(
        job_id=job_id, agent_name=name, spec_path=str(spec_path),
        pid=pid, log_path=str(LOGS_DIR / job_id / "run.log"),
        artifacts_dir=str(ARTIFACTS_DIR / job_id), status="running",
        notes="dry-run" if dry_run else "",
    )
    msg = (f"✅ 已提交 job_id=`{job_id}` (pid={pid})\n\n"
           f"日志会自动每 3 秒刷新一次。任务完成后会自动停止。")
    return (msg, job_id, spec_md, str(spec_path),
            "⏳ 任务已启动，等待 3 秒后开始拉取日志…")


# ============================================================
# 通用：日志读取 + 状态摘要
# ============================================================
def _tail_text(path: Path, n: int) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"(read failed: {e})"
    lines = text.splitlines()
    return "\n".join(lines[-n:])


def _read_log(job_id: str, n_lines: int = 200) -> str:
    if not job_id:
        return ""
    log = LOGS_DIR / job_id / "run.log"
    pipeline = ARTIFACTS_DIR / job_id / "pipeline.log"
    parts = []
    if pipeline.exists():
        parts.append("=== pipeline.log ===\n" + _tail_text(pipeline, n_lines))
    if log.exists():
        parts.append("=== run.log ===\n" + _tail_text(log, n_lines))
    return "\n\n".join(parts) or "(no log yet)"


def _job_status_short(job_id: str) -> str:
    """给 Tab1 提交后的轮询用：一行状态。"""
    if not job_id:
        return "(no job)"
    j = job_manager.refresh_from_artifacts(job_id, ARTIFACTS_DIR) \
        or job_manager.get_job(job_id)
    if not j:
        return f"❓ 未找到 {job_id}"
    status = j.get("status", "?")
    score = j.get("score")
    score_str = f"score={score}" if score is not None else ""
    icon = {"running": "⏳", "success": "✅", "failed": "❌"}.get(status, "•")
    return f"{icon} 状态: {status}  {score_str}"


def poll_job_log(job_id: str):
    """Tab1 提交后每 3s 调这个：返回 (短状态, 日志全文)。"""
    if not job_id:
        return "(尚未提交任务)", ""
    return _job_status_short(job_id), _read_log(job_id, 200)


def _refresh_jobs_table():
    rows = []
    for j in job_manager.list_jobs(200):
        latest = job_manager.refresh_from_artifacts(j["job_id"], ARTIFACTS_DIR) or j
        rows.append([
            latest["job_id"], latest["agent_name"], latest["status"],
            f'{latest.get("score") or ""}',
            datetime.fromtimestamp(latest["created_at"]).strftime("%Y-%m-%d %H:%M:%S"),
            latest.get("notes", ""),
        ])
    return rows


def _job_summary(job_id: str) -> tuple[str, str, str]:
    if not job_id:
        return "", "", ""
    j = job_manager.refresh_from_artifacts(job_id, ARTIFACTS_DIR) \
        or job_manager.get_job(job_id) \
        or {}
    summary_lines = [
        f"job_id: {j.get('job_id')}",
        f"agent : {j.get('agent_name')}",
        f"status: {j.get('status')}",
        f"score : {j.get('score')}",
        f"created : {datetime.fromtimestamp(j['created_at']).isoformat() if j.get('created_at') else ''}",
        f"finished: {datetime.fromtimestamp(j['finished_at']).isoformat() if j.get('finished_at') else ''}",
    ]
    register = j.get("register_json") or "{}"
    return "\n".join(summary_lines), register, _read_log(job_id, 200)


# ============================================================
# Tab 4: 发布
# ============================================================
def _list_published():
    rows = []
    for name, info in list_agents().items():
        rows.append([
            name, info.get("version"),
            info.get("score"),
            info.get("route"),
            info.get("source_job_id"),
        ])
    return rows


def publish_job(job_id: str, force: bool):
    if not job_id:
        return "❌ 请输入 job_id", _list_published()
    try:
        info = publish(job_id, force=force)
    except Exception as e:
        return f"❌ {type(e).__name__}: {e}", _list_published()
    return (f"✅ 已发布: {json.dumps(info, ensure_ascii=False, indent=2)}\n\n"
            f"如果要让 FastAPI 路由生效，请重启 agent/main.py。\n"
            f"Web 控制台 Tab 6 会立即可用，无需重启。"), _list_published()


def unpublish_action(name: str):
    if not name:
        return "❌ 请输入 name", _list_published()
    ok = unpublish(name)
    return (f"{'✅' if ok else '❌'} unpublish={ok}", _list_published())


# ============================================================
# Tab 5: 数据库 CRUD
# ============================================================
def db_table_list():
    return db_admin.list_tables()


def db_describe(table: str) -> str:
    if not table:
        return ""
    cols = db_admin.get_columns(table)
    pk = ", ".join(c["name"] for c in cols if c["pk"]) or "(none)"
    lines = [f"**表**: `{table}`  ·  **主键**: `{pk}`", ""]
    lines.append("| 列 | 类型 | 非空 | 默认 | PK |")
    lines.append("|---|---|---|---|---|")
    for c in cols:
        lines.append(f"| `{c['name']}` | {c['type']} | {c['notnull']} | {c['default']} | {c['pk']} |")
    return "\n".join(lines)


def db_query(table: str, where: str, limit: int, offset: int):
    if not table:
        return [[]], "请选择表"
    try:
        cols, rows, total = db_admin.query(table, where=where or "",
                                           limit=int(limit), offset=int(offset))
    except Exception as e:
        return [[]], f"❌ {type(e).__name__}: {e}"
    msg = f"共 {total} 行匹配，返回 {len(rows)} 行（offset={offset}）"
    return [cols] + rows if rows else [cols], msg


def _parse_kv_text(text: str) -> dict:
    """解析"key=value" 一行一条；value 是 'NULL' 视为 None；裸数字保持字符串（SQLite 不挑）。"""
    out: dict = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()
        if v.upper() == "NULL":
            out[k] = None
        else:
            out[k] = v
    return out


def db_insert(table: str, kv_text: str):
    if not table:
        return "❌ 请选择表"
    row = _parse_kv_text(kv_text)
    if not row:
        return "❌ 至少填写一行 key=value"
    try:
        out = db_admin.insert(table, row)
    except Exception as e:
        return f"❌ {type(e).__name__}: {e}"
    return f"✅ inserted: {json.dumps(out, ensure_ascii=False)}"


def db_update(table: str, key_text: str, val_text: str):
    if not table:
        return "❌ 请选择表"
    key = _parse_kv_text(key_text)
    values = _parse_kv_text(val_text)
    if not key or not values:
        return "❌ 主键 key 和待修改 values 都不能空"
    try:
        out = db_admin.update(table, key=key, values=values)
    except Exception as e:
        return f"❌ {type(e).__name__}: {e}"
    return f"✅ updated: {json.dumps(out, ensure_ascii=False)}"


def db_delete(table: str, key_text: str):
    if not table:
        return "❌ 请选择表"
    key = _parse_kv_text(key_text)
    if not key:
        return "❌ 主键 key 不能空"
    try:
        out = db_admin.delete(table, key=key)
    except Exception as e:
        return f"❌ {type(e).__name__}: {e}"
    return f"✅ deleted: {json.dumps(out, ensure_ascii=False)}"


def db_reset():
    out = db_admin.reset_database()
    return f"```\n{out}\n```"


def db_stats_text() -> str:
    rows = db_admin.stats()
    parts = ["| 表 | 行数 |", "|---|---|"]
    for t, n in rows:
        parts.append(f"| `{t}` | {n} |")
    return "\n".join(parts)


# ============================================================
# Tab 6: Agent 对话测试
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
    template_text = TEMPLATE_PATH.read_text(encoding="utf-8") if TEMPLATE_PATH.exists() else ""
    help_md = spec_helper.render_help_markdown()
    tools_default = spec_helper.example_tools_table_row()
    tests_default = spec_helper.example_test_cases()
    db_tables = db_admin.list_tables()
    published_now = agent_chat.published_agent_names()

    with gr.Blocks(title="Agent-of-Agent 控制台", theme=gr.themes.Soft()) as demo:
        gr.Markdown("# Agent-of-Agent 控制台")
        gr.Markdown(
            "**核心流程**：填写智能体描述 → 后台跑流水线（自动轮询日志） → 一键发布到 FastAPI → 在 Tab 6 直接对话测试。"
            "默认基座 `Qwen3-VL-4B-Instruct-FP8` (vLLM 8004)，元智能体走 Claude (IMDS)。"
        )

        with gr.Tabs():
            # ----- Tab 1 -----
            with gr.Tab("1. 新建任务"):
                with gr.Row():
                    with gr.Column(scale=1):
                        gr.Markdown("### 填写指南（基于当前真实可用工具/表/枚举）")
                        gr.Markdown(value=help_md)
                        with gr.Accordion("📄 完整模板（参考 templates/AGENT_SPEC_TEMPLATE.md）", open=False):
                            gr.Markdown(value=template_text)
                    with gr.Column(scale=1):
                        gr.Markdown("### 任务表单")
                        name = gr.Textbox(label="name (蛇形小写)", value="alarm_query_agent_v2")
                        version = gr.Textbox(label="version", value="0.1.0")
                        owner = gr.Textbox(label="owner", value="")
                        business_goal = gr.Textbox(
                            label="业务目标 (一句话, ≤50字)",
                            value="查询安全生产平台的告警记录，按日期/类型/摄像头筛选并给出聚合统计。",
                            lines=2,
                        )
                        scenarios_text = gr.Textbox(
                            label="用户场景（每行一条, 至少 3 条）", lines=4,
                            value=("场景1: 用户问'今天的告警', Agent 调用 query_alarms\n"
                                   "场景2: 用户提到具体日期+类型, Agent 提取 date 与 alarm_type\n"
                                   "场景3: 用户用别名(抽烟->smoking), Agent 完成中英映射"),
                        )
                        tools_text = gr.Textbox(
                            label="工具表格行（每行一条, 直接复制左侧规则的占位）",
                            lines=3,
                            value=tools_default,
                        )
                        with gr.Row():
                            db_engine = gr.Dropdown(["sqlite", "mysql", "none"],
                                                    value="sqlite", label="数据库")
                            db_path = gr.Textbox(label="路径", value="data/ksipms_dev.db")
                        table_list = gr.Textbox(label="使用的表（逗号分隔）", value="alarms")
                        knowledge_base = gr.Textbox(
                            label="知识库（当前未启用 RAG，默认填占位）",
                            value="暂未支持，后续 RAG 阶段填充。", lines=1,
                        )
                        test_cases_text = gr.Textbox(
                            label="测试用例表格行（每行一条）", lines=5,
                            value=tests_default,
                        )
                        with gr.Row():
                            target_score = gr.Slider(0.5, 1.0, value=0.7, step=0.05,
                                                     label="overall_score 阈值")
                            max_iter = gr.Slider(1, 5, value=1, step=1, label="最大迭代")
                        with gr.Row():
                            max_in_tok = gr.Number(value=50000, label="max_input_tokens")
                            max_out_tok = gr.Number(value=20000, label="max_output_tokens")
                        with gr.Row():
                            dry_run = gr.Checkbox(label="dry-run（不调 Claude）", value=False)
                            anthropic_token = gr.Textbox(
                                label="ANTHROPIC_AUTH_TOKEN（留空用环境变量）",
                                type="password",
                            )
                        submit_btn = gr.Button("一键发布任务", variant="primary")

                gr.Markdown("---")
                gr.Markdown("### 提交结果与实时进度（自动每 3 秒刷新一次）")
                result_msg = gr.Markdown(label="提交结果")
                new_job_id = gr.Textbox(label="新 job_id", interactive=True,
                                        info="任务 ID。Tab 3 / 4 中会用到。手动改可切到其他 job 继续看日志。")
                status_short = gr.Markdown(label="状态")
                live_log = gr.Textbox(label="实时日志（最后 200 行）", lines=20,
                                      interactive=False)

                with gr.Accordion("查看本次提交生成的 spec.md", open=False):
                    spec_preview = gr.Textbox(label="spec.md 预览", lines=12)
                    spec_path_box = gr.Textbox(label="spec.md 落盘路径", interactive=False)

                submit_btn.click(
                    submit_task,
                    inputs=[name, version, owner, business_goal, scenarios_text, tools_text,
                            db_engine, db_path, table_list, knowledge_base, test_cases_text,
                            target_score, max_iter, max_in_tok, max_out_tok,
                            dry_run, anthropic_token],
                    outputs=[result_msg, new_job_id, spec_preview, spec_path_box,
                             status_short],
                )

                # 自动轮询：every 3s 调 poll_job_log，输入是 new_job_id 的当前值
                log_timer = gr.Timer(value=3.0, active=True)
                log_timer.tick(poll_job_log, inputs=new_job_id,
                               outputs=[status_short, live_log])

            # ----- Tab 2 -----
            with gr.Tab("2. 任务列表"):
                gr.Markdown("点击 *刷新* 重新读取所有任务（自动同步 artifacts/REGISTER.json）。")
                refresh_btn = gr.Button("刷新", variant="primary")
                jobs_table = gr.Dataframe(
                    headers=["job_id", "agent_name", "status", "score", "created", "notes"],
                    label="所有任务",
                    interactive=False,
                )
                refresh_btn.click(_refresh_jobs_table, outputs=jobs_table)
                demo.load(_refresh_jobs_table, outputs=jobs_table)

            # ----- Tab 3 -----
            with gr.Tab("3. 任务详情"):
                detail_job = gr.Textbox(label="job_id（也可从 Tab 2 复制）")
                detail_btn = gr.Button("查询", variant="primary")
                detail_summary = gr.Textbox(label="摘要", lines=6)
                detail_register = gr.Textbox(label="REGISTER.json", lines=10)
                detail_log = gr.Textbox(label="日志（最后 200 行）", lines=20)
                detail_btn.click(_job_summary, inputs=detail_job,
                                 outputs=[detail_summary, detail_register, detail_log])

            # ----- Tab 4 -----
            with gr.Tab("4. 归档发布"):
                gr.Markdown(
                    "把验收通过的任务发布到注册表 (`agent_registry.json`)。"
                    "发布后 Web Tab 6 立即可用；要让 `agent/main.py` 上的 `/agents/<name>/chat` 路由生效需重启 FastAPI。"
                )
                pub_job_id = gr.Textbox(label="job_id 待发布")
                pub_force = gr.Checkbox(label="--force（即便未达验收也发布）")
                pub_btn = gr.Button("发布", variant="primary")
                pub_result = gr.Markdown()

                gr.Markdown("### 当前已发布")
                pub_list_btn = gr.Button("刷新列表")
                pub_table = gr.Dataframe(
                    headers=["name", "version", "score", "route", "source_job_id"],
                    interactive=False,
                )
                gr.Markdown("### 撤销发布")
                with gr.Row():
                    unpub_name = gr.Textbox(label="agent name", scale=3)
                    unpub_btn = gr.Button("撤销", scale=1, variant="stop")
                unpub_result = gr.Markdown()

                pub_btn.click(publish_job, inputs=[pub_job_id, pub_force],
                              outputs=[pub_result, pub_table])
                pub_list_btn.click(_list_published, outputs=pub_table)
                unpub_btn.click(unpublish_action, inputs=unpub_name,
                                outputs=[unpub_result, pub_table])
                demo.load(_list_published, outputs=pub_table)

            # ----- Tab 5 -----
            with gr.Tab("5. 数据库浏览 / CRUD"):
                gr.Markdown(
                    "直接操作 `data/ksipms_dev.db`。`audit_log` 表只读（仅 Agent 可写）。"
                    "改坏了点 *🔄 重置数据库* 重跑 seed.py 即可（约 1 秒）。"
                )
                with gr.Row():
                    table_dd = gr.Dropdown(choices=db_tables, label="选择表",
                                           value=db_tables[0] if db_tables else None)
                    refresh_tables_btn = gr.Button("刷新表清单", scale=0)
                schema_md = gr.Markdown(label="表结构")

                gr.Markdown("### 浏览 / 查询")
                with gr.Row():
                    where = gr.Textbox(
                        label="where（可选）",
                        placeholder="例 alarm_type=smoking and severity>=4   |   "
                                    "支持 = != >= <= > < LIKE IN, 用 and 连接",
                    )
                    limit = gr.Number(value=50, label="limit", precision=0)
                    offset = gr.Number(value=0, label="offset", precision=0)
                with gr.Row():
                    query_btn = gr.Button("查询", variant="primary")
                    stats_btn = gr.Button("查看全库统计")
                query_msg = gr.Markdown()
                rows_df = gr.Dataframe(label="结果", interactive=False, wrap=True)
                stats_md = gr.Markdown()

                gr.Markdown("---")
                gr.Markdown("### 增 / 改 / 删（每行一条 `key=value`，留空 NULL 写大写 NULL）")
                with gr.Row():
                    with gr.Column():
                        gr.Markdown("**新增**")
                        ins_kv = gr.Textbox(
                            label="所有字段 key=value（每行一条）", lines=6,
                            placeholder="alarm_uuid=xxx-yyy-zzz\nalarm_type=smoking\n...",
                        )
                        ins_btn = gr.Button("插入", variant="primary")
                        ins_result = gr.Markdown()
                    with gr.Column():
                        gr.Markdown("**修改**")
                        upd_key = gr.Textbox(label="主键 key=value", lines=2,
                                             placeholder="alarm_uuid=xxx-yyy-zzz")
                        upd_val = gr.Textbox(label="待改字段 key=value", lines=4,
                                             placeholder="status=closed\nprocessed_note=已处置")
                        upd_btn = gr.Button("更新", variant="primary")
                        upd_result = gr.Markdown()
                    with gr.Column():
                        gr.Markdown("**删除**")
                        del_key = gr.Textbox(label="主键 key=value", lines=2,
                                             placeholder="alarm_uuid=xxx-yyy-zzz")
                        del_btn = gr.Button("删除", variant="stop")
                        del_result = gr.Markdown()

                gr.Markdown("---")
                with gr.Row():
                    reset_btn = gr.Button("🔄 重置数据库（重跑 data/seed.py）", variant="stop")
                reset_result = gr.Markdown()

                # 事件
                table_dd.change(db_describe, inputs=table_dd, outputs=schema_md)
                refresh_tables_btn.click(
                    lambda: gr.update(choices=db_admin.list_tables()),
                    outputs=table_dd,
                )
                query_btn.click(db_query,
                                inputs=[table_dd, where, limit, offset],
                                outputs=[rows_df, query_msg])
                stats_btn.click(db_stats_text, outputs=stats_md)
                ins_btn.click(db_insert, inputs=[table_dd, ins_kv], outputs=ins_result)
                upd_btn.click(db_update, inputs=[table_dd, upd_key, upd_val],
                              outputs=upd_result)
                del_btn.click(db_delete, inputs=[table_dd, del_key], outputs=del_result)
                reset_btn.click(db_reset, outputs=reset_result)
                if db_tables:
                    demo.load(db_describe, inputs=table_dd, outputs=schema_md)

            # ----- Tab 6 -----
            with gr.Tab("6. Agent 对话测试"):
                gr.Markdown(
                    "**ChatGPT 风格调试已发布的 Agent**。每条消息独立调用 `agent.run`，"
                    "Agent 本身不带会话上下文（多轮对话只是 UI 展示）。"
                )
                with gr.Row():
                    agent_dd = gr.Dropdown(
                        choices=published_now,
                        value=published_now[0] if published_now else None,
                        label="选择已发布的 Agent",
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
                        label="给 Agent 发消息",
                        placeholder="例：查询2026-06-01的抽烟告警",
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
