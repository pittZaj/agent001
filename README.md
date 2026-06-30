# KSAgent — 安全生产场景 AI 智能体平台

> 基于 LangGraph + Qwen3-VL + MCP 的多模态智能体平台，对接真实 KSIpms 综合管理平台
> **当前版本**: v4.0（真实平台对接 + RAG + 短期记忆 + P0 时延优化 + 方向三 Skill/提示词优化 全部完成）
> **最后更新**: 2026-06-30

---

## 📋 项目状态

| 阶段 | 内容 | 状态 |
|------|------|------|
| **阶段 1** | 最小可运行框架（FastAPI + LangGraph + VLM） | ✅ 已完成 |
| **阶段 2** | MCP 集成 + Skill Registry + Plan-Execute | ✅ 已完成 |
| **阶段 2.5** | 复杂任务编排能力验证（3 个端到端 Demo） | ✅ 已完成（2026-06-05） |
| **阶段 3** | RAG 知识库集成（Qdrant + BGE-M3 + reranker） | ✅ 已完成（2026-06-08） |
| **阶段 2.5.1** | 真实平台对接（HTTP 直连真实 MCP Server） | ✅ 已完成（2026-06-10） |
| **多模态聊天 + 短期记忆** | 类 ChatGPT 接口 / 会话隔离 / Redis 持久化 / SSE 流式 | ✅ 已完成 |
| **P0 时延优化** | MCP 连接复用 / fast-path 预路由 / 并发翻页 | ✅ 已完成（2026-06-22） |
| **方向三：Skill 与提示词优化** | T0~T10 共 11 项（见下文） | ✅ 已完成（2026-06-30） |

> 历史 README（v3.2，2026-06-10）多处已滞后，本版以 2026-06-30 现网代码为准重写。

---

## 🎯 核心能力

### 1. 自然语言操作（Plan-Execute）
文字/图文输入 → LLM 动态规划 → 统一调用工具 → 汇总响应。

- **Router**：按是否带图分流（带图走 VLM 直答，纯文本走 Planner 数据链路）
- **Planner**：从 Skill Registry 动态读取可用工具，生成 JSON 任务数组
  - 结构化输出（vLLM `guided_json`），必出合法数组，省去正则抠 JSON
  - 提示词分层（L1 稳定前缀 / L2 半静态字典 / L3 动态时间），利于 prefix caching
  - 防幻觉规则：区分"平台支持识别"与"数据库有数据"，权威 `event_type` 字典杜绝瞎猜编码
  - 当前日期注入：避免 LLM 用训练数据年份猜测时间
- **Executor**：通过 `registry.invoke()` 统一调用（MCP / 本地 / 子图），支持复杂步骤间传参
  - 纯引用 `{{step_0.field}}` 保留原类型；混合字符串 `"ID是 {{step_0.uuid}}"` 自动拼接；嵌套 `{{step_0.data.items[0].id}}`
- **Formatter**：智能压缩中间结果（剥离 `image_base64` 等大对象为占位符，避免超 token），统一回答骨架

### 2. 真实平台对接（MCP HTTP 直连）
从本地 SQLite 模拟切换到真实 KSIpms 综合管理平台。

- HTTP 直连真实 MCP Server（`http://127.0.0.1:6620/mcp`，`transport: http`）
- 19+ 真实工具动态注册（`ai_event_*` / `video_*` / `system_*` / 录像直播 / 压缩任务）
- 字段映射对齐（`alarm_uuid→uuid`、`alarm_type→event_type`、`camera_id→camera_name` 等）
- 跨事件循环连接修复：进程级长驻后台事件循环（`utils/async_loop.py`），MCP 连接全程复用，杜绝 `ClosedResourceError` / cancel scope 崩溃
- MCP 调用重试 + 超时分级：瞬时错误指数退避，业务错不重试（`config.yaml` `mcp.retry`）

### 3. 多模态告警复判（VLM 子图）
接收告警 + 截图，调用 Qwen3-VL-4B-FP8 二次确认。

- VLM 复判子图泛化，支持多类告警
- 端到端闭环：VLM 复判 → `verdict` 自动映射（confirmed→review_status=2 / rejected→3）→ 回写真实平台 `ai_event_deal` + 审计
- 支持 HTTP 图片 URL（拉取真实平台截图）

### 4. 知识库联动（RAG）
RAG 检索规章制度，引用条文回答违规问题。

