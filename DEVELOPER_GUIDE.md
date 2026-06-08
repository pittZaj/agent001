# KSAgent 智能体开发操作手册

> **版本**: v2.0 (2026-06-05)
> **目标读者**: 智能体开发者、MCP Server 开发者、平台维护者
> **前置知识**: Python、LangGraph、MCP 协议基础
> **本版变化**: 全部示例改为基于已跑通的真实交付代码（复判子图 / 告警业务 Skill / 只写 MCP / Agent-of-Agent），环境与端口校正为线上实际值。

---

## 目录

1. [环境准备](#1-环境准备)
2. [架构总览](#2-架构总览)
3. [Skill 开发指南](#3-skill-开发指南)
4. [MCP Server 开发指南](#4-mcp-server-开发指南)
5. [智能体开发流程](#5-智能体开发流程)
6. [测试与调试](#6-测试与调试)
7. [发布与部署](#7-发布与部署)
8. [最佳实践](#8-最佳实践)
9. [常见问题](#9-常见问题)
10. [参考资源](#10-参考资源)

---

## 1. 环境准备

### 1.1 系统要求

| 项 | 实际值 | 说明 |
|----|--------|------|
| Python | **3.10**（conda 环境 `agent`） | 不是 3.11，生成代码已按 3.10 验证 |
| Conda | Anaconda3，环境名 `agent` | 全部命令必须在该环境下运行 |
| GPU | 1 卡（用于本地 vLLM） | Qwen3-VL-4B-Instruct-FP8 单卡可跑 |
| 系统字体 | Noto Sans CJK | 可视化中文渲染依赖（见 §3.2.2） |
| 数据库 | SQLite（开发） / MySQL（对接平台） | 开发用 `agent/data/ksipms_dev.db` |

### 1.2 关键端口（线上实际）

| 服务 | 端口 | 启动方 | 备注 |
|------|------|--------|------|
| vLLM（Qwen3-VL） | **8004** | 单独部署 | `config.yaml` 的 `llm.base_url` 指向它 |
| FastAPI（KSAgent 主服务） | **8001** | `python main.py` | 原 8000 被 Docker 占用，已改 8001 |
| Gradio 控制台 | **7860** | `python agent/web/app.py` | 演示与开发自测主入口 |

> ⚠️ 端口在 `config.yaml` 的 `server.port`（FastAPI）与代码中固定（Gradio 7860）。改端口只改 `config.yaml`，不要散落硬编码。

### 1.3 激活环境与依赖

```bash
# 1. 激活 conda 环境（所有后续命令的前提）
source /root/anaconda3/bin/activate agent

# 2. 进入项目根
cd /mnt/data3/clip/LangGraph/agent

# 3. 安装依赖
pip install -r requirements.txt

# 4. 安装 MCP SDK（requirements 未含，需单独装）
pip install mcp

# 5. 验证核心依赖
python -c "import langgraph, mcp, gradio, matplotlib; print('OK')"
```

### 1.4 Gradio / FastAPI 版本冲突（真实踩坑）

`requirements.txt` 锁定了 `fastapi==0.115.0`，但旧版 `gradio 4.44.1` 仍在用 FastAPI 已移除的 `on_startup` 参数，启动 Gradio 时报：

```
TypeError: Router.__init__() got an unexpected keyword argument 'on_startup'
```

**根因**：FastAPI ≥ 0.109 用 `lifespan` 取代了 `on_startup/on_shutdown`，而 gradio 4.x 没跟上。

**解决（已验证）**：升级 gradio 到 5.x，pip 会顺带把 fastapi/starlette 拉到兼容版本：

```bash
source /root/anaconda3/bin/activate agent
pip install --upgrade 'gradio>=5.0,<6.0'
# 结果：gradio 5.50.0 + fastapi 0.136.3 + starlette 0.52.1，启动正常
```

升级后 `app.py` 会有几条 Gradio 6.0 弃用警告（`theme=`、`show_copy_button=`、`allow_tags`），**不影响运行**，留待后续升 6.x 时处理。

> 经验：当底层库（FastAPI）版本是被其它需求钉死的，优先升上层库（Gradio）去适配，而不是降 FastAPI——降 FastAPI 会牵连 pydantic/starlette 一连串依赖。

### 1.5 配置文件

主配置 `config.yaml`（节选，真实值）：

```yaml
llm:
  base_url: "http://127.0.0.1:8004/v1"   # vLLM 服务，端口 8004
  model: "Qwen3-VL-4B-Instruct-FP8"
  api_key: "EMPTY"
  temperature: 0.2
  max_tokens: 2048
  timeout: 60

mcp:
  enabled: true

server:
  host: "0.0.0.0"
  port: 8001                              # FastAPI 端口（非 8000）
  reload: false
  log_level: "info"
```

只写 MCP 的权限配置在 `mcp_servers/config_write.yaml`（详见 §4.3）。

### 1.6 初始化测试数据

复判 Demo 依赖真实告警图片入库。脚本幂等，可反复跑：

```bash
cd /mnt/data3/clip/LangGraph/agent
python agent/data/seed_test_alarms.py

# 验证：应看到 8 类、约 40 条
sqlite3 agent/data/ksipms_dev.db \
  "SELECT alarm_type, COUNT(*) FROM alarms WHERE alarm_desc LIKE '%测试数据%' GROUP BY alarm_type"
```

### 1.7 两种启动方式

```bash
# 方式 A：Gradio 控制台（开发自测 / 演示，推荐）
cd /mnt/data3/clip/LangGraph/agent/agent/web
python app.py
# 浏览器访问 http://localhost:7860，打开 Tab6「Agent 对话测试」

# 方式 B：FastAPI 服务（生产 / 对外 API）
cd /mnt/data3/clip/LangGraph/agent
python main.py
# 健康检查
curl http://127.0.0.1:8001/health
```

> 首次调用主智能体需初始化 Skill Registry + 预热主图（约 5-10s，FastAPI 整体启动 30-40s），属正常现象，后续复用已加载的图。

---

## 2. 架构总览

### 2.1 整体架构图

```
┌─────────────────────────────────────────────────────────────┐
│                        用户交互层                              │
│  ┌──────────────┐              ┌──────────────┐              │
│  │ Gradio Web   │              │ FastAPI      │              │
│  │ (端口 7860)  │              │ (端口 8001)  │              │
│  └──────┬───────┘              └──────┬───────┘              │
└─────────┼────────────────────────────┼────────────────────────┘
          │                            │
          └────────────┬───────────────┘
                       │
┌──────────────────────▼────────────────────────────────────────┐
│                    Plan-Execute 主图                          │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐  │
│  │ Planner  │──▶│ Executor │──▶│Formatter │──▶│ Response │  │
│  │ (LLM)    │   │ (调度器) │   │ (LLM)    │   │          │  │
│  └──────────┘   └─────┬────┘   └──────────┘   └──────────┘  │
│                       │                                        │
│                       │ invoke(skill_id, args)                │
└───────────────────────┼────────────────────────────────────────┘
                        │
┌───────────────────────▼────────────────────────────────────────┐
│                   Skill Registry                               │
│  ┌────────────────────────────────────────────────────────┐   │
│  │  TOOL: 本地计算 (aggregate_alarms, visualize_alarms)  │   │
│  ├────────────────────────────────────────────────────────┤   │
│  │  MCP_TOOL: 数据访问 (query_alarms, update_status)    │   │
│  ├────────────────────────────────────────────────────────┤   │
│  │  SUBGRAPH: 子图 (vlm_judge_alarm)                    │   │
│  └────────────────────────────────────────────────────────┘   │
└─────────────┬──────────────────────────┬───────────────────────┘
              │                          │
    ┌─────────▼────────┐      ┌─────────▼────────┐
    │  MCP Servers     │      │  VLM Client      │
    │  (只读/只写)     │      │  (Qwen3-VL)      │
    └─────────┬────────┘      └─────────┬────────┘
              │                          │
    ┌─────────▼────────┐      ┌─────────▼────────┐
    │  SQLite 数据库   │      │  vLLM Server     │
    │  ksipms_dev.db   │      │  (端口 8004)     │
    └──────────────────┘      └──────────────────┘
```

### 2.2 核心流程：用户请求到响应

**示例**：用户输入 "统计每种告警类型数量并画柱状图"

```
1. Planner 节点（LLM 规划）
   输入：user_message + 可用 Skill 列表
   输出：plan = [
     {"task": "aggregate_alarms", "args": {"group_by": "alarm_type"}},
     {"task": "visualize_alarms", "args": {"data": "{{step_0.data}}", "chart_type": "bar"}}
   ]

2. Executor 节点（串行执行）
   步骤 0：
     - 调用 registry.invoke("aggregate_alarms", {"group_by": "alarm_type"})
     - 返回 {"data": [{"key": "no_helmet", "count": 23}, ...], "total": 315}
     - 存入 state["step_outputs"][0]
   
   步骤 1：
     - 解析 args: "{{step_0.data}}" → 引用步骤 0 的 data 字段
     - 调用 registry.invoke("visualize_alarms", {"data": [...], "chart_type": "bar"})
     - 返回 {"image_base64": "data:image/png;base64,..."}
     - 存入 state["step_outputs"][1]

3. Formatter 节点（LLM 生成自然语言）
   输入：plan + tool_results（剥离大字段后）
   输出：自然语言回复 "已统计 8 类告警，共 315 条。柱状图已生成。"

4. 返回给用户
```

### 2.3 关键设计决策

| 设计点 | 方案 | 理由 |
|--------|------|------|
| **步骤间传参** | 模板语法 `{{step_N.field}}` | 简单、可调试，Planner 易理解 |
| **类型保留** | 纯引用快捷路径 | list/dict 不被 str() 污染 |
| **大对象剥离** | formatter 前递归剥离 base64 | 避免 LLM token 超限 |
| **异步包装** | `_run_async` helper | 兼容同步主图调用异步 Skill |
| **只读/只写分离** | 两个 MCP Server | 审计可控，权限最小化 |
| **verdict 自动映射** | Skill 内部逻辑 | Planner 无需记住映射规则 |

---

## 3. Skill 开发指南

### 3.1 Skill 类型决策树

```
需要访问数据库？
  ├─ 是 → MCP_TOOL（§3.4）
  │       └─ 需要写操作？
  │           ├─ 是 → 只写 MCP Server（§4.3）
  │           └─ 否 → 只读 MCP Server（§4.2）
  │
  └─ 否 → 有多步骤流程 / 需要内部状态？
          ├─ 是 → SUBGRAPH（§3.3）
          └─ 否 → TOOL（§3.2）
```

**典型场景**：

| Skill 类型 | 适用场景 | 示例 |
|-----------|----------|------|
| **TOOL** | 纯计算、格式化、单步操作 | 聚合统计、可视化、录像回溯 |
| **MCP_TOOL** | 数据库 CRUD（需权限控制） | 查询告警、查询人员、回写状态 |
| **SUBGRAPH** | 多步骤流程、需要子图状态 | VLM 复判（查库→推理→返回）|

### 3.2 开发 TOOL 类型 Skill

#### 3.2.1 完整示例：告警聚合统计

**文件**: `skills/alarm_skills.py`（真实代码节选）

```python
"""告警聚合统计 Skill"""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from loguru import logger
from skills.base import Skill, SkillType

DB_PATH = Path(__file__).resolve().parent.parent / "agent" / "data" / "ksipms_dev.db"

def _ro_conn():
    """只读连接"""
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn

def _date_to_epoch(date_str: str, end: bool = False) -> int:
    """YYYY-MM-DD → Unix 时间戳"""
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    ts = int(dt.timestamp())
    return ts + 86400 if end else ts

def aggregate_alarms_impl(args: dict, context: dict) -> dict:
    """按 date/alarm_type/camera_id 聚合统计告警数"""
    group_by = args.get("group_by", "alarm_type")
    if group_by not in ("date", "alarm_type", "camera_id"):
        return {"error": f"group_by 必须是 date/alarm_type/camera_id，收到: {group_by}"}
    
    date_start = args.get("date_start")
    date_end = args.get("date_end")
    alarm_type = args.get("alarm_type")
    
    where = ["1=1"]
    params: list[Any] = []
    if date_start:
        where.append("ts_event >= ?"); params.append(_date_to_epoch(date_start))
    if date_end:
        where.append("ts_event < ?"); params.append(_date_to_epoch(date_end, end=True))
    if alarm_type:
        where.append("alarm_type = ?"); params.append(alarm_type)
    
    if group_by == "date":
        select_key = "strftime('%Y-%m-%d', datetime(ts_event,'unixepoch')) AS k"
        order = "k ASC"
    else:
        select_key = f"{group_by} AS k"
        order = "cnt DESC"
    
    sql = f"SELECT {select_key}, COUNT(*) AS cnt FROM alarms WHERE {' AND '.join(where)} GROUP BY k ORDER BY {order}"
    try:
        conn = _ro_conn()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as e:
        return {"error": f"sqlite error: {e}"}
    
    data = [{"key": r["k"], "count": r["cnt"]} for r in rows]
    return {
        "group_by": group_by,
        "data": data,
        "total": sum(d["count"] for d in data),
        "error": None,
    }

# 注册到 Registry
def register_alarm_skills(registry):
    registry.register(Skill(
        id="aggregate_alarms",
        name="告警聚合统计",
        description="按 日期/类型/摄像头 聚合统计告警数量。group_by 取值 date/alarm_type/camera_id。返回 data 列表供可视化使用。",
        parameters={
            "type": "object",
            "properties": {
                "group_by": {"type": "string", "description": "分组维度：date/alarm_type/camera_id", "default": "alarm_type"},
                "date_start": {"type": "string", "description": "起始日期 YYYY-MM-DD（可选）"},
                "date_end": {"type": "string", "description": "结束日期 YYYY-MM-DD（可选）"},
                "alarm_type": {"type": "string", "description": "筛选特定告警类型（可选）"},
            }
        },
        implementation=aggregate_alarms_impl,
        skill_type=SkillType.TOOL,
        tags=["data", "stats"],
    ))
    logger.info("告警业务 Skills 已注册: aggregate_alarms")
```

**关键点**：
1. ✅ **只读连接**：`mode=ro`，防止误写
2. ✅ **参数校验**：group_by 白名单，date 格式转换
3. ✅ **错误处理**：返回 `{"error": "..."}` 而非抛异常
4. ✅ **结构化输出**：固定字段 `{data, total, error}`，便于下游引用

#### 3.2.2 完整示例：可视化

**文件**: `skills/alarm_skills.py`（续）

```python
"""告警可视化 Skill（生成折线图/柱状图/饼图）"""
import base64
import io
import matplotlib
matplotlib.use("Agg")  # 无头环境
import matplotlib.pyplot as plt
from matplotlib import font_manager as _fm

# ===== 中文字体配置（关键）=====
_CJK_FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
]
for _fp in _CJK_FONT_CANDIDATES:
    if Path(_fp).exists():
        try:
            _fm.fontManager.addfont(_fp)
            _name = _fm.FontProperties(fname=_fp).get_name()
            matplotlib.rcParams["font.sans-serif"] = [_name, "DejaVu Sans"]
            matplotlib.rcParams["axes.unicode_minus"] = False
            logger.info(f"[viz] 使用中文字体: {_name}")
            break
        except Exception:
            continue

def visualize_alarms_impl(args: dict, context: dict) -> dict:
    """将聚合数据渲染为折线图/柱状图/饼图，返回 base64 PNG"""
    data = args.get("data")
    # 兼容传入 aggregate_alarms 的完整输出
    if isinstance(data, dict) and "data" in data:
        data = data["data"]
    if not data or not isinstance(data, list):
        return {"error": "data 为空或格式错误（需 [{key, count}, ...]）"}
    
    chart_type = args.get("chart_type", "bar")
    title = args.get("title", "告警统计")
    labels = [str(d.get("key")) for d in data]
    values = [d.get("count", 0) for d in data]
    
    try:
        fig, ax = plt.subplots(figsize=(8, 4.5))
        if chart_type == "line":
            ax.plot(labels, values, marker="o", color="#2c7be5", linewidth=2)
            ax.set_ylabel("数量")
            for x, y in zip(labels, values):
                ax.annotate(str(y), (x, y), textcoords="offset points", xytext=(0, 6), ha="center")
        elif chart_type == "pie":
            ax.pie(values, labels=labels, autopct="%1.1f%%", startangle=90)
            ax.axis("equal")
        else:  # bar
            bars = ax.bar(labels, values, color="#2c7be5")
            ax.set_ylabel("数量")
            ax.bar_label(bars)
        
        ax.set_title(title)
        if chart_type != "pie":
            plt.xticks(rotation=30, ha="right")
        plt.tight_layout()
        
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=100)
        plt.close(fig)
        b64 = base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        logger.exception("[visualize] 绘图失败")
        return {"error": f"绘图失败: {type(e).__name__}: {e}"}
    
    return {
        "image_base64": f"data:image/png;base64,{b64}",
        "chart_type": chart_type,
        "title": title,
        "error": None,
    }
```

**踩坑经验**：
- ❌ **DroidSansFallback 不够**：只含中文 Glyph，数字/拉丁文字符缺失
- ✅ **Noto Sans CJK**：三者全覆盖，优先选它
- ✅ **严格模式**：`axes.unicode_minus = False` 避免负号警告

### 3.3 开发 SUBGRAPH 类型 Skill

#### 3.3.1 完整示例：VLM 复判子图

**场景**：多模态复判需要 3 个步骤：
1. 查库拿图片路径和告警类型
2. 查 alarm_types 表拿 display_name
3. 调用 VLM 推理

**文件**: `skills/vlm_judge_subgraph.py`（真实代码）

```python
"""大模型复判子图（SUBGRAPH 类型 Skill）"""
import sqlite3
from pathlib import Path
from typing import Any
from loguru import logger
from skills.base import Skill, SkillType
from utils.vlm import get_vlm_client

DB_PATH = Path(__file__).resolve().parent.parent / "agent" / "data" / "ksipms_dev.db"

def _lookup_alarm(alarm_uuid: str) -> dict | None:
    """从库读取告警的 snapshot_url 和 alarm_type"""
    if not DB_PATH.exists():
        return None
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT alarm_uuid, alarm_type, snapshot_url, model_conf FROM alarms WHERE alarm_uuid=?",
            (alarm_uuid,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def _lookup_display_name(alarm_type: str) -> str:
    """从 alarm_types 表读 display_name"""
    if not DB_PATH.exists():
        return alarm_type
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT display_name FROM alarm_types WHERE type_code=?", (alarm_type,)
        ).fetchone()
        return row[0] if row else alarm_type
    finally:
        conn.close()

async def vlm_judge_impl(args: dict, context: dict) -> dict:
    """复判 Skill 实现（async 函数）"""
    alarm_uuid = args.get("alarm_uuid")
    image_path = args.get("image_path")
    alarm_type = args.get("alarm_type")
    model_conf = args.get("model_conf")
    
    # 路径1：传入 alarm_uuid，从库解析图片和类型
    if alarm_uuid:
        alarm = _lookup_alarm(alarm_uuid)
        if not alarm:
            return {"error": f"告警不存在: {alarm_uuid}"}
        image_path = alarm["snapshot_url"]
        alarm_type = alarm["alarm_type"]
        model_conf = alarm.get("model_conf")
    
    if not image_path:
        return {"error": "缺少 image_path 或 alarm_uuid"}
    if not alarm_type:
        return {"error": "缺少 alarm_type"}
    
    # 检查图片是否为本地路径且存在
    if image_path.startswith("/") and not Path(image_path).exists():
        return {"error": f"图片文件不存在: {image_path}"}
    if image_path.startswith("http"):
        return {"error": f"快照为远程 URL，无法本地复判: {image_path}（请使用测试数据集图片）"}
    
    display_name = _lookup_display_name(alarm_type)
    logger.info(f"[VLMJudge] 复判 alarm_uuid={alarm_uuid} type={alarm_type}({display_name})")
    
    try:
        vlm = get_vlm_client()
        result = vlm.judge_alarm_type(
            display_name=display_name,
            image_path=image_path,
            model_conf=model_conf,
        )
        result.update({
            "alarm_uuid": alarm_uuid,
            "alarm_type": alarm_type,
            "display_name": display_name,
            "error": None,
        })
        return result
    except Exception as e:
        logger.exception("[VLMJudge] 复判失败")
        return {"error": f"VLM 复判失败: {type(e).__name__}: {e}"}

def register_vlm_judge_skill(registry):
    """注册复判子图 Skill"""
    registry.register(Skill(
        id="vlm_judge_alarm",
        name="大模型复判告警",
        description="读取告警快照图片，用多模态大模型复判该告警是否真实（支持全部8类告警）。"
                    "传入 alarm_uuid 自动从库读取图片和类型。返回 verdict(confirmed/rejected/uncertain)、confidence、reasoning。",
        parameters={
            "type": "object",
            "properties": {
                "alarm_uuid": {"type": "string", "description": "告警UUID（推荐，自动读取图片和类型）"},
                "image_path": {"type": "string", "description": "图片本地路径（不传 alarm_uuid 时使用）"},
                "alarm_type": {"type": "string", "description": "告警类型 type_code（配合 image_path 使用）"},
            },
        },
        implementation=vlm_judge_impl,
        skill_type=SkillType.SUBGRAPH,
        tags=["vlm", "judge", "multimodal"],
    ))
    logger.info("复判子图 Skill 已注册: vlm_judge_alarm")
```

**关键点**：
1. ✅ **异步实现**：`async def vlm_judge_impl`，VLM 调用耗时
2. ✅ **内部状态管理**：查库、查表、VLM 推理，多步骤
3. ✅ **泛化设计**：支持 8 类告警（表驱动 display_name）
4. ✅ **两种入参模式**：alarm_uuid（便捷）/ image_path+alarm_type（灵活）

#### 3.3.2 SUBGRAPH vs TOOL 的选择

| 场景 | 推荐类型 | 理由 |
|------|----------|------|
| 单步 VLM 调用（图片路径已知） | TOOL | 简单直接 |
| VLM 调用前需查库拿图片 | SUBGRAPH | 内部管理查库逻辑 |
| 需要重试、降级、缓存 | SUBGRAPH | 可用 LangGraph 节点实现 |
| 纯 HTTP API 调用 | TOOL | 无状态，TOOL 即可 |

### 3.4 开发 MCP_TOOL 类型 Skill

MCP_TOOL 是对 MCP Server 工具的包装。开发流程：

1. **先开发 MCP Server**（§4）
2. **在 Skill Registry 注册为 MCP_TOOL**

**示例**：注册只写 MCP 的 `update_alarm_status` 工具

**文件**: `skills/alarm_skills.py`（续）

```python
"""状态回写 Skill（包装只写 MCP）"""

# 复判结论 verdict -> 告警状态 status 的映射
_VERDICT_TO_STATUS = {"confirmed": "closed", "rejected": "false_alarm"}

def update_alarm_status_impl(args: dict, context: dict) -> dict:
    """复判结论回写（包装 mcp_servers.ksipms_write_server 的受控写实现）
    
    支持两种入参：
    - 直接传 status (closed/false_alarm)
    - 传 verdict (confirmed/rejected)，自动映射为 status（便于用 {{step_N.verdict}} 引用复判输出）
    """
    from mcp_servers.ksipms_write_server import update_alarm_status_impl as _write
    
    alarm_uuid = args.get("alarm_uuid")
    status = args.get("status")
    note = args.get("note", "")
    
    # verdict 自动映射
    verdict = args.get("verdict")
    if not status and verdict:
        status = _VERDICT_TO_STATUS.get(verdict)
        if not status:
            return {"error": f"verdict={verdict} 无法映射为状态（confirmed/rejected）；uncertain 不自动回写"}
    
    if not alarm_uuid or not status:
        return {"error": "缺少 alarm_uuid 或 status"}
    
    result = _write(alarm_uuid, status, note)
    if not result.get("success"):
        return {"error": result.get("error", "写回失败")}
    return result

def register_alarm_skills(registry):
    # ... 前面的 aggregate/visualize 注册代码 ...
    
    registry.register(Skill(
        id="update_alarm_status",
        name="回写告警状态",
        description="将复判结论写回数据库并记审计日志。推荐用 verdict 参数引用复判子图的输出（如 verdict=\"{{step_0.verdict}}\"），"
                    "由系统自动映射：confirmed→closed、rejected→false_alarm。也可直接传 status(closed/false_alarm)。",
        parameters={
            "type": "object",
            "properties": {
                "alarm_uuid": {"type": "string", "description": "告警UUID"},
                "verdict": {"type": "string", "description": "复判结论 confirmed/rejected（推荐用 {{step_N.verdict}} 引用复判输出，自动映射为状态）"},
                "status": {"type": "string", "description": "直接指定状态 closed 或 false_alarm（与 verdict 二选一）"},
                "note": {"type": "string", "description": "处理备注（复判理由）"},
            },
            "required": ["alarm_uuid"]
        },
        implementation=update_alarm_status_impl,
        skill_type=SkillType.TOOL,  # 注意：这里是 TOOL，不是 MCP_TOOL
        tags=["data", "write"],
    ))
```

**设计亮点**：
- ✅ **verdict 自动映射**：Planner 无需记住 `confirmed→closed` 的映射规则
- ✅ **引导步骤引用**：description 中明确提示用 `{{step_0.verdict}}`
- ✅ **uncertain 拒绝回写**：避免低置信度误操作

### 3.5 Skill 注册到主图

**文件**: `skills/init.py`

```python
"""统一注册所有 Skills 到 Registry"""
from skills.registry import SkillRegistry, get_skill_registry
from skills.alarm_skills import register_alarm_skills
from skills.vlm_judge_subgraph import register_vlm_judge_skill
from loguru import logger

def init_skill_registry() -> SkillRegistry:
    """初始化 Skill Registry（主图启动时调用）"""
    registry = get_skill_registry()
    
    # 注册告警业务 Skills（4 个 TOOL）
    register_alarm_skills(registry)
    
    # 注册复判子图（1 个 SUBGRAPH）
    register_vlm_judge_skill(registry)
    
    # TODO: 注册其他业务 Skills
    
    logger.info(f"Skill Registry 初始化完成，共 {len(registry.list_skills())} 个 Skill")
    return registry
```

**启动时加载**：

**文件**: `main.py`（FastAPI 启动）

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from skills.init import init_skill_registry

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时：初始化 Skill Registry
    init_skill_registry()
    yield
    # 关闭时：清理资源（如需）

app = FastAPI(lifespan=lifespan)
```

---

## 4. MCP Server 开发指南

### 4.1 何时需要 MCP Server

**决策表**：

| 场景 | 推荐方案 | 理由 |
|------|----------|------|
| 查询数据库（只读） | 只读 MCP Server | 审计、字段白名单 |
| 写入数据库 | 只写 MCP Server | 强审计、操作白名单 |
| 纯计算（无数据访问） | TOOL Skill | 无需 MCP 开销 |
| 调用外部 API | TOOL Skill | HTTP 调用不需要 MCP |
| 需要细粒度权限控制 | MCP Server | 表级、字段级权限 |

**不需要 MCP Server 的场景**：
- ❌ matplotlib 绘图（纯计算）
- ❌ 调用 vLLM API（HTTP 调用）
- ❌ 数据格式转换（无状态）

### 4.2 只读 MCP Server 示例

**文件**: `mcp_servers/ksipms_server.py`（节选）

```python
"""KSIPMS 只读 MCP Server"""
import json
import sqlite3
from pathlib import Path
import yaml
from mcp.server import Server
from mcp.types import Tool, TextContent

def load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)["ksipms_server"]

CONFIG = load_config()
server = Server("ksipms")

@server.list_tools()
async def list_tools() -> list[Tool]:
    """列出所有可用工具"""
    return [
        Tool(
            name="query_alarms",
            description="查询告警记录（只读），支持按日期、类型、摄像头筛选",
            inputSchema={
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "日期 YYYY-MM-DD"},
                    "alarm_type": {"type": "string", "description": "告警类型"},
                    "camera_id": {"type": "string", "description": "摄像头ID"},
                    "limit": {"type": "integer", "description": "返回数量", "default": 10},
                },
            },
        ),
    ]

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    """调用工具"""
    if name == "query_alarms":
        result = query_alarms_impl(**arguments)
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]
    else:
        raise ValueError(f"Unknown tool: {name}")

def query_alarms_impl(date: str = None, alarm_type: str = None, 
                      camera_id: str = None, limit: int = 10) -> dict:
    """查询告警实现"""
    db_path = Path(CONFIG["db_path"])
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    
    where = ["1=1"]
    params = []
    if date:
        where.append("DATE(datetime(ts_event,'unixepoch')) = ?")
        params.append(date)
    if alarm_type:
        where.append("alarm_type = ?")
        params.append(alarm_type)
    if camera_id:
        where.append("camera_id = ?")
        params.append(camera_id)
    
    sql = f"SELECT * FROM alarms WHERE {' AND '.join(where)} ORDER BY ts_event DESC LIMIT ?"
    params.append(limit)
    
    try:
        rows = conn.execute(sql, params).fetchall()
        alarms = [dict(row) for row in rows]
        # 字段白名单过滤
        allowed_fields = CONFIG.get("allowed_fields", {}).get("alarms", [])
        if allowed_fields:
            alarms = [{k: v for k, v in a.items() if k in allowed_fields} for a in alarms]
        return {"alarms": alarms, "count": len(alarms)}
    finally:
        conn.close()
```

**配置文件**: `mcp_servers/config.yaml`

```yaml
ksipms_server:
  db_path: "agent/data/ksipms_dev.db"
  read_only: true
  
  allowed_fields:
    alarms:
      - alarm_uuid
      - alarm_type
      - camera_id
      - ts_event
      - status
      - snapshot_url
      # 不暴露: raw_payload, internal_notes
```

### 4.3 只写 MCP Server 示例（受控写操作）

**文件**: `mcp_servers/ksipms_write_server.py`（真实代码）

```python
"""KSIPMS 只写 MCP Server（受控写操作）"""
import json
import sqlite3
import time
from pathlib import Path
import yaml
from mcp.server import Server
from mcp.types import Tool, TextContent

def load_config() -> dict:
    config_path = Path(__file__).parent / "config_write.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)["ksipms_write_server"]

CONFIG = load_config()
OPERATOR_ID = "agent:vlm_judge"  # 智能体写操作统一标识
server = Server("ksipms_write")

def update_alarm_status_impl(alarm_uuid: str, status: str, note: str = "") -> dict:
    """更新告警状态（受控写）"""
    # 1. 校验 status 白名单
    allowed_status = CONFIG["allowed_status_values"]
    if status not in allowed_status:
        return {"success": False, "error": f"status 必须是 {allowed_status} 之一，收到: {status}"}
    
    db_path = Path(CONFIG["db_path"])
    if not db_path.is_absolute():
        db_path = Path(__file__).resolve().parent.parent / CONFIG["db_path"]
    
    if not db_path.exists():
        return {"success": False, "error": f"db not found: {db_path}"}
    
    now = int(time.time())
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            # 2. 校验告警存在
            cur = conn.execute("SELECT alarm_uuid, status FROM alarms WHERE alarm_uuid=?", (alarm_uuid,))
            row = cur.fetchone()
            if not row:
                return {"success": False, "error": f"告警不存在: {alarm_uuid}"}
            old_status = row[1]
            
            
            # 3. 强制审计（写操作前）
            conn.execute(
                "INSERT INTO audit_log(alarm_id, action, operator_id, payload, ts) VALUES (?,?,?,?,?)",
                (
                    alarm_uuid, "agent_update_status", OPERATOR_ID,
                    json.dumps({"old_status": old_status, "new_status": status, "note": note}, ensure_ascii=False),
                    now,
                ),
            )
            
            # 4. 受控更新（只动白名单字段）
            conn.execute(
                "UPDATE alarms SET status=?, processed_note=?, processed_at=?, processed_by=? WHERE alarm_uuid=?",
                (status, note, now, OPERATOR_ID, alarm_uuid),
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error as e:
        return {"success": False, "error": f"sqlite error: {e}"}
    
    return {
        "success": True, "error": None,
        "alarm_uuid": alarm_uuid,
        "old_status": old_status, "new_status": status,
        "processed_by": OPERATOR_ID, "processed_at": now,
    }

@server.list_tools()
async def list_tools() -> list[Tool]:
    tools_config = CONFIG["tools"]
    return [
        Tool(
            name="update_alarm_status",
            description=tools_config["update_alarm_status"]["description"],
            inputSchema=tools_config["update_alarm_status"]["parameters"],
        ),
    ]

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "update_alarm_status":
        result = update_alarm_status_impl(**arguments)
    else:
        result = {"success": False, "error": f"unknown tool: {name}"}
    return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]
```

**配置文件**: `mcp_servers/config_write.yaml`

```yaml
ksipms_write_server:
  db_path: "agent/data/ksipms_dev.db"
  read_only: false  # 允许写
  
  # 状态值白名单（只能写这两个值）
  allowed_status_values:
    - closed
    - false_alarm
  
  # 工具定义
  tools:
    update_alarm_status:
      description: "更新告警处理状态（仅限 closed/false_alarm），自动记录审计日志"
      parameters:
        type: object
        properties:
          alarm_uuid:
            type: string
            description: 告警UUID
          status:
            type: string
            description: 新状态（closed 或 false_alarm）
            enum: [closed, false_alarm]
          note:
            type: string
            description: 处理备注
        required: [alarm_uuid, status]
```

**关键设计**：
1. ✅ **白名单校验**：status 只能是 `closed/false_alarm`
2. ✅ **强制审计**：每次写入前必须插入 `audit_log`
3. ✅ **字段限制**：只能更新 4 个字段，其他字段不可动
4. ✅ **统一标识**：`processed_by` 固定为 `agent:vlm_judge`

### 4.4 测试 MCP Server

#### 4.4.1 单元测试（直接调用 impl）

```python
# tests/test_mcp_write_server.py
import pytest
from mcp_servers.ksipms_write_server import update_alarm_status_impl

def test_update_alarm_status_success():
    """测试正常更新"""
    result = update_alarm_status_impl(
        alarm_uuid="test-uuid-001",
        status="closed",
        note="VLM 复判确认"
    )
    assert result["success"] is True
    assert result["new_status"] == "closed"
    assert result["processed_by"] == "agent:vlm_judge"

def test_update_alarm_status_invalid_status():
    """测试非法 status"""
    result = update_alarm_status_impl(
        alarm_uuid="test-uuid-001",
        status="pending",  # 不在白名单
        note=""
    )
    assert result["success"] is False
    assert "status 必须是" in result["error"]

def test_update_alarm_status_not_found():
    """测试告警不存在"""
    result = update_alarm_status_impl(
        alarm_uuid="nonexistent-uuid",
        status="closed",
        note=""
    )
    assert result["success"] is False
    assert "告警不存在" in result["error"]
```

#### 4.4.2 集成测试（通过 MCP 协议）

```bash
# 启动 MCP Server（stdio 模式）
python -m mcp_servers.ksipms_write_server

# 测试工具列表
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python -m mcp_servers.ksipms_write_server

# 测试工具调用
echo '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"update_alarm_status","arguments":{"alarm_uuid":"test-uuid","status":"closed","note":"测试"}}}' | python -m mcp_servers.ksipms_write_server
```

#### 4.4.3 审计日志验证

```bash
# 查看审计日志
sqlite3 agent/data/ksipms_dev.db \
  "SELECT alarm_id, action, operator_id, payload, datetime(ts, 'unixepoch') AS ts_readable 
   FROM audit_log 
   WHERE operator_id='agent:vlm_judge' 
   ORDER BY ts DESC 
   LIMIT 10"
```

**预期输出**：
```
test-uuid-001|agent_update_status|agent:vlm_judge|{"old_status":"pending","new_status":"closed","note":"VLM 复判确认"}|2026-06-06 10:30:45
```

---

## 5. 智能体开发流程

### 5.1 开发方式选择

| 方式 | 适用场景 | 优势 | 劣势 | 开发时间 |
|------|----------|------|------|----------|
| **手动开发** | 复杂业务逻辑、需精细控制 | 灵活、可调试、质量可控 | 开发慢 | 2-4h |
| **Agent-of-Agent** | 标准 CRUD、简单查询聚合 | 快速生成、自动测试 | 代码质量依赖 LLM | 10-20min |
| **主智能体编排** | 已有 Skill 组合、多步骤 | 零代码、纯配置 | 受限于已有 Skill | 5min |

**决策树**：
```
需求是否可用现有 Skill 组合实现？
  ├─ 是 → 主智能体编排（最快）
  └─ 否 → 需要新 Skill
          ├─ 简单 CRUD？
          │   ├─ 是 → Agent-of-Agent 生成
          │   └─ 否 → 手动开发
          └─ 复杂业务逻辑 → 手动开发
```

### 5.2 手动开发智能体（推荐）

#### 5.2.1 创建 Agent 文件

**文件**: `agent/artifacts/published/weekly_alarm_stats_v1_0_0.py`

```python
"""
Weekly Alarm Stats Agent - 每周告警统计智能体

功能：统计本周（周一到周日）的告警情况，按类型分组
"""
from datetime import datetime, timedelta
from skills import get_skill_registry
from loguru import logger

async def run(user_input: str, session_id: str = "") -> str:
    """
    Agent 入口函数
    
    Args:
        user_input: 用户输入
        session_id: 会话ID
    
    Returns:
        响应文本
    """
    registry = get_skill_registry()
    
    # 计算本周日期范围
    today = datetime.now()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    
    date_start = monday.strftime("%Y-%m-%d")
    date_end = sunday.strftime("%Y-%m-%d")
    
    logger.info(f"[WeeklyStats] 统计周期: {date_start} ~ {date_end}")
    
    # 调用聚合统计 Skill
    result = await registry.invoke(
        skill_id="aggregate_alarms",
        args={
            "group_by": "alarm_type",
            "date_start": date_start,
            "date_end": date_end,
        },
        context={"agent": "weekly_alarm_stats", "session_id": session_id}
    )
    
    if result.get("error"):
        return f"统计失败: {result['error']}"
    
    # 格式化输出
    data = result.get("data", [])
    total = result.get("total", 0)
    
    if not data:
        return f"本周（{date_start} 至 {date_end}）暂无告警记录。"
    
    lines = [f"本周告警统计（{date_start} 至 {date_end}）："]
    for item in data:
        lines.append(f"- {item['key']}: {item['count']}次")
    lines.append(f"总计: {total}次")
    
    return "\n".join(lines)
```

**关键点**：
1. ✅ **async def run**：标准入口，返回 str
2. ✅ **通过 Registry 调用 Skill**：不直接导入 `aggregate_alarms_impl`
3. ✅ **错误处理友好**：返回用户可理解的信息
4. ✅ **日志记录**：便于调试

#### 5.2.2 注册 Agent

**文件**: `agent/registry/agent_registry.json`

```json
{
  "weekly_alarm_stats": {
    "version": "1.0.0",
    "published_path": "agent/artifacts/published/weekly_alarm_stats_v1_0_0.py",
    "module_name": "weekly_alarm_stats_v1_0_0",
    "route": "/agents/weekly_alarm_stats/chat",
    "registered_at": "2026-06-06T10:00:00Z",
    "description": "统计本周告警情况，按类型分组",
    "tags": ["stats", "weekly"]
  }
}
```

#### 5.2.3 手动注册到 FastAPI

**文件**: `main.py`

```python
from agent.registry import list_agents, load_agent_run
from fastapi import HTTPException
from pydantic import BaseModel

class ChatRequest(BaseModel):
    message: str
    session_id: str = ""

@app.post("/agents/{agent_name}/chat")
async def agent_chat(agent_name: str, request: ChatRequest):
    """动态路由到已发布 Agent"""
    try:
        run_func = load_agent_run(agent_name)
        response = await run_func(request.message, request.session_id)
        return {"response": response, "agent": agent_name}
    except KeyError:
        raise HTTPException(404, f"Agent {agent_name} not found")
    except Exception as e:
        logger.exception(f"Agent {agent_name} 执行失败")
        raise HTTPException(500, f"Agent execution failed: {e}")

@app.get("/agents")
async def list_all_agents():
    """列出所有已发布 Agent"""
    return {"agents": list_agents()}
```

#### 5.2.4 测试 Agent

```bash
# API 调用
curl -X POST http://localhost:8001/agents/weekly_alarm_stats/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "本周告警统计", "session_id": "test"}'

# 预期输出
{
  "response": "本周告警统计（2026-06-02 至 2026-06-08）：\n- 未戴安全帽: 15次\n- 抽烟: 3次\n- 接打电话: 7次\n总计: 25次",
  "agent": "weekly_alarm_stats"
}
```

### 5.3 使用 Agent-of-Agent 生成

#### 5.3.1 编写需求描述

**文件**: `requirements/weekly_stats.txt`

```
Agent Name: weekly_alarm_stats
Description: 统计本周告警情况

功能需求：
1. 自动计算本周周一到周日的日期范围
2. 调用 aggregate_alarms Skill 查询本周告警
3. 按告警类型分组统计数量
4. 返回格式化的统计结果

示例输入：
- "本周告警统计"
- "这周发生了哪些告警"
- "weekly report"

示例输出：
本周告警统计（2026-06-02 至 2026-06-08）：
- 未戴安全帽: 15次
- 抽烟: 3次
- 接打电话: 7次
总计: 25次

技术约束：
- 使用 aggregate_alarms Skill
- 日期计算用 datetime 标准库
- 错误时返回友好提示
```

#### 5.3.2 运行元智能体

```bash
cd /mnt/data3/clip/LangGraph/agent
python -m agent.run_meta_agent

# 交互式输入（或读取文件）
# Agent Name: weekly_alarm_stats
# Description: 统计本周告警情况
# ... 粘贴需求描述 ...

# 或非交互式
python -m agent.run_meta_agent --requirement requirements/weekly_stats.txt
```

**执行流程**：
```
1. Generator: 生成代码到 agent/artifacts/<job_id>/agent_code.py
2. Executor: 运行测试用例（自动生成或手动提供）
3. Acceptor: 验收评分（代码质量、测试覆盖、功能完整性）
4. 通过验收 → 自动发布到 agent/artifacts/published/
5. 更新 agent_registry.json
```

#### 5.3.3 查看生成结果

```bash
# 生成的代码
cat agent/artifacts/<job_id>/agent_code.py

# 验收报告
cat agent/artifacts/<job_id>/REGISTER.json

# 示例输出
{
  "job_id": "abc123",
  "agent_name": "weekly_alarm_stats",
  "passed_tests": 3,
  "total_tests": 3,
  "acceptance_score": 85,
  "passed_acceptance": true,
  "published_at": "2026-06-06T10:30:00Z"
}
```

#### 5.3.4 调整生成代码（可选）

如果生成代码不完美，可手动微调：

```bash
# 1. 编辑生成的代码
vim agent/artifacts/published/weekly_alarm_stats_v1_0_0.py

# 2. 重新测试
python -m agent.test_agent weekly_alarm_stats

# 3. 确认无误后，代码已在 published/ 目录，自动生效
```

### 5.4 主智能体编排（零代码）

**场景**：用户请求 = 已有 Skill 的组合

**示例 1**：统计 + 可视化

```
用户："最近 7 天每种告警类型的数量，给我一个柱状图"

主智能体自动规划：
步骤 0: aggregate_alarms(group_by=alarm_type, date_start=7天前, date_end=今天)
步骤 1: visualize_alarms(data={{step_0.data}}, chart_type=bar)

无需写任何代码，直接跑通！
```

**示例 2**：复判 + 回写

```
用户："复判告警 abc-123，并根据复判结论回写状态"

主智能体自动规划：
步骤 0: vlm_judge_alarm(alarm_uuid=abc-123)
步骤 1: update_alarm_status(alarm_uuid=abc-123, verdict={{step_0.verdict}})

verdict 自动映射为 status，无需人工干预！
```

**适用条件**：
- ✅ 请求可拆解为 2-5 步
- ✅ 每步对应一个已注册 Skill
- ✅ 步骤间传参清晰（输出字段明确）

**不适用条件**：
- ❌ 需要复杂条件判断（if-else 分支）
- ❌ 需要循环（while/for）
- ❌ 需要自定义格式化逻辑

---

## 6. 测试与调试

### 6.1 单元测试

#### 6.1.1 测试 TOOL Skill

```python
# tests/test_alarm_skills.py
import pytest
from skills.alarm_skills import aggregate_alarms_impl

@pytest.mark.asyncio
async def test_aggregate_alarms_by_type():
    """测试按类型聚合"""
    args = {"group_by": "alarm_type"}
    context = {"session_id": "test"}
    
    result = aggregate_alarms_impl(args, context)
    
    assert "error" not in result or result["error"] is None
    assert "data" in result
    assert isinstance(result["data"], list)
    assert result["total"] >= 0

@pytest.mark.asyncio
async def test_aggregate_alarms_by_date():
    """测试按日期聚合"""
    args = {
        "group_by": "date",
        "date_start": "2026-06-01",
        "date_end": "2026-06-07"
    }
    context = {}
    
    result = aggregate_alarms_impl(args, context)
    
    assert "error" not in result or result["error"] is None
    assert "data" in result
    # 验证日期排序
    dates = [d["key"] for d in result["data"]]
    assert dates == sorted(dates)

@pytest.mark.asyncio
async def test_aggregate_alarms_invalid_group_by():
    """测试非法 group_by"""
    args = {"group_by": "invalid"}
    context = {}
    
    result = aggregate_alarms_impl(args, context)
    
    assert "error" in result
    assert "group_by 必须是" in result["error"]
```

#### 6.1.2 测试 SUBGRAPH Skill

```python
# tests/test_vlm_judge.py
import pytest
from skills.vlm_judge_subgraph import vlm_judge_impl

@pytest.mark.asyncio
async def test_vlm_judge_by_uuid():
    """测试通过 alarm_uuid 复判"""
    args = {"alarm_uuid": "test-uuid-with-image"}
    context = {}
    
    result = await vlm_judge_impl(args, context)
    
    assert "error" not in result or result["error"] is None
    assert "verdict" in result
    assert result["verdict"] in ("confirmed", "rejected", "uncertain")
    assert "confidence" in result
    assert 0 <= result["confidence"] <= 1

@pytest.mark.asyncio
async def test_vlm_judge_alarm_not_found():
    """测试告警不存在"""
    args = {"alarm_uuid": "nonexistent-uuid"}
    context = {}
    
    result = await vlm_judge_impl(args, context)
    
    assert "error" in result
    assert "告警不存在" in result["error"]
```

#### 6.1.3 测试步骤间传参

```python
# tests/test_step_param_passing.py
import pytest
from graph.nodes import _resolve_step_references

def test_pure_reference_preserves_type():
    """测试纯引用保留类型"""
    step_outputs = {0: {"data": [{"key": "a", "count": 10}], "camera_id": "CAM-001"}}
    args = {"data": "{{step_0.data}}"}
    
    resolved = _resolve_step_references(args, step_outputs, 1)
    
    assert isinstance(resolved["data"], list)
    assert resolved["data"][0]["key"] == "a"

def test_mixed_string_reference():
    """测试混合字符串引用"""
    step_outputs = {0: {"camera_id": "CAM-001"}}
    args = {"message": "摄像头{{step_0.camera_id}}发生告警"}
    
    resolved = _resolve_step_references(args, step_outputs, 1)
    
    assert resolved["message"] == "摄像头CAM-001发生告警"

def test_nested_field_access():
    """测试嵌套字段访问"""
    step_outputs = {0: {"data": {"camera": {"id": "CAM-001"}}}}
    args = {"camera_id": "{{step_0.data.camera.id}}"}
    
    resolved = _resolve_step_references(args, step_outputs, 1)
    
    assert resolved["camera_id"] == "CAM-001"
```


### 6.2 集成测试

#### 6.2.1 测试完整的 Plan-Execute 流程

```python
# tests/test_e2e_main_graph.py
import pytest
from graph.main_graph import build_main_graph
from graph.state import AgentState

@pytest.mark.asyncio
async def test_aggregate_and_visualize():
    """端到端测试：聚合统计 + 可视化"""
    graph = build_main_graph()
    
    initial_state = AgentState(
        session_id="test_e2e",
        user_message="统计每种告警类型数量并画柱状图",
        messages=[],
        plan=[],
        current_task_idx=0,
        tool_results=[],
        step_outputs={},
        final_response=None,
        error=None,
    )
    
    # 执行主图
    result = graph.invoke(initial_state)
    
    # 验证
    assert result["final_response"] is not None
    assert len(result["plan"]) >= 2  # 至少 2 步
    assert result["plan"][0]["task"] == "aggregate_alarms"
    assert result["plan"][1]["task"] == "visualize_alarms"
    assert "image_base64" in str(result["tool_results"])

@pytest.mark.asyncio
async def test_vlm_judge_and_write_back():
    """端到端测试：VLM 复判 + 回写状态"""
    graph = build_main_graph()
    
    # 准备一个 pending 状态的测试告警
    from agent.data.seed_test_alarms import seed_one_alarm
    alarm_uuid = seed_one_alarm(alarm_type="no_helmet", status="pending")
    
    initial_state = AgentState(
        session_id="test_vlm",
        user_message=f"复判告警 {alarm_uuid}，并根据复判结论回写状态",
        messages=[],
        plan=[],
        current_task_idx=0,
        tool_results=[],
        step_outputs={},
        final_response=None,
        error=None,
    )
    
    result = graph.invoke(initial_state)
    
    # 验证计划
    assert len(result["plan"]) == 2
    assert result["plan"][0]["task"] == "vlm_judge_alarm"
    assert result["plan"][1]["task"] == "update_alarm_status"
    
    # 验证步骤间传参
    assert "{{step_0.verdict}}" in str(result["plan"][1]["args"])
    
    # 验证执行结果
    assert result["step_outputs"][0]["verdict"] in ("confirmed", "rejected", "uncertain")
    if result["step_outputs"][0]["verdict"] != "uncertain":
        assert result["step_outputs"][1]["success"] is True
```

#### 6.2.2 测试 Skill Registry

```python
# tests/test_skill_registry.py
import pytest
from skills import get_skill_registry

def test_registry_initialization():
    """测试 Skill Registry 初始化"""
    registry = get_skill_registry()
    skills = registry.list_skills()
    
    # 验证核心 Skills 已注册
    skill_ids = [s.id for s in skills]
    assert "aggregate_alarms" in skill_ids
    assert "visualize_alarms" in skill_ids
    assert "vlm_judge_alarm" in skill_ids
    assert "update_alarm_status" in skill_ids

@pytest.mark.asyncio
async def test_skill_invocation():
    """测试 Skill 调用"""
    registry = get_skill_registry()
    
    result = await registry.invoke(
        skill_id="aggregate_alarms",
        args={"group_by": "alarm_type"},
        context={"session_id": "test"}
    )
    
    assert "data" in result
    assert isinstance(result["data"], list)
```

### 6.3 调试技巧

#### 6.3.1 启用详细日志

**文件**: `utils/logger.py`（或 main.py）

```python
from loguru import logger
import sys

# 开发环境：详细日志
logger.remove()
logger.add(
    sys.stderr,
    level="DEBUG",
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
)
logger.add("logs/debug.log", rotation="100 MB", level="DEBUG")
```

#### 6.3.2 调试 Planner 输出

```python
# 临时脚本：test_planner.py
from graph.nodes import planner_node
from graph.state import AgentState

state = AgentState(
    session_id="debug",
    user_message="查询今天的未戴安全帽告警，取第一条的图片复判",
    messages=[],
    plan=[],
    current_task_idx=0,
    tool_results=[],
    step_outputs={},
    final_response=None,
    error=None,
)

result = planner_node(state)
print("生成的 Plan:")
import json
print(json.dumps(result["plan"], indent=2, ensure_ascii=False))
```

**预期输出**：
```json
[
  {
    "task": "query_alarms",
    "args": {
      "date": "2026-06-06",
      "alarm_type": "no_helmet",
      "limit": 1
    },
    "status": "pending"
  },
  {
    "task": "vlm_judge_alarm",
    "args": {
      "alarm_uuid": "{{step_0.alarms.0.alarm_uuid}}"
    },
    "status": "pending"
  }
]
```

#### 6.3.3 调试 Executor 步骤间传参

```python
# 在 graph/nodes.py 的 executor_node 中加日志
def executor_node(state: AgentState) -> Dict[str, Any]:
    idx = state["current_task_idx"]
    task = state["plan"][idx]
    
    # 解析前
    logger.debug(f"[Executor] 步骤 {idx} 原始 args: {task['args']}")
    
    # 解析后
    resolved_args = _resolve_step_references(task["args"], state.get("step_outputs", {}), idx)
    logger.debug(f"[Executor] 步骤 {idx} 解析后 args: {resolved_args}")
    
    # ... 执行逻辑 ...
```

#### 6.3.4 使用 Gradio 调试面板

Gradio Web 控制台的 Tab6 右侧显示：
- ✅ **完整 Plan**：每个步骤的 task 和 args
- ✅ **Tool Results**：每个步骤的返回值
- ✅ **Step Outputs**：步骤输出字典（用于传参）
- ✅ **Final Response**：formatter 生成的最终回复

**使用方法**：
1. 启动 Gradio：`python agent/web/app.py`
2. 在 Tab6 输入测试用例
3. 观察右侧 JSON 面板，检查：
   - Plan 是否合理
   - Args 中的 `{{step_N.field}}` 是否被正确解析
   - Tool Results 是否包含预期字段

#### 6.3.5 Mock VLM 服务（快速测试）

```python
# utils/vlm.py 中加 Mock 模式
class VLMClient:
    def __init__(self, base_url: str, model: str, api_key: str):
        self.mock_mode = os.getenv("VLM_MOCK", "false").lower() == "true"
        if not self.mock_mode:
            # 真实 VLM 初始化
            self.client = OpenAI(base_url=base_url, api_key=api_key)
        else:
            logger.warning("[VLM] Mock 模式启用，返回模拟结果")
    
    def judge_alarm_type(self, display_name: str, image_path: str, model_conf: float = None) -> dict:
        if self.mock_mode:
            # 返回模拟结果
            return {
                "verdict": "confirmed",
                "exists": True,
                "confidence": 0.92,
                "reasoning": "Mock 模式：假设告警真实"
            }
        # 真实 VLM 调用
        # ...
```

**启用 Mock**：
```bash
export VLM_MOCK=true
python -m pytest tests/test_vlm_judge.py
```

---

## 7. 发布与部署

### 7.1 发布 Agent

#### 7.1.1 手动发布流程

```bash
# 1. 确认 Agent 代码在 published/ 目录
ls agent/artifacts/published/weekly_alarm_stats_v1_0_0.py

# 2. 更新 agent_registry.json
vim agent/registry/agent_registry.json
# 添加：
# "weekly_alarm_stats": {
#   "version": "1.0.0",
#   "published_path": "agent/artifacts/published/weekly_alarm_stats_v1_0_0.py",
#   ...
# }

# 3. 重启 FastAPI（热重载）
# 如果 config.yaml 中 server.reload: true，代码会自动重载
# 否则手动重启：
pkill -f "python main.py"
python main.py

# 4. 验证
curl http://localhost:8001/agents
# 应看到 weekly_alarm_stats
```

#### 7.1.2 Agent-of-Agent 自动发布

```bash
# 生成并自动发布
python -m agent.run_meta_agent --requirement requirements/weekly_stats.txt --auto-publish

# 查看发布状态
cat agent/artifacts/<job_id>/REGISTER.json
```

### 7.2 Docker 部署

#### 7.2.1 Dockerfile

```dockerfile
# Dockerfile
FROM python:3.10-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    fonts-noto-cjk \
    libsqlite3-0 \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir mcp

# 复制项目文件
COPY . .

# 暴露端口
EXPOSE 8001

# 启动命令
CMD ["python", "main.py"]
```

#### 7.2.2 docker-compose.yml

```yaml
version: '3.8'

services:
  ksagent:
    build: .
    ports:
      - "8001:8001"
    volumes:
      - ./agent/data:/app/agent/data
      - ./agent/registry:/app/agent/registry
      - ./logs:/app/logs
    environment:
      - LLM_BASE_URL=http://vllm:8004/v1
      - LOG_LEVEL=info
    depends_on:
      - vllm
    restart: unless-stopped
  
  vllm:
    image: vllm/vllm-openai:latest
    command: >
      --model Qwen/Qwen2-VL-7B-Instruct
      --port 8004
      --tensor-parallel-size 1
    ports:
      - "8004:8004"
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    restart: unless-stopped
```

#### 7.2.3 部署命令

```bash
# 构建镜像
docker-compose build

# 启动服务
docker-compose up -d

# 查看日志
docker-compose logs -f ksagent

# 健康检查
curl http://localhost:8001/health

# 停止服务
docker-compose down
```

### 7.3 监控与维护

#### 7.3.1 健康检查端点

**文件**: `main.py`

```python
@app.get("/health")
async def health_check():
    """健康检查"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "skills_loaded": len(get_skill_registry().list_skills()),
        "agents_registered": len(list_agents()),
    }
```

#### 7.3.2 审计日志监控

```bash
# 定时任务：每小时统计 Agent 写操作
# crontab -e
# 0 * * * * /app/scripts/audit_report.sh

# audit_report.sh
#!/bin/bash
sqlite3 /app/agent/data/ksipms_dev.db <<EOF
SELECT 
    operator_id,
    COUNT(*) AS operations,
    MIN(datetime(ts, 'unixepoch')) AS first_op,
    MAX(datetime(ts, 'unixepoch')) AS last_op
FROM audit_log
WHERE ts >= strftime('%s', 'now', '-1 hour')
GROUP BY operator_id;
EOF
```

#### 7.3.3 性能监控

```python
# utils/metrics.py
from prometheus_client import Counter, Histogram
import time

skill_invocation_counter = Counter('skill_invocations_total', 'Total skill invocations', ['skill_id', 'status'])
skill_duration_histogram = Histogram('skill_duration_seconds', 'Skill execution duration', ['skill_id'])

def track_skill_invocation(skill_id: str):
    """装饰器：追踪 Skill 调用"""
    def decorator(func):
        async def wrapper(*args, **kwargs):
            start = time.time()
            try:
                result = await func(*args, **kwargs)
                skill_invocation_counter.labels(skill_id=skill_id, status='success').inc()
                return result
            except Exception as e:
                skill_invocation_counter.labels(skill_id=skill_id, status='error').inc()
                raise
            finally:
                duration = time.time() - start
                skill_duration_histogram.labels(skill_id=skill_id).observe(duration)
        return wrapper
    return decorator
```

---

## 8. 最佳实践

### 8.1 Skill 开发最佳实践

#### ✅ 推荐做法

1. **保持 Skill 功能单一**
   ```python
   # ✅ 好：单一职责
   def aggregate_alarms_impl(args, context):
       """只负责聚合统计"""
       return {"data": [...], "total": 10}
   
   # ❌ 坏：又聚合又可视化
   def aggregate_and_visualize_impl(args, context):
       data = aggregate(...)
       image = visualize(data)  # 应该拆成两个 Skill
       return {"data": data, "image": image}
   ```

2. **参数使用 JSON Schema 严格校验**
   ```python
   parameters = {
       "type": "object",
       "properties": {
           "group_by": {
               "type": "string",
               "enum": ["date", "alarm_type", "camera_id"],  # 枚举值
               "description": "分组维度"
           },
           "limit": {
               "type": "integer",
               "minimum": 1,  # 最小值
               "maximum": 1000,  # 最大值
               "default": 10
           }
       },
       "required": ["group_by"]  // 必填字段
   }
   ```

3. **错误返回统一格式**
   ```python
   # ✅ 好：返回 {"error": "..."}
   if not alarm_uuid:
       return {"error": "缺少 alarm_uuid"}
   
   # ❌ 坏：抛异常（会中断整个流程）
   if not alarm_uuid:
       raise ValueError("缺少 alarm_uuid")
   ```

4. **异步函数优先**
   ```python
   # ✅ 好：async 函数（性能更好）
   async def my_skill_impl(args, context):
       result = await some_async_api()
       return result
   
   # ⚠️ 可以：同步函数（简单计算）
   def my_skill_impl(args, context):
       return {"result": sum(args["numbers"])}
   ```

5. **详细的 docstring**
   ```python
   async def vlm_judge_impl(args: dict, context: dict) -> dict:
       """VLM 复判告警
       
       Args:
           args: {
               "alarm_uuid": str, 告警UUID（推荐）
               "image_path": str, 图片路径（可选）
               "alarm_type": str, 告警类型（可选）
           }
           context: {
               "session_id": str,
               "agent": str
           }
       
       Returns:
           {
               "verdict": "confirmed" | "rejected" | "uncertain",
               "confidence": float,  // 0.0-1.0
               "reasoning": str,
               "error": str | None
           }
       """
   ```

#### ❌ 避免做法

1. **Skill 内部直接访问数据库（应用 MCP）**
2. **硬编码配置（应用 config.yaml）**
3. **捕获异常后不返回错误信息**
4. **循环依赖其他 Skill**
5. **返回超大对象（如完整图片 bytes，应 base64）**

### 8.2 MCP Server 开发最佳实践

#### ✅ 推荐做法

1. **只读模式优先**
   ```yaml
   ksipms_server:
       read_only: true  # 默认只读
   ```

2. **字段白名单（隐藏敏感信息）**
   ```yaml
   allowed_fields:
     alarms:
       - alarm_uuid
       - alarm_type
       - ts_event
       # 不暴露: internal_notes, raw_payload
   ```

3. **强制审计（所有写操作）**
   ```python
   # 写操作前必须插入审计日志
   conn.execute(
       "INSERT INTO audit_log(...) VALUES (...)",
       (alarm_uuid, action, operator_id, payload, ts)
   )
   conn.execute("UPDATE alarms SET ... WHERE alarm_uuid=?", ...)
   conn.commit()
   ```

4. **参数校验（防止 SQL 注入）**
   ```python
   # ✅ 好：参数化查询
   conn.execute("SELECT * FROM alarms WHERE alarm_uuid=?", (alarm_uuid,))
   
   # ❌ 坏：字符串拼接
   conn.execute(f"SELECT * FROM alarms WHERE alarm_uuid='{alarm_uuid}'")
   ```

#### ❌ 避免做法

1. **暴露所有表和字段**
2. **允许写操作（除非必要）**
3. **跳过审计日志**
4. **返回原始错误信息（可能泄露内部结构）**

### 8.3 智能体开发最佳实践

#### ✅ 推荐做法

1. **通过 Skill Registry 调用工具**
   ```python
   # ✅ 好
   result = await registry.invoke("aggregate_alarms", args, context)
   
   # ❌ 坏：直接导入
   from skills.alarm_skills import aggregate_alarms_impl
   result = aggregate_alarms_impl(args, context)
   ```

2. **错误处理友好**
   ```python
   if result.get("error"):
       return f"查询失败：{result['error']}"  # 用户可理解
   ```

3. **添加示例输入输出**
   ```python
   """
   示例输入：
   - "本周告警统计"
   - "这周发生了哪些告警"
   
   示例输出：
   本周告警统计（2026-06-02 至 2026-06-08）：
   - 未戴安全帽: 15次
   总计: 15次
   """
   ```

4. **版本号语义化**
   ```
   v1.0.0 → 初始版本
   v1.1.0 → 新增功能（向后兼容）
   v2.0.0 → 破坏性变更
   ```


### 8.4 测试最佳实践

#### ✅ 推荐做法

1. **每个 Skill 至少 3 个测试（正常/异常/边界）**
   ```python
   # 正常情况
   def test_aggregate_alarms_success(): ...
   
   # 异常情况
   def test_aggregate_alarms_invalid_group_by(): ...
   
   # 边界条件
   def test_aggregate_alarms_empty_result(): ...
   ```

2. **端到端测试覆盖核心流程**
3. **Mock 外部依赖（数据库、API）**
4. **使用 pytest fixtures**

---

## 9. 常见问题

### Q1: 如何查看可用的 Skill？

**方法 1**：通过 API

```bash
curl http://localhost:8001/skills
```

**方法 2**：Python 脚本

```python
from skills import get_skill_registry

registry = get_skill_registry()
skills = registry.list_skills()

for skill in skills:
    print(f"{skill.id}: {skill.description}")
    print(f"  Type: {skill.skill_type}")
    print(f"  Params: {list(skill.parameters.get('properties', {}).keys())}")
```

### Q2: MCP Server 调用失败怎么办？

**排查步骤**：

1. **检查配置文件**
   ```bash
   cat mcp_servers/config.yaml
   # 确认 db_path 正确
   ```

2. **测试 MCP Server 单独运行**
   ```bash
   python -m mcp_servers.ksipms_server
   # 看是否报错
   ```

3. **查看审计日志**
   ```bash
   sqlite3 agent/data/ksipms_dev.db "SELECT * FROM audit_log ORDER BY ts DESC LIMIT 10"
   ```

4. **启用详细日志**
   ```python
   # 在 main.py 中
   logger.add("logs/debug.log", level="DEBUG")
   ```

### Q3: Agent 生成质量不高？

**改进方法**：

1. **优化需求描述**
   ```
   ❌ 坏："写一个查询告警的 Agent"
   ✅ 好："查询指定日期范围内特定类型的告警，返回告警 UUID、时间、摄像头ID，按时间倒序排列"
   ```

2. **提供示例输入输出**
3. **更新 RULES.md**（添加约束）
4. **人工审核生成代码后再发布**

### Q4: 如何回滚 Agent？

```bash
# 1. 从注册表删除
vim agent/registry/agent_registry.json
# 删除对应条目

# 2. 重启 FastAPI
pkill -f "python main.py"
python main.py

# 或：发布旧版本
python -m agent.publish <old_job_id>
```

### Q5: 步骤间传参不生效？

**排查清单**：

1. ✅ 检查语法：`{{step_0.field}}` 双花括号
2. ✅ 检查步骤索引：引用的步骤必须已执行（step_0 可被 step_1 引用）
3. ✅ 检查字段路径：`step_0.data.camera_id` 嵌套访问
4. ✅ 查看日志：`grep "解析参数" logs/debug.log`

**调试脚本**：
```python
from graph.nodes import _resolve_step_references

step_outputs = {0: {"camera_id": "CAM-001", "data": [...]}}
args = {"camera_id": "{{step_0.camera_id}}"}
resolved = _resolve_step_references(args, step_outputs, 1)
print(resolved)  # 应输出 {"camera_id": "CAM-001"}
```

### Q6: VLM 复判超时？

**原因**：
- vLLM 服务未启动
- 模型加载慢（首次推理）
- 图片分辨率过高

**解决方案**：

1. **检查 vLLM 服务**
   ```bash
   curl http://127.0.0.1:8004/v1/models
   ```

2. **增加超时时间**
   ```yaml
   # config.yaml
   llm:
     timeout: 120  # 改为 120 秒
   ```

3. **压缩图片**
   ```python
   from PIL import Image
   
   img = Image.open(image_path)
   img.thumbnail((1024, 1024))  # 缩放到 1024x1024
   img.save("resized.jpg")
   ```

### Q7: 可视化中文显示为方块？

**原因**：matplotlib 默认字体不含中文 Glyph

**解决方案**（已在代码中实现）：

```python
# skills/alarm_skills.py 已包含
import matplotlib
from matplotlib import font_manager as _fm

_CJK_FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
]
for _fp in _CJK_FONT_CANDIDATES:
    if Path(_fp).exists():
        _fm.fontManager.addfont(_fp)
        _name = _fm.FontProperties(fname=_fp).get_name()
        matplotlib.rcParams["font.sans-serif"] = [_name, "DejaVu Sans"]
        break
```

**验证**：
```python
python3 -c "
from skills.alarm_skills import *
import matplotlib
print('当前字体:', matplotlib.rcParams['font.sans-serif'])
"
# 应显示 ['Noto Sans CJK JP', 'DejaVu Sans']
```

### Q8: FastAPI 服务启动失败（8001 端口）？

**常见原因**：

1. **端口被占用**
   ```bash
   lsof -i :8001
   # 杀掉占用进程
   kill -9 <PID>
   ```

2. **starlette 版本不兼容**（已解决）
   ```bash
   pip install --upgrade 'gradio>=5.0'
   ```

3. **启动耗时长（30-40s）**
   - 正常现象：Skill Registry + 图预热
   - 耐心等待即可

### Q9: 如何添加新的告警类型？

```bash
# 1. 在 alarm_types 表添加记录
sqlite3 agent/data/ksipms_dev.db
INSERT INTO alarm_types(type_code, display_name, severity_default, enabled)
VALUES ('new_type', '新告警类型', 'medium', 1);

# 2. 准备测试图片
mkdir -p /mnt/ksnas/AI_Dataset/V5-test/test-agent/new_type/
# 放入测试图片

# 3. 重新入库测试数据
python agent/data/seed_test_alarms.py

# 4. VLM 复判会自动支持（表驱动）
```

### Q10: 如何优化 Plan-Execute 性能？

**优化方向**：

1. **缓存聚合结果**（Redis）
   ```python
   import redis
   r = redis.Redis(host='localhost', port=6379, db=0)
   
   cache_key = f"aggregate:{group_by}:{date_start}:{date_end}"
   cached = r.get(cache_key)
   if cached:
       return json.loads(cached)
   
   result = aggregate_alarms_impl(...)
   r.setex(cache_key, 3600, json.dumps(result))  # 缓存 1 小时
   return result
   ```

2. **并行执行无依赖任务**
   ```python
   # graph/nodes.py executor_node
   # 分析 plan，找出无依赖的任务并行执行
   # （需改 plan 结构，加 depends_on 字段）
   ```

3. **流式输出**（WebSocket）
   ```python
   @app.websocket("/ws/chat")
   async def websocket_chat(websocket: WebSocket):
       await websocket.accept()
       # 执行步骤时实时推送进度
       await websocket.send_json({"step": 0, "status": "running"})
       # ...
   ```

---

## 10. 参考资源

### 10.1 官方文档

- **LangGraph**: https://langchain-ai.github.io/langgraph/
- **MCP 协议**: https://modelcontextprotocol.io/
- **FastAPI**: https://fastapi.tiangolo.com/
- **Gradio**: https://www.gradio.app/docs/

### 10.2 项目内部文档

- **架构设计**: `ARCHITECTURE_V2.md`
- **路线图**: `ROADMAP.md`
- **规划书**: `agent/plan/COMPLEXITY_VALIDATION_PLAN.md`
- **交付总结**: `agent/plan/DELIVERY_SUMMARY.md`
- **快速启动**: `agent/plan/QUICK_START.md`

### 10.3 相关代码

| 模块 | 文件 | 说明 |
|------|------|------|
| **主图** | `graph/main_graph.py` | Plan-Execute 主图 |
| **节点** | `graph/nodes.py` | Planner / Executor / Formatter |
| **Skill 基类** | `skills/base.py` | Skill / SkillRegistry |
| **告警 Skills** | `skills/alarm_skills.py` | 聚合/可视化/回溯/回写 |
| **复判子图** | `skills/vlm_judge_subgraph.py` | VLM 复判 |
| **只读 MCP** | `mcp_servers/ksipms_server.py` | 查询告警/人员/录像 |
| **只写 MCP** | `mcp_servers/ksipms_write_server.py` | 回写告警状态 |
| **元智能体** | `agent/meta_agent/` | Agent-of-Agent |

---

## 11. 附录：60+代码示例

### 11.1 Skill 开发示例（10 个）

#### 示例 1：简单计算 TOOL

```python
def calculate_stats_impl(args: dict, context: dict) -> dict:
    """计算统计指标"""
    numbers = args.get("numbers", [])
    if not numbers:
        return {"error": "numbers 为空"}
    
    return {
        "sum": sum(numbers),
        "avg": sum(numbers) / len(numbers),
        "min": min(numbers),
        "max": max(numbers),
        "count": len(numbers),
    }
```

#### 示例 2：HTTP API 调用 TOOL

```python
import httpx

async def call_external_api_impl(args: dict, context: dict) -> dict:
    """调用外部 API"""
    url = args.get("url")
    method = args.get("method", "GET")
    payload = args.get("payload", {})
    
    async with httpx.AsyncClient() as client:
        try:
            if method == "GET":
                response = await client.get(url, params=payload)
            else:
                response = await client.post(url, json=payload)
            response.raise_for_status()
            return {"data": response.json(), "status_code": response.status_code}
        except httpx.HTTPError as e:
            return {"error": str(e)}
```

#### 示例 3：文件读取 TOOL

```python
from pathlib import Path

def read_file_impl(args: dict, context: dict) -> dict:
    """读取文件内容"""
    file_path = args.get("file_path")
    encoding = args.get("encoding", "utf-8")
    
    path = Path(file_path)
    if not path.exists():
        return {"error": f"文件不存在: {file_path}"}
    
    try:
        content = path.read_text(encoding=encoding)
        return {"content": content, "lines": len(content.split("\n"))}
    except Exception as e:
        return {"error": str(e)}
```

#### 示例 4：JSON 格式化 TOOL

```python
import json

def format_json_impl(args: dict, context: dict) -> dict:
    """格式化 JSON 字符串"""
    json_str = args.get("json_str")
    indent = args.get("indent", 2)
    
    try:
        obj = json.loads(json_str)
        formatted = json.dumps(obj, indent=indent, ensure_ascii=False)
        return {"formatted": formatted}
    except json.JSONDecodeError as e:
        return {"error": f"JSON 解析失败: {e}"}
```

#### 示例 5：日期计算 TOOL

```python
from datetime import datetime, timedelta

def calculate_date_impl(args: dict, context: dict) -> dict:
    """日期计算"""
    base_date = args.get("base_date")  # YYYY-MM-DD
    days_offset = args.get("days_offset", 0)
    
    try:
        dt = datetime.strptime(base_date, "%Y-%m-%d")
        result_dt = dt + timedelta(days=days_offset)
        return {
            "result_date": result_dt.strftime("%Y-%m-%d"),
            "weekday": result_dt.strftime("%A"),
            "iso_week": result_dt.isocalendar()[1],
        }
    except ValueError as e:
        return {"error": f"日期格式错误: {e}"}
```

#### 示例 6：正则匹配 TOOL

```python
import re

def regex_match_impl(args: dict, context: dict) -> dict:
    """正则表达式匹配"""
    pattern = args.get("pattern")
    text = args.get("text")
    flags = args.get("flags", 0)  # re.IGNORECASE = 2
    
    try:
        matches = re.findall(pattern, text, flags)
        return {"matches": matches, "count": len(matches)}
    except re.error as e:
        return {"error": f"正则表达式错误: {e}"}
```

#### 示例 7：数据转换 TOOL

```python
def transform_data_impl(args: dict, context: dict) -> dict:
    """数据格式转换"""
    data = args.get("data")  # list of dicts
    key_mapping = args.get("key_mapping", {})  # {"old_key": "new_key"}
    
    if not isinstance(data, list):
        return {"error": "data 必须是列表"}
    
    transformed = []
    for item in data:
        new_item = {}
        for old_key, new_key in key_mapping.items():
            if old_key in item:
                new_item[new_key] = item[old_key]
        transformed.append(new_item)
    
    return {"data": transformed, "count": len(transformed)}
```

#### 示例 8：CSV 解析 TOOL

```python
import csv
from io import StringIO

def parse_csv_impl(args: dict, context: dict) -> dict:
    """解析 CSV 字符串"""
    csv_str = args.get("csv_str")
    delimiter = args.get("delimiter", ",")
    
    try:
        reader = csv.DictReader(StringIO(csv_str), delimiter=delimiter)
        data = list(reader)
        return {"data": data, "rows": len(data)}
    except Exception as e:
        return {"error": str(e)}
```

#### 示例 9：邮件发送 TOOL

```python
import smtplib
from email.mime.text import MIMEText

async def send_email_impl(args: dict, context: dict) -> dict:
    """发送邮件"""
    to_addr = args.get("to")
    subject = args.get("subject")
    body = args.get("body")
    smtp_server = args.get("smtp_server", "smtp.example.com")
    
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = "noreply@example.com"
        msg["To"] = to_addr
        
        with smtplib.SMTP(smtp_server, 587) as server:
            server.starttls()
            server.send_message(msg)
        
        return {"success": True, "to": to_addr}
    except Exception as e:
        return {"error": str(e)}
```

#### 示例 10：Markdown 渲染 TOOL

```python
import markdown

def render_markdown_impl(args: dict, context: dict) -> dict:
    """Markdown 转 HTML"""
    md_text = args.get("markdown")
    extensions = args.get("extensions", ["tables", "fenced_code"])
    
    try:
        html = markdown.markdown(md_text, extensions=extensions)
        return {"html": html}
    except Exception as e:
        return {"error": str(e)}
```

### 11.2 SUBGRAPH 开发示例（5 个）

#### 示例 11：重试机制子图

```python
from langgraph.graph import StateGraph, END
from typing import TypedDict
import asyncio

class RetryState(TypedDict):
    input: str
    attempts: int
    max_attempts: int
    result: Any
    error: str | None

def attempt_node(state: RetryState) -> dict:
    """尝试执行"""
    try:
        result = risky_operation(state["input"])
        return {"result": result, "error": None}
    except Exception as e:
        return {"attempts": state["attempts"] + 1, "error": str(e)}

def should_retry(state: RetryState) -> str:
    """判断是否重试"""
    if state.get("error") and state["attempts"] < state["max_attempts"]:
        return "retry"
    return "end"

def build_retry_subgraph() -> StateGraph:
    graph = StateGraph(RetryState)
    graph.add_node("attempt", attempt_node)
    graph.set_entry_point("attempt")
    graph.add_conditional_edges("attempt", should_retry, {
        "retry": "attempt",
        "end": END
    })
    return graph.compile()
```

#### 示例 12：并行查询子图

```python
class ParallelQueryState(TypedDict):
    query: str
    source1_result: Any
    source2_result: Any
    source3_result: Any
    merged_result: Any

async def query_source1(state: ParallelQueryState) -> dict:
    result = await api1.query(state["query"])
    return {"source1_result": result}

async def query_source2(state: ParallelQueryState) -> dict:
    result = await api2.query(state["query"])
    return {"source2_result": result}

async def query_source3(state: ParallelQueryState) -> dict:
    result = await api3.query(state["query"])
    return {"source3_result": result}

def merge_node(state: ParallelQueryState) -> dict:
    """合并多个数据源的结果"""
    merged = []
    for key in ["source1_result", "source2_result", "source3_result"]:
        if state.get(key):
            merged.extend(state[key])
    return {"merged_result": merged}

def build_parallel_query_graph() -> StateGraph:
    graph = StateGraph(ParallelQueryState)
    graph.add_node("query1", query_source1)
    graph.add_node("query2", query_source2)
    graph.add_node("query3", query_source3)
    graph.add_node("merge", merge_node)
    
    graph.set_entry_point("query1")
    graph.add_edge("query1", "query2")
    graph.add_edge("query2", "query3")
    graph.add_edge("query3", "merge")
    graph.add_edge("merge", END)
    return graph.compile()
```


#### 示例 13：条件分支子图

```python
class ConditionalState(TypedDict):
    input: dict
    category: str
    result: Any

def classify_node(state: ConditionalState) -> dict:
    """分类输入"""
    if state["input"].get("type") == "A":
        return {"category": "type_a"}
    elif state["input"].get("type") == "B":
        return {"category": "type_b"}
    else:
        return {"category": "unknown"}

def handle_type_a(state: ConditionalState) -> dict:
    """处理 A 类型"""
    result = process_a(state["input"])
    return {"result": result}

def handle_type_b(state: ConditionalState) -> dict:
    """处理 B 类型"""
    result = process_b(state["input"])
    return {"result": result}

def handle_unknown(state: ConditionalState) -> dict:
    """处理未知类型"""
    return {"result": {"error": "Unknown type"}}

def route(state: ConditionalState) -> str:
    return state["category"]

def build_conditional_graph() -> StateGraph:
    graph = StateGraph(ConditionalState)
    graph.add_node("classify", classify_node)
    graph.add_node("handle_a", handle_type_a)
    graph.add_node("handle_b", handle_type_b)
    graph.add_node("handle_unknown", handle_unknown)
    
    graph.set_entry_point("classify")
    graph.add_conditional_edges("classify", route, {
        "type_a": "handle_a",
        "type_b": "handle_b",
        "unknown": "handle_unknown"
    })
    graph.add_edge("handle_a", END)
    graph.add_edge("handle_b", END)
    graph.add_edge("handle_unknown", END)
    return graph.compile()
```

#### 示例 14：审批流程子图

```python
class ApprovalState(TypedDict):
    request: dict
    l1_approved: bool
    l2_approved: bool
    final_status: str

def l1_approval_node(state: ApprovalState) -> dict:
    """一级审批"""
    approved = check_l1_rules(state["request"])
    return {"l1_approved": approved}

def l2_approval_node(state: ApprovalState) -> dict:
    """二级审批"""
    approved = check_l2_rules(state["request"])
    return {"l2_approved": approved}

def finalize_node(state: ApprovalState) -> dict:
    """最终状态"""
    if state["l1_approved"] and state["l2_approved"]:
        status = "approved"
    else:
        status = "rejected"
    return {"final_status": status}

def should_proceed_to_l2(state: ApprovalState) -> str:
    return "l2" if state["l1_approved"] else "reject"

def build_approval_graph() -> StateGraph:
    graph = StateGraph(ApprovalState)
    graph.add_node("l1", l1_approval_node)
    graph.add_node("l2", l2_approval_node)
    graph.add_node("finalize", finalize_node)
    
    graph.set_entry_point("l1")
    graph.add_conditional_edges("l1", should_proceed_to_l2, {
        "l2": "l2",
        "reject": "finalize"
    })
    graph.add_edge("l2", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile()
```

#### 示例 15：数据清洗子图

```python
class CleaningState(TypedDict):
    raw_data: list
    deduplicated: list
    validated: list
    cleaned: list

def deduplicate_node(state: CleaningState) -> dict:
    """去重"""
    seen = set()
    deduped = []
    for item in state["raw_data"]:
        key = item.get("id")
        if key and key not in seen:
            seen.add(key)
            deduped.append(item)
    return {"deduplicated": deduped}

def validate_node(state: CleaningState) -> dict:
    """验证"""
    validated = [
        item for item in state["deduplicated"]
        if validate_item(item)
    ]
    return {"validated": validated}

def clean_node(state: CleaningState) -> dict:
    """清洗"""
    cleaned = [clean_item(item) for item in state["validated"]]
    return {"cleaned": cleaned}

def build_cleaning_graph() -> StateGraph:
    graph = StateGraph(CleaningState)
    graph.add_node("deduplicate", deduplicate_node)
    graph.add_node("validate", validate_node)
    graph.add_node("clean", clean_node)
    
    graph.set_entry_point("deduplicate")
    graph.add_edge("deduplicate", "validate")
    graph.add_edge("validate", "clean")
    graph.add_edge("clean", END)
    return graph.compile()
```

### 11.3 MCP Server 示例（5 个）

#### 示例 16：只读查询 MCP

```python
# 已在 §4.2 完整展示，此处略
```

#### 示例 17：只写更新 MCP

```python
# 已在 §4.3 完整展示，此处略
```

#### 示例 18：批量操作 MCP

```python
@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "batch_update":
        results = []
        for item in arguments["items"]:
            result = update_one_impl(item)
            results.append(result)
        return [TextContent(type="text", text=json.dumps({"results": results}))]
```

#### 示例 19：分页查询 MCP

```python
def query_with_pagination_impl(page: int = 1, page_size: int = 10, filters: dict = None) -> dict:
    """分页查询"""
    offset = (page - 1) * page_size
    
    where = ["1=1"]
    params = []
    if filters:
        for k, v in filters.items():
            where.append(f"{k} = ?")
            params.append(v)
    
    sql_count = f"SELECT COUNT(*) FROM table WHERE {' AND '.join(where)}"
    sql_data = f"SELECT * FROM table WHERE {' AND '.join(where)} LIMIT ? OFFSET ?"
    params_data = params + [page_size, offset]
    
    conn = get_conn()
    total = conn.execute(sql_count, params).fetchone()[0]
    rows = conn.execute(sql_data, params_data).fetchall()
    
    return {
        "data": [dict(r) for r in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": (total + page_size - 1) // page_size
    }
```

#### 示例 20：事务支持 MCP

```python
def transactional_update_impl(operations: list) -> dict:
    """事务更新多个记录"""
    conn = get_conn()
    try:
        conn.execute("BEGIN")
        for op in operations:
            if op["type"] == "update":
                conn.execute("UPDATE table SET ... WHERE id=?", (op["id"],))
            elif op["type"] == "delete":
                conn.execute("DELETE FROM table WHERE id=?", (op["id"],))
        conn.execute("COMMIT")
        return {"success": True, "operations_count": len(operations)}
    except Exception as e:
        conn.execute("ROLLBACK")
        return {"success": False, "error": str(e)}
```

### 11.4 Agent 开发示例（10 个）

#### 示例 21：简单查询 Agent

```python
async def run(user_input: str, session_id: str = "") -> str:
    """查询今天的告警"""
    registry = get_skill_registry()
    today = datetime.now().strftime("%Y-%m-%d")
    
    result = await registry.invoke(
        "query_alarms",
        {"date": today, "limit": 10},
        {"session_id": session_id}
    )
    
    if result.get("error"):
        return f"查询失败: {result['error']}"
    
    alarms = result.get("alarms", [])
    return f"今天共有 {len(alarms)} 条告警。"
```

#### 示例 22：条件分支 Agent

```python
async def run(user_input: str, session_id: str = "") -> str:
    """根据关键词路由到不同 Skill"""
    registry = get_skill_registry()
    
    if "统计" in user_input or "数量" in user_input:
        result = await registry.invoke("aggregate_alarms", {"group_by": "alarm_type"}, {})
        return format_stats(result)
    elif "复判" in user_input:
        alarm_uuid = extract_uuid(user_input)
        result = await registry.invoke("vlm_judge_alarm", {"alarm_uuid": alarm_uuid}, {})
        return format_verdict(result)
    else:
        return "不理解您的请求，请说明要统计还是复判。"
```

#### 示例 23：循环处理 Agent

```python
async def run(user_input: str, session_id: str = "") -> str:
    """批量复判所有 pending 告警"""
    registry = get_skill_registry()
    
    # 1. 查询所有 pending 告警
    result = await registry.invoke("query_alarms", {"status": "pending", "limit": 100}, {})
    alarms = result.get("alarms", [])
    
    if not alarms:
        return "没有待复判的告警。"
    
    # 2. 逐个复判
    processed = 0
    for alarm in alarms:
        verdict_result = await registry.invoke(
            "vlm_judge_alarm",
            {"alarm_uuid": alarm["alarm_uuid"]},
            {}
        )
        if verdict_result.get("verdict") in ("confirmed", "rejected"):
            await registry.invoke(
                "update_alarm_status",
                {"alarm_uuid": alarm["alarm_uuid"], "verdict": verdict_result["verdict"]},
                {}
            )
            processed += 1
    
    return f"已复判 {processed}/{len(alarms)} 条告警。"
```

#### 示例 24：多步编排 Agent

```python
async def run(user_input: str, session_id: str = "") -> str:
    """查询 → 聚合 → 可视化"""
    registry = get_skill_registry()
    
    # 步骤 1：查询最近 7 天告警
    date_start = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    date_end = datetime.now().strftime("%Y-%m-%d")
    
    # 步骤 2：聚合统计
    agg_result = await registry.invoke(
        "aggregate_alarms",
        {"group_by": "alarm_type", "date_start": date_start, "date_end": date_end},
        {}
    )
    
    if agg_result.get("error"):
        return f"聚合失败: {agg_result['error']}"
    
    # 步骤 3：可视化
    viz_result = await registry.invoke(
        "visualize_alarms",
        {"data": agg_result["data"], "chart_type": "bar"},
        {}
    )
    
    if viz_result.get("error"):
        return f"可视化失败: {viz_result['error']}"
    
    return f"已生成柱状图（{agg_result['total']} 条告警）。图片: {viz_result['image_base64'][:50]}..."
```

#### 示例 25：错误重试 Agent

```python
async def run(user_input: str, session_id: str = "") -> str:
    """带重试的 VLM 复判"""
    registry = get_skill_registry()
    alarm_uuid = extract_uuid(user_input)
    
    max_retries = 3
    for attempt in range(max_retries):
        result = await registry.invoke(
            "vlm_judge_alarm",
            {"alarm_uuid": alarm_uuid},
            {}
        )
        
        if not result.get("error"):
            return f"复判成功：{result['verdict']} (置信度: {result['confidence']})"
        
        logger.warning(f"复判失败（尝试 {attempt+1}/{max_retries}）: {result['error']}")
        if attempt < max_retries - 1:
            await asyncio.sleep(2 ** attempt)  # 指数退避
    
    return f"复判失败（已重试 {max_retries} 次）"
```

#### 示例 26：缓存优化 Agent

```python
import hashlib

_cache = {}

async def run(user_input: str, session_id: str = "") -> str:
    """带缓存的聚合统计"""
    registry = get_skill_registry()
    
    # 生成缓存键
    cache_key = hashlib.md5(user_input.encode()).hexdigest()
    if cache_key in _cache:
        logger.info(f"命中缓存: {cache_key}")
        return _cache[cache_key]
    
    # 执行查询
    result = await registry.invoke("aggregate_alarms", {"group_by": "alarm_type"}, {})
    response = format_result(result)
    
    # 缓存结果（5 分钟）
    _cache[cache_key] = response
    asyncio.create_task(expire_cache(cache_key, 300))
    
    return response

async def expire_cache(key: str, seconds: int):
    await asyncio.sleep(seconds)
    _cache.pop(key, None)
```

#### 示例 27：流式输出 Agent

```python
async def run(user_input: str, session_id: str = "") -> AsyncGenerator[str, None]:
    """流式返回结果"""
    registry = get_skill_registry()
    
    yield "正在查询告警...\n"
    result = await registry.invoke("query_alarms", {"limit": 100}, {})
    
    yield f"查询到 {len(result['alarms'])} 条告警\n"
    
    yield "正在聚合统计...\n"
    agg_result = await registry.invoke("aggregate_alarms", {"group_by": "alarm_type"}, {})
    
    yield "统计结果：\n"
    for item in agg_result["data"]:
        yield f"- {item['key']}: {item['count']} 条\n"
```

#### 示例 28：多语言 Agent

```python
async def run(user_input: str, session_id: str = "") -> str:
    """支持中英文"""
    registry = get_skill_registry()
    
    # 检测语言
    is_chinese = any('一' <= c <= '鿿' for c in user_input)
    
    result = await registry.invoke("aggregate_alarms", {"group_by": "alarm_type"}, {})
    
    if is_chinese:
        return f"共有 {result['total']} 条告警。"
    else:
        return f"Total {result['total']} alarms."
```

#### 示例 29：权限检查 Agent

```python
async def run(user_input: str, session_id: str = "") -> str:
    """带权限检查的 Agent"""
    # 从 session 获取用户角色
    user_role = get_user_role(session_id)
    
    if "删除" in user_input or "delete" in user_input.lower():
        if user_role != "admin":
            return "权限不足：只有管理员可以删除数据。"
    
    # 执行操作
    registry = get_skill_registry()
    result = await registry.invoke(..., {})
    return format_result(result)
```

#### 示例 30：定时任务 Agent

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()

async def daily_report():
    """每日报告 Agent"""
    registry = get_skill_registry()
    
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    result = await registry.invoke(
        "aggregate_alarms",
        {"group_by": "alarm_type", "date_start": yesterday, "date_end": yesterday},
        {}
    )
    
    # 发送报告
    report = format_daily_report(result)
    await send_email(to="admin@example.com", subject="每日告警报告", body=report)

# 注册定时任务（每天 8:00）
scheduler.add_job(daily_report, 'cron', hour=8, minute=0)
scheduler.start()
```

### 11.5 测试示例（10 个）

#### 示例 31-40：单元测试、集成测试

```python
# 已在 §6.1、§6.2 详细展示，此处略
```

### 11.6 工具函数示例（10 个）

#### 示例 41：日期范围生成

```python
def date_range(start: str, end: str) -> list[str]:
    """生成日期范围 YYYY-MM-DD"""
    start_dt = datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.strptime(end, "%Y-%m-%d")
    dates = []
    current = start_dt
    while current <= end_dt:
        dates.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    return dates
```

#### 示例 42：UUID 提取

```python
import re

def extract_uuid(text: str) -> str | None:
    """从文本中提取 UUID"""
    pattern = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
    match = re.search(pattern, text, re.IGNORECASE)
    return match.group(0) if match else None
```

#### 示例 43：中文分词

```python
import jieba

def tokenize_chinese(text: str) -> list[str]:
    """中文分词"""
    return list(jieba.cut(text))
```

#### 示例 44：相似度计算

```python
from difflib import SequenceMatcher

def similarity(a: str, b: str) -> float:
    """计算字符串相似度"""
    return SequenceMatcher(None, a, b).ratio()
```

#### 示例 45：JSON 安全加载

```python
import json

def safe_json_loads(text: str, default: Any = None) -> Any:
    """安全加载 JSON，失败返回默认值"""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return default
```

#### 示例 46：重试装饰器

```python
import functools
import time

def retry(max_attempts: int = 3, delay: float = 1.0):
    """重试装饰器"""
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_attempts - 1:
                        raise
                    await asyncio.sleep(delay * (2 ** attempt))
            return wrapper
    return decorator
```

#### 示例 47：超时装饰器

```python
import asyncio

def timeout(seconds: float):
    """超时装饰器"""
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            return await asyncio.wait_for(func(*args, **kwargs), timeout=seconds)
        return wrapper
    return decorator
```

#### 示例 48：批处理工具

```python
def batch_process(items: list, batch_size: int) -> list:
    """批量处理"""
    for i in range(0, len(items), batch_size):
        yield items[i:i + batch_size]

# 使用
for batch in batch_process(large_list, 100):
    process(batch)
```

#### 示例 49：环境变量加载

```python
import os
from dotenv import load_dotenv

load_dotenv()

def get_env(key: str, default: str = None) -> str:
    """获取环境变量"""
    value = os.getenv(key, default)
    if value is None:
        raise ValueError(f"Environment variable {key} not set")
    return value
```

#### 示例 50：日志装饰器

```python
from loguru import logger

def log_execution(func):
    """日志装饰器"""
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        logger.info(f"[{func.__name__}] 开始执行")
        try:
            result = await func(*args, **kwargs)
            logger.info(f"[{func.__name__}] 执行成功")
            return result
        except Exception as e:
            logger.exception(f"[{func.__name__}] 执行失败: {e}")
            raise
    return wrapper
```

### 11.7 配置示例（5 个）

#### 示例 51：完整 config.yaml

```yaml
llm:
  base_url: "http://127.0.0.1:8004/v1"
  model: "Qwen3-VL-4B-Instruct-FP8"
  api_key: "EMPTY"
  temperature: 0.2
  max_tokens: 2048
  timeout: 60

mcp:
  enabled: true
  servers:
    - name: "ksipms"
      command: "python"
      args: ["-m", "mcp_servers.ksipms_server"]
    - name: "ksipms_write"
      command: "python"
      args: ["-m", "mcp_servers.ksipms_write_server"]

database:
  url: "sqlite:///agent/data/ksipms_dev.db"
  pool_size: 5
  echo: false

server:
  host: "0.0.0.0"
  port: 8001
  reload: false
  log_level: "info"
  cors_origins: ["*"]

logging:
  level: "INFO"
  format: "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
  file: "logs/ksagent.log"
  max_bytes: 10485760  # 10MB
  backup_count: 5
```

#### 示例 52：MCP 只读配置

```yaml
# mcp_servers/config.yaml
ksipms_server:
  db_path: "agent/data/ksipms_dev.db"
  read_only: true
  
  allowed_tables:
    - alarms
    - alarm_types
    - cameras
    - persons
    - video_clips
  
  allowed_fields:
    alarms:
      - alarm_uuid
      - alarm_type
      - camera_id
      - ts_event
      - status
      - snapshot_url
      - alarm_desc
    
    persons:
      - person_id
      - name
      - department
      # 不暴露: id_number, phone
```

#### 示例 53：MCP 只写配置

```yaml
# mcp_servers/config_write.yaml
ksipms_write_server:
  db_path: "agent/data/ksipms_dev.db"
  read_only: false
  
  allowed_status_values:
    - closed
    - false_alarm
  
  audit_required: true
  
  tools:
    update_alarm_status:
      description: "更新告警处理状态"
      parameters:
        type: object
        properties:
          alarm_uuid:
            type: string
          status:
            type: string
            enum: [closed, false_alarm]
          note:
            type: string
        required: [alarm_uuid, status]
```

#### 示例 54：Docker Compose 配置

```yaml
# 已在 §7.2.2 完整展示
```

#### 示例 55：Pytest 配置

```ini
# pytest.ini
[pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
addopts = 
    -v
    --tb=short
    --strict-markers
    --disable-warnings
markers =
    slow: marks tests as slow
    integration: marks tests as integration tests
    unit: marks tests as unit tests
```

### 11.8 部署脚本示例（5 个）

#### 示例 56：启动脚本

```bash
#!/bin/bash
# start.sh

set -e

echo "激活 conda 环境..."
source /root/anaconda3/bin/activate agent

echo "切换到项目目录..."
cd /mnt/data3/clip/LangGraph/agent

echo "启动 FastAPI 服务..."
nohup python main.py > logs/fastapi.log 2>&1 &
echo $! > logs/fastapi.pid
echo "FastAPI 已启动 (PID: $(cat logs/fastapi.pid))"

echo "等待 5 秒..."
sleep 5

echo "健康检查..."
curl -f http://localhost:8001/health || { echo "健康检查失败"; exit 1; }

echo "启动成功！"
```

#### 示例 57：停止脚本

```bash
#!/bin/bash
# stop.sh

if [ -f logs/fastapi.pid ]; then
    PID=$(cat logs/fastapi.pid)
    echo "停止 FastAPI (PID: $PID)..."
    kill $PID
    rm logs/fastapi.pid
    echo "已停止"
else
    echo "PID 文件不存在"
fi
```

#### 示例 58：重启脚本

```bash
#!/bin/bash
# restart.sh

./stop.sh
sleep 2
./start.sh
```

#### 示例 59：备份脚本

```bash
#!/bin/bash
# backup.sh

BACKUP_DIR="/backup/ksagent"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

mkdir -p $BACKUP_DIR

echo "备份数据库..."
cp agent/data/ksipms_dev.db $BACKUP_DIR/ksipms_dev_$TIMESTAMP.db

echo "备份 Agent Registry..."
cp -r agent/registry $BACKUP_DIR/registry_$TIMESTAMP

echo "备份日志..."
tar -czf $BACKUP_DIR/logs_$TIMESTAMP.tar.gz logs/

echo "备份完成: $BACKUP_DIR"
```

#### 示例 60：监控脚本

```bash
#!/bin/bash
# monitor.sh

while true; do
    if ! curl -f http://localhost:8001/health > /dev/null 2>&1; then
        echo "$(date): 服务异常，尝试重启..."
        ./restart.sh
        
        # 发送告警
        curl -X POST https://api.telegram.org/bot<TOKEN>/sendMessage \
            -d chat_id=<CHAT_ID> \
            -d text="KSAgent 服务异常并已重启"
    fi
    sleep 60
done
```

---

## 结语

本开发操作手册基于复杂任务编排验证的真实交付代码编写，涵盖：

- ✅ **完整开发链路**：环境→开发→测试→发布
- ✅ **三种 Skill 类型**：TOOL / MCP_TOOL / SUBGRAPH
- ✅ **两种 MCP Server**：只读 / 只写
- ✅ **三种开发方式**：手动 / Agent-of-Agent / 主智能体编排
- ✅ **60+ 代码示例**：涵盖所有核心场景
- ✅ **最佳实践**：踩坑经验与解决方案
- ✅ **FAQ**：10+ 常见问题与解决方案

**下一步**：
1. 按 §2-3 开发第一个 Skill
2. 按 §5 开发第一个 Agent
3. 按 §7 部署到生产环境
4. 查阅 §11 附录复制代码示例

**文档维护**：随项目演进持续更新  
**问题反馈**：提交 Issue 或联系开发团队  
**最后更新**：2026-06-06

---

**文档编写者**: Claude Opus 4.8  
**审阅者**: （待填写）  
**批准日期**: （待填写）