- 自建轻量方案：Qdrant + BGE-M3 + BGE-reranker-v2-m3，资源占用 < 2GB
- `kb_regulation` Skill 已注册，Planner 自动判定是否检索
- 混合检索（语义 + 关键词）；分块策略可选（fixed_size 滑窗 / by_paragraph / by_title / by_separator）
- 知识库管理 Web（上传 / 检索调参 / 分块编辑 / 统计）
- 端到端验证通过（"未戴安全帽违反哪些规定？" → 返回条文 + 处罚标准，无幻觉）

### 5. 数据可视化
Matplotlib 生成统计图表。

- `aggregate_alarms`（按类型/日期/区域/摄像头聚合）+ `visualize_alarms`（柱状图/折线图/饼图）
- 大对象剥离避免超 token；中文字体加载

### 6. 短期记忆 + 多会话 + 流式输出
- 会话级记忆（`thread_id` 隔离），Redis 持久化（`graph/redis_checkpoint.py`），滑动窗口防 token 爆炸
- 多会话管理 API（`/api/v1/sessions` 增删改查）
- SSE 流式输出（`stream=true`），逐字下发 token + 进度文案（19+ 工具全中文进度）

---

## 🏗️ 技术架构

```
┌──────────────────────────────────────────────────────────┐
│  FastAPI 应用层 (8001)                                     │
│  /api/v1/chat (文本/图文/SSE流式)  /api/v1/judge           │
│  /api/v1/sessions (多会话 CRUD)   /health                  │
│  api/kb_routes.py (知识库)                                 │
└───────────────────────┬────────────────────────────────────┘
                        │
┌───────────────────────▼────────────────────────────────────┐
│  LangGraph 编排层 (Plan-Execute)                            │
│  route_by_modality → [pre_route fast-path] →                │
│  Planner → Executor(loop) → Formatter                       │
│  · 短期记忆(memory.py) · 流式(streaming.py) · 会话(session) │
└───────────┬───────────────────────────┬─────────────────────┘
            │ 动态读取                   │ 统一调用
┌───────────▼───────────────────────────▼─────────────────────┐
│  Skill Registry (统一工具注册表，动态发现)                   │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ MCP_TOOL : ai_event_* / video_* / system_* (HTTP)   │   │
│  │ TOOL     : aggregate / visualize / fetch_context /   │   │
│  │            update_alarm_status / direct_response     │   │
│  │ SUBGRAPH : vlm_judge_alarm / kb_regulation           │   │
│  └─────────────────────────────────────────────────────┘   │
└──────┬──────────────────────────┬──────────────────┬────────┘
       │ MCP (HTTP)               │ 本地 Python       │ 子图
┌──────▼────────────┐   ┌─────────▼────────┐  ┌──────▼─────────┐
│ MCP Adapter       │   │ skills/*.py      │  │ vlm_judge /    │
│ (streamable_http) │   │ (聚合/可视化)    │  │ kb (Qdrant)    │
│ + 长驻事件循环    │   └──────────────────┘  └────────────────┘
│ + 重试/超时分级   │
└──────┬────────────┘
       │
┌──────▼──────────────────────┐   ┌──────────────────────────┐
│ 真实 KSIpms MCP Server      │   │ 业务数据库 MySQL          │
│ http://127.0.0.1:6620/mcp   │   │ (会话/审计等，6669/ksom)  │
│ 静态资源 :6611 (告警截图)   │   │ + Redis (记忆持久化)      │
└─────────────────────────────┘   └──────────────────────────┘
```

---

## 🛠️ 技术栈

| 组件 | 技术选型 | 说明 |
|---|---|---|
| **Web 框架** | FastAPI 0.136 + uvicorn | 异步 API、OpenAPI 文档、SSE 流式 |
| **智能体编排** | LangGraph 0.2.45 | Plan-Execute 状态图 |
| **LLM / VLM 后端** | Qwen3-VL-4B-Instruct-FP8 (vLLM) | `http://127.0.0.1:8004/v1`（FP8 量化） |
| **工具协议** | MCP 1.28（streamable_http） | HTTP 直连真实平台 `:6620/mcp`（19+ 工具） |
| **工具管理** | Skill Registry | 统一注册表，动态发现，跨事件循环复用 |
| **知识库** | Qdrant 1.18 + BGE-M3 + bge-reranker-v2-m3 | 自建轻量方案，混合检索，device=cuda:0 |
| **业务数据库** | MySQL（aiomysql / pymysql） | 会话、审计等（`127.0.0.1:6669/ksom`） |
| **记忆持久化** | Redis 5.2 | 会话级短期记忆，TTL 30 天 |
| **Web 管理界面** | Gradio 5.50（7860） | 对话调试 + 知识库管理两 Tab |
| **可视化** | Matplotlib 3.10 | 告警统计图表 |
| **嵌入/重排运行时** | torch 2.8.0 / transformers 4.57.6 | 钉版本（见环境约束） |

> ⚠️ **环境约束**：torch 2.8.0+cu128 / transformers 4.57.6 为钉版本，降版会导致 `cuda.is_available()==False` 或 reranker 报错。Python 3.10（勿用 3.14，mcp 对 starlette 有额外约束）。

---

## 📁 项目结构

```
agent/
├── README.md                    # 本文档
├── main.py                      # FastAPI 入口（chat/judge/sessions/health）
├── config.yaml                  # 主配置（llm/mcp/kb/database/redis/server）
├── requirements.txt             # 依赖（生产环境，Python 3.10）
├── requirements-vllm.txt        # vLLM 推理服务依赖（独立进程）
├── restart_api.sh               # 重启 FastAPI（8001）
├── restart_web.sh               # 重启 Gradio 调试平台（7860）
├── api/
│   └── kb_routes.py             # 知识库 API 路由
├── graph/                       # LangGraph 编排层
│   ├── state.py                 # AgentState 状态定义
│   ├── graph.py                 # 图构建（Plan-Execute + 流式入口）
│   ├── nodes.py                 # 核心节点：planner / executor / formatter
│   ├── planner_prompt.py        # ✨T2 提示词分层（L1/L2/L3）
│   ├── answer_skeleton.py       # ✨T10 统一回答骨架
│   ├── pre_router.py            # ✨P0 fast-path 预路由（闲聊/规章）
│   ├── pagination.py            # ✨P0 并发翻页 + ✨T6 created_at 排序兜底
│   ├── streaming.py             # SSE 流式 + 工具进度文案
│   ├── memory.py                # 短期记忆（历史拼接）
│   ├── session_manager.py       # 多会话管理
│   └── redis_checkpoint.py      # Redis 记忆持久化
├── skills/                      # Skill Registry
│   ├── base.py                  # Skill 抽象（TOOL/MCP_TOOL/SUBGRAPH）
│   ├── registry.py              # Registry 实现（动态发现 + invoke）
│   ├── init.py                  # Registry 初始化（注册本地/子图）
│   ├── mcp_skills.py            # MCP 工具注册
│   ├── mcp_watcher.py           # MCP 连接健康检测 + 自动重连
│   ├── event_types.py           # 告警类型权威字典（防幻觉）
│   ├── alarm_skills.py          # 聚合/可视化/上下文/回写
│   ├── vlm_judge_subgraph.py    # VLM 复判子图
│   └── kb/                      # RAG 知识库（service/config/skill）
├── mcp_adapter/
│   └── client.py                # MCP Client（streamable_http + 重试 + 长驻循环）
├── utils/
│   ├── llm_pool.py              # ✨P0 LLM 客户端单例池
│   ├── async_loop.py            # ✨P0 进程级长驻事件循环
│   └── vlm.py                   # Qwen3-VL 调用封装
├── eval/                        # ✨T0 轻量 planner 层回归评测
│   ├── cases.py                 # 26 条评测用例（12 维度）
│   ├── run_planner_eval.py      # 主入口（断言 + baseline diff）
│   └── baseline.json            # 基线快照
├── web/
│   └── app.py                   # Gradio 调试平台（对话 + 知识库）
├── agent/                       # Agent-of-Agent 元智能体（MVP）
├── models/schemas.py            # Pydantic 数据模型
├── migrations/                  # 数据库迁移
└── tests/                       # 测试
```

---

## 🚀 快速开始

### 前置服务
- **vLLM**（Qwen3-VL-4B-FP8）：监听 8004
- **真实 MCP Server**：`http://127.0.0.1:6620/mcp`（静态资源 6611）
- **Qdrant**：6333（RAG）
- **MySQL**：6669 / **Redis**：见 `config.yaml`

### 启动 FastAPI（生产接口）
```bash
source /root/anaconda3/bin/activate agent
cd /mnt/data3/clip/LangGraph/agent
bash restart_api.sh           # 或 python main.py
# 健康检查
curl http://127.0.0.1:8001/health
```

### 启动 Gradio 调试平台（演示）
```bash
cd /mnt/data3/clip/LangGraph/agent
bash restart_web.sh           # 端口 7860
# Tab: 主智能体对话测试 / 知识库管理
```

### 调用示例
```bash
# 文本对话
curl -X POST http://127.0.0.1:8001/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id":"demo","message":"统计每种告警类型数量并画柱状图"}'

# SSE 流式
curl -N -X POST http://127.0.0.1:8001/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id":"demo","message":"查最近5条告警","stream":true}'
```

### 经典 Demo
```text
统计每种告警类型数量并画柱状图        # 统计 + 可视化（多步编排）
复判告警 <UUID> 并回写它的状态        # VLM 复判闭环
未戴安全帽违反哪些规定？会被怎么处罚？ # RAG 知识库联动
查最近 5 条 AI 告警                   # 客户端 created_at 排序兜底
```

---

## 📡 API 速览

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查（LLM / MCP 在线状态） |
| POST | `/api/v1/chat` | 文本/图文对话；`stream=true` 走 SSE |
| POST | `/api/v1/judge` | 多模态告警复判（图片 + 检测结果） |
| GET/POST | `/api/v1/sessions` | 会话列表 / 新建 |
| GET/PUT/DELETE | `/api/v1/sessions/{id}` | 会话详情 / 改名 / 删除 |
| (kb_routes) | `/api/...` | 知识库上传 / 检索 / 管理 |

`/api/v1/chat` 响应含 `response` / `plan` / `tool_calls` / `session_id` 字段。

---

## ⚙️ 配置要点（config.yaml）

```yaml
llm:
  base_url: http://127.0.0.1:8004/v1   # vLLM Qwen3-VL-4B-FP8
  model: Qwen3-VL-4B-Instruct-FP8
  guided_json_enabled: true            # T1 结构化输出开关（可回退正则）
mcp:
  transport: http
  endpoint: http://127.0.0.1:6620/mcp  # 真实平台 MCP Server
  om_base_url: http://127.0.0.1:6611   # 告警截图静态资源
  retry: { max_attempts: 3, backoff_base: 0.5, backoff_max: 4.0 }  # T5 重试分级
  reconnect: { interval: 10, health_interval: 30 }                 # 自动重连
kb:
  device: cuda:0                       # RAG 模型显卡
  chunk_size: 300                      # 条文级检索更精准
  collection_name: safety_regulations
database:
  url: mysql+pymysql://...@127.0.0.1:6669/ksom
redis:
  enabled: true                        # 短期记忆持久化
  ttl: 2592000                         # 30 天
server:
  port: 8001
```

---

## 🧩 方向三：Skill 与提示词优化（T0~T10，已全部完成）

依据《平台优化建议书》§6，11 项任务全部落地（详见 `plan/DIRECTION3_COMPLETION_SUMMARY.md`）：

| 任务 | 内容 | 关键产出 |
|------|------|---------|
| **T0** | 轻量回归评测脚本（标尺先行） | `eval/`：26 条用例 + baseline diff，退出码可阻断回退 |
| **T1** | guided_json 结构化输出 | planner 必出合法 JSON 数组，配置开关可回退 |
| **T2** | 提示词分层（L1/L2/L3） | `planner_prompt.py`，利于 prefix caching + 可维护 |
| **T3** | Few-shot 覆盖发散意图 | 对比型样例（最近N条 vs 统计 / 不支持类型） |
| **T4** | 降级路径友好化 | 解析失败/异常对用户给友好话术，原文仅落日志 |
| **T5** | MCP 重试 + 超时分级 | 瞬时错误指数退避，业务错不重试 |
| **T6** | ai_event_list 排序兜底 | `recent` 标志 → 全量拉取 + created_at 降序 + 截断 |
| **T7** | 进度文案补全 | 19+ 工具全中文流式进度 |
| **T8** | 代码卫生（去重） | 清理重复定义 |
| **T9** | 字体加载说明 | 确认导入期一次性，零行为变更 |
| **T10** | formatter 两条出口统一 | `answer_skeleton.py` 统一回答骨架 |

**铁律**：所有提示词改动前后均跑 T0 回归，零退化（0 条 pass→fail），退出码 0。运行：
```bash
cd /mnt/data3/clip/LangGraph/agent && python -m eval.run_planner_eval
```

---

## ⚡ P0 时延优化（已完成，2026-06-22）

详见 `plan/LATENCY_OPTIMIZATION_EXECUTION_PLAN_2026-06-22.md` 及各 TASK 报告：

- **4.1 MCP 连接复用**：进程级长驻事件循环（`utils/async_loop.py`），一次图执行从 N 次握手降为 1 次
- **4.2 fast-path 预路由 + LLM 单例池**（`pre_router.py` / `llm_pool.py`）：闲聊/规章跳过完整 planner
- **4.3 并发翻页**（`pagination.py`）：`asyncio.gather` + 限流，多页拉取串行→并发

---

## 🔄 LangGraph 图结构

```
START → route_by_modality
          ├─ 带图 → vlm_chat（VLM 直答）
          └─ 纯文本 → [pre_route fast-path?]
                        ├─ 命中(闲聊/规章) → 直接出 plan
                        └─ 未命中 → Planner(LLM, guided_json)
                                      → Executor(loop, registry.invoke)
                                          → should_continue?(More→loop / Done)
                                              → Formatter → END
```

`AgentState`（`graph/state.py`）核心字段：`session_id` / `user_message` / `plan` / `current_task_idx` / `tool_results` / `step_outputs` / `final_response` / `messages`(短期记忆) / `error`。

---

## 📚 文档导航

**架构与开发**
- `ARCHITECTURE_V2.md` — 阶段2架构（MCP / Skill Registry / Plan-Execute）
- `DEVELOPER_GUIDE.md` — 开发操作手册（Skill/MCP/智能体，含大量示例）
- `HTTP 接口对接说明文档-V3.md` — 对外 HTTP 接口对接

**优化方案与完成报告**（`plan/`）
- `PLATFORM_OPTIMIZATION_PROPOSAL_2026-06-18.md` — 平台优化建议书（总纲）
- `SKILL_PROMPT_OPTIMIZATION_EXECUTION_PLAN_2026-06-23.md` — 方向三执行摘要
- `DIRECTION3_COMPLETION_SUMMARY.md` — 方向三整体完成汇总
- `LATENCY_OPTIMIZATION_EXECUTION_PLAN_2026-06-22.md` — P0 时延优化方案
- `TASK_T0~T10_*.md` — 方向三各任务完成报告
- `TASK_4_1~4_3_*.md` — P0 时延各任务完成报告

**阶段总结**（`plan/`）
- `STAGE_2_5_1_COMPLETION_SUMMARY.md` — 真实平台对接
- `RAG_COMPLETION_SUMMARY.md` — RAG 知识库集成
- `SHORT_TERM_MEMORY_COMPLETION_REPORT.md` — 短期记忆
- `MEMORY_PERSISTENCE_OPTIMIZATION_COMPLETION_REPORT.md` — Redis 记忆持久化

---

## 🔧 故障排查

**MCP 调用失败**
1. 确认真实 MCP Server 可达：`curl http://127.0.0.1:6620/mcp`
2. `/health` 查看 `mcp_online` 状态；`mcp_watcher` 会自动重连
3. 弱网下 T5 重试会自动指数退避（日志 `[MCP] 第N次失败…重试`）

**Planner 找不到工具**
```python
from skills import get_skill_registry
print([s.id for s in get_skill_registry().list_skills()])
```

**提示词改动验收**
```bash
python -m eval.run_planner_eval   # 看 baseline diff，有回退则退出码 1
```

**LLM 不可达**
```bash
curl http://127.0.0.1:8004/v1/models
```

---

## 🤝 贡献与维护

- 改提示词/规划逻辑前后必跑 `eval/`，禁止破坏防幻觉规则与语义区分
- 新增 MCP 工具时同步补 `streaming.py` 的进度文案（T7 已有启动期自检）
- 改 `nodes.py` 遵循"叠加不重写"，保留配置开关以便灰度回退
- 并行开发先 `git pull --rebase`，合并后跑 T0 回归再推送

---

**项目维护**: KSAgent 开发团队
**最后更新**: 2026-06-30
**版本**: v4.0（真实平台 + RAG + 记忆 + P0 时延 + 方向三 全部完成）
