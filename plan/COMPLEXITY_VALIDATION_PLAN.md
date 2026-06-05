# 复杂任务编排能力验证 — 实施规划书

> **版本**: v1.0 (2026-06-05)
> **目标**: 在 RAG 知识库开工之前，用一组覆盖监控告警平台真实业务的 Skill / MCP / 子智能体，验证当前 Plan-Execute 架构在多步骤、跨工具、多模态的复杂任务上的编排效果。
> **范围**: 阶段 2 之上的"编排能力增强 + 业务能力扩张"，**不**含 RAG 知识库（预留接口）。
> **基线**: `ARCHITECTURE_V2.md` 阶段2（MCP + Skill Registry + Plan-Execute 已就位）
> **平台特性**: 数据源对接监控（告警/录像/摄像头），核心业务包括告警查询、文搜图、图搜图、人脸检索、大模型复判。

---

## 0. 本规划与既有路线的关系

| 既有文档 | 本规划的关系 |
|----------|--------------|
| `ARCHITECTURE_V2.md` | 完全沿用阶段2架构（MCP + Registry + Plan-Execute），不重写底座 |
| `ROADMAP.md` 阶段3（RAG） | **暂缓**。本规划完成并通过验收后再启动 |
| `ROADMAP.md` 阶段4（Agent-of-Agent） | 本规划的"主智能体"用现有主图实现；生成式智能体仍可作为子能力被主智能体调度 |
| `DEVELOPER_GUIDE.md` | 新增的 Skill / MCP / 子图全部按其规范开发 |

**核心理念变化**：把验证重心从"架构是否能跑通一次工具调用"上移到"架构能否在多步骤、跨模态、需要回写、需要可视化的真实业务链路里端到端跑通"。

---

## 1. 当前架构的能力评估（资深工程师视角）

读完全部代码后，针对"复杂任务编排"这个目标，定位到 **5 个真正影响验证效果的短板**，全部需要在本轮补齐：

### 1.1 短板 1：Executor 不支持步骤间传参（**致命**，必须先修**）

**现状**：`graph/nodes.py` 的 `planner_node` 一次性生成全部任务的 args，`executor_node` 串行执行且无法将 `plan[N-1]` 的输出注入到 `plan[N]` 的输入。

**问题**：你举的核心用例——"查告警 → 拿到 camera_id+时间 → 回溯前后10秒录像"——本质就是**数据在步骤间流动**。当前架构做不到。

**修复方案**：
- 在 `executor_node` 执行每个任务后，将 `result` 写入 `state["step_outputs"]`（dict，key 为 step_idx）
- 下一步任务执行前，用简单模板语法（如 `{{step_0.camera_id}}`）从 `step_outputs` 取值注入到 args
- 或者让 Planner 输出带依赖关系的 plan（如 `depends_on: [0]`），Executor 动态拼接

**工作量**：`graph/nodes.py` 改 30 行。

---

### 1.2 短板 2：复判未进编排层（**阻塞核心用例**）

**现状**：复判走独立端点 `/api/v1/judge`，不是 Skill Registry 里的 Skill/Subgraph，Planner 无法把"复判"编排进 plan。

**问题**：要测"查告警 → 复判图片 → 写回结果"这种端到端链路，复判必须成为可调度的能力。

**修复方案**：
- 新建 `skills/vlm_judge_subgraph.py`，包装 `utils/vlm.py` 的 `VLMClient.judge_image` 为一个 `SUBGRAPH` 类型 Skill
- 输入：`alarm_uuid`（从库读 snapshot_url 或本地路径）或直接 `image_path`
- 输出：结构化判定结果 `{"alarm_type": ..., "verdict": ..., "confidence": ..., "reasoning": ...}`
- 注册到 Registry，Planner 可见

**工作量**：新文件 80 行。

---

### 1.3 短板 3：复判只认 4 类，库里有 8 类（**业务不全**）

**现状**：`utils/vlm.py` 硬编码 `smoking/helmet/phone/mask`，但 `alarm_types` 有 **8 类**（含 `fall_down/fire_smoke/intrusion/ppe_other`）。

**问题**：测试图片有 8 类，复判智能体跑不了后 4 类。

**修复方案**：
- `VLMClient.judge_image` 改为泛化提示词："图中是否存在 {alarm_type_display_name}？置信度？"
- 由调用方传入 `alarm_type` 和 `display_name`（从 `alarm_types` 表读），VLM 返回 `{"exists": bool, "confidence": float, "reasoning": str}`
- 子图内部先查 `alarm_types` 表拿到 display_name，再调 VLM

**工作量**：`utils/vlm.py` 改 40 行，子图内嵌表查询 20 行。

---

### 1.4 短板 4：MCP 全只读，复判结果无回写路径（**闭环断裂**）

**现状**：`mcp_servers/ksipms_server.py` 配置 `read_only: true`，无法写库。

**问题**：要把复判结论（确认/误报）写回 `alarms.status`、`processed_note`，当前没工具能做。

**修复方案**：
- 新建 `mcp_servers/ksipms_write_server.py`，专门处理**受控写操作**
- 暴露工具 `update_alarm_status(alarm_uuid, status, note)`，只允许更新 `status IN ('closed','false_alarm')`、`processed_note`、`processed_at`、`processed_by`（固定为 `agent:vlm_judge`）
- 配置**强审计**：每次写操作记 `audit_log`
- 注册为 MCP_TOOL，通过 Registry 可调用

**工作量**：新文件 150 行 + 配置 30 行。

---

### 1.5 短板 5：缺统计/聚合/可视化/录像回溯 Skill（**演示不完整**）

**现状**：只有 3 个基础查询工具（`query_alarms/person/video`），没有：
- 统计聚合（按类型/时间段/摄像头统计告警数）
- 可视化（折线图/柱状图/饼图，matplotlib 生成 base64）
- 录像回溯（给定告警，自动算前后 N 秒时间窗，调 `query_video`）

**问题**：你要求的"返回折线图或柱状图或饼状图"、"追溯告警前后十秒录像"，当前一个都做不了。

**修复方案**：
- 新建 `skills/alarm_skills.py`，实现 4 个本地 TOOL：
  1. `aggregate_alarms(date_start, date_end, group_by)` → 统计结果
  2. `visualize_alarms(data, chart_type)` → base64 图片
  3. `fetch_alarm_context(alarm_uuid, before_sec, after_sec)` → 录像片段列表
  4. 已在 1.4 说明：`update_alarm_status` 作为 MCP_TOOL

**工作量**：新文件 200 行。

---

### 1.6 短板 6：Planner 无重规划/错误恢复（**鲁棒性弱**）

**现状**：计划是线性的，一步失败整个流程卡住。

**问题**：真实场景里工具可能失败（录像文件缺失、VLM 超时），编排层应该能重规划或降级。

**修复方案**（本轮**不做**，列为后续增强）：
- 在 `executor_node` 失败时，重新调用 `planner_node`，传入 `{"failed_task": ..., "error": ...}`
- Planner 生成替代方案（如录像缺失时改为只返回快照）

**理由**：此项不影响"编排能力验证"的核心目标，且需要 Planner 提示词大改（+50 行），留给阶段 5"生产优化"。

---

## 2. 验证目标与成功标准

### 2.1 核心验证目标（3 个）

| 目标 | 说明 | 验证方式 |
|------|------|----------|
| **多步编排** | Planner 能生成 3-5 步 plan，Executor 正确串行执行并传参 | 用例："查今天的 no_helmet 告警，取第一条的图片复判，结果写回库" |
| **跨模态融合** | 文本查询 + 图像理解 + 结构化回写，无缝衔接 | 用例："这个告警图片是真的未戴安全帽吗？" → 查库 → VLM → 更新 status |
| **可视化输出** | 统计结果能生成图表返回给用户 | 用例："最近 7 天每种告警的趋势" → 折线图 base64 |

### 2.2 成功标准（可演示 Demo）

**Demo 1：告警复判闭环**
```
用户：检查今天所有 pending 状态的未戴安全帽告警，用大模型复判，把误报标记为 false_alarm
主智能体：
  步骤1: query_alarms(date=today, alarm_type=no_helmet, status=pending) → 3条
  步骤2: vlm_judge(alarm_uuid=xxx) → verdict: true, confidence: 0.92
  步骤3: vlm_judge(alarm_uuid=yyy) → verdict: false, confidence: 0.65 (误报)
  步骤4: update_alarm_status(yyy, status=false_alarm, note="VLM复判为误报")
  步骤5: 汇总 → "已复判 3 条，其中 1 条误报已标记"
```

**Demo 2：告警统计 + 可视化**
```
用户：最近 7 天每天的告警数量趋势，给我一个折线图
主智能体：
  步骤1: aggregate_alarms(date_start=7天前, date_end=今天, group_by=date) → {...}
  步骤2: visualize_alarms(data=步骤1结果, chart_type=line) → base64图片
  步骤3: 返回图片 + 摘要 "总计 275 条，峰值在 6月1日（52条）"
```

**Demo 3：告警溯源（步骤间传参验证）**
```
用户：查一下昨天 CAM-005 的 smoking 告警，给我看告警前后 10 秒的录像
主智能体：
  步骤1: query_alarms(date=昨天, camera_id=CAM-005, alarm_type=smoking) → alarm_uuid=aaa, ts_event=1234567890
  步骤2: fetch_alarm_context(alarm_uuid=aaa, before_sec=10, after_sec=10) → video_clips=[...]
  步骤3: 返回 "找到 1 个录像片段：/video/CAM-005/1234567890.mp4 (时长 20s)"
```

**验收标准**：
- ✅ 三个 Demo 在 Web Tab6 跑通，响应时间 < 10s
- ✅ 测试图片（40 张）全部入库，复判子图对 8 类告警均能正确调用
- ✅ 至少 1 次"3 步以上编排 + 步骤间传参"成功案例
- ✅ 生成至少 1 张可视化图表（折线/柱状/饼图）
- ✅ 至少 1 次复判结果成功写回数据库，audit_log 有记录

---

## 3. 技术方案详解

<!-- PLAN_SECTION_3_START -->

### 3.1 测试图片入库（解决数据准备）

**目标**：把 `/mnt/ksnas/AI_Dataset/V5-test/test-agent/` 下 8 类 40 张图片，每张生成一条 `alarms` 记录。

**实现**：`agent/data/seed_test_alarms.py`

**插入逻辑**：
- 遍历 8 个文件夹（type_code 为文件夹名）
- 每张图：
  - `alarm_uuid` = uuid4
  - `alarm_type` = 文件夹名（对应 alarm_types.type_code）
  - `camera_id` = 随机选一个 online 摄像头
  - `area_id` = 该摄像头的 area_id
  - `severity` = 从 alarm_types 表读 severity_default
  - `status` = 'pending'（待复判）
  - `ts_event` = NOW - 随机0-48小时（模拟最近2天的告警）
  - `snapshot_url` = 图片的**绝对路径**（如 `/mnt/ksnas/AI_Dataset/V5-test/test-agent/smoking/1-1.png`）
  - `model_conf` = 随机 0.75-0.95（模拟小模型置信度）
  - `alarm_desc` = "{display_name}告警 (测试数据)"
  - `person_id` = NULL（测试数据不关联人员）
  - `video_clip_id` = NULL
  - `processed_at/by/note` = NULL
  - `raw_payload` = JSON: `{"source": "test_dataset", "file": "文件名"}`
  - `created_at` = NOW

**验证**：
```bash
python agent/data/seed_test_alarms.py
sqlite3 agent/data/ksipms_dev.db "SELECT alarm_type, COUNT(*) FROM alarms WHERE alarm_desc LIKE '%测试数据%' GROUP BY alarm_type"
# 应看到 8 行，每行约 4-10 条（对应 8 类图片数量）
```

---

### 3.2 大模型复判子图（解决多模态能力）

**目标**：将 VLM 复判封装为可编排的 SUBGRAPH Skill。

**实现**：`skills/vlm_judge_subgraph.py`

**接口**：
```python
Skill(
    id="vlm_judge_alarm",
    name="大模型复判告警",
    description="读取告警图片，用多模态大模型判断告警是否真实",
    parameters={
        "alarm_uuid": {"type": "string", "description": "告警UUID"},
        # 或 "image_path": 直接传图片路径
    },
    skill_type=SkillType.SUBGRAPH
)
```

**内部流程**（LangGraph 子图）：
1. 如果传入 `alarm_uuid`：查 `alarms` 表拿 `snapshot_url`（本地路径）和 `alarm_type`
2. 查 `alarm_types` 表拿 `display_name`
3. 构造提示词：`"图中是否存在【{display_name}】行为？请给出判断（是/否/不确定）和理由。"`
4. 调用 `VLMClient.judge_image(image_path, prompt)`
5. 解析 VLM 输出：
   - verdict: 'confirmed' | 'rejected' | 'uncertain'
   - confidence: 0.0-1.0
   - reasoning: 文本
6. 返回结构化结果

**VLM 泛化改造**（`utils/vlm.py`）：
- 原本硬编码 4 类 → 改为接受任意 `alarm_type_display` 参数
- 提示词模板化：`"图中是否存在【{display}】？返回 JSON: {\"exists\": bool, \"confidence\": float, \"reasoning\": str}"`

---

### 3.3 告警统计与可视化 Skill（解决业务分析能力）

**目标**：提供聚合统计和图表生成能力。

**实现**：`skills/alarm_skills.py`（4个本地TOOL）

#### Skill 1: `aggregate_alarms`

```python
{
  "id": "aggregate_alarms",
  "description": "按时间/类型/摄像头聚合统计告警数",
  "parameters": {
    "date_start": "YYYY-MM-DD",
    "date_end": "YYYY-MM-DD",
    "group_by": "date | alarm_type | camera_id",
    "alarm_type": "可选，筛选特定类型"
  }
}
```

**实现逻辑**：
- 直接查 SQLite（非 MCP，因为是本地计算密集型）
- SQL: `SELECT {group_by}, COUNT(*) FROM alarms WHERE ts_event BETWEEN ... GROUP BY {group_by} ORDER BY COUNT(*) DESC`
- 返回：`[{"key": "no_helmet", "count": 23}, ...]`

#### Skill 2: `visualize_alarms`

```python
{
  "id": "visualize_alarms",
  "description": "生成告警统计图表（折线图/柱状图/饼图）",
  "parameters": {
    "data": "聚合数据（aggregate_alarms 的输出）",
    "chart_type": "line | bar | pie",
    "title": "图表标题"
  }
}
```

**实现逻辑**：
- 用 matplotlib 绘图
- 保存到内存（BytesIO）
- 返回 base64 编码的 PNG：`{"image_base64": "data:image/png;base64,...", "format": "png"}`

#### Skill 3: `fetch_alarm_context`

```python
{
  "id": "fetch_alarm_context",
  "description": "获取告警前后N秒的录像片段",
  "parameters": {
    "alarm_uuid": "告警UUID",
    "before_sec": "告警前多少秒（默认10）",
    "after_sec": "告警后多少秒（默认10）"
  }
}
```

**实现逻辑**：
- 查 `alarms` 表拿 `camera_id` 和 `ts_event`
- 调用已有 MCP 工具 `query_video(camera_id, start_time=ts_event-before_sec, end_time=ts_event+after_sec)`
- 返回录像列表

#### Skill 4: 已在 3.4 描述（`update_alarm_status`，MCP 工具）

---

### 3.4 只写 MCP Server（解决回写需求）

**目标**：提供受控的数据库写能力，只允许更新告警状态。

**实现**：`mcp_servers/ksipms_write_server.py` + `mcp_servers/config_write.yaml`

**暴露工具**：

```python
Tool(
    name="update_alarm_status",
    description="更新告警处理状态（仅限 closed/false_alarm）",
    inputSchema={
        "alarm_uuid": "告警UUID",
        "status": "closed | false_alarm",
        "note": "处理备注"
    }
)
```

**权限控制**：
- 只能 UPDATE `alarms` 表
- 只能修改字段：`status`, `processed_note`, `processed_at`, `processed_by`
- `status` 只能设为 `closed` 或 `false_alarm`
- `processed_by` 强制设为 `agent:vlm_judge`
- `processed_at` 自动设为当前时间

**审计**：
- 每次 UPDATE 前，INSERT `audit_log`：
  ```sql
  INSERT INTO audit_log(alarm_id, action, operator_id, payload, ts)
  VALUES (?, 'agent_update_status', 'agent:vlm_judge', ?, ?)
  ```

**配置**：`config_write.yaml`
```yaml
ksipms_write_server:
  db_path: "agent/data/ksipms_dev.db"
  read_only: false  # 允许写
  allowed_operations:
    - UPDATE
  allowed_tables:
    - alarms
  allowed_fields:
    alarms:
      - status
      - processed_note
      - processed_at
      - processed_by
  audit_required: true
```

---

### 3.5 Executor 步骤间传参（解决编排短板）

**目标**：让后续步骤能引用前序步骤的输出。

**改动点**：`graph/nodes.py` 的 `executor_node`

**方案**：简单模板替换（不引入复杂 DSL）

**State 扩展**：
```python
class AgentState(TypedDict):
    # ... 原有字段
    step_outputs: Dict[int, Any]  # {0: {...}, 1: {...}}，存每步的 result
```

**Executor 改造**：
```python
def executor_node(state: AgentState) -> Dict[str, Any]:
    idx = state["current_task_idx"]
    task = state["plan"][idx]
    
    # **新增：模板替换**
    args = task["args"].copy()
    step_outputs = state.get("step_outputs", {})
    for k, v in args.items():
        if isinstance(v, str) and v.startswith("{{step_"):
            # 解析 {{step_0.camera_id}}
            match = re.match(r'\{\{step_(\d+)\.(.+?)\}\}', v)
            if match:
                step_idx, field = int(match.group(1)), match.group(2)
                if step_idx in step_outputs:
                    args[k] = step_outputs[step_idx].get(field)
    
    # 执行任务
    result = registry.invoke(task["task"], args, context)
    
    # **新增：保存输出**
    step_outputs[idx] = result
    
    return {
        "step_outputs": step_outputs,
        "tool_results": [...],
        "current_task_idx": idx + 1
    }
```

**Planner 提示词补充**：
```
如果后续任务依赖前面任务的输出，使用模板语法：
{{"task": "query_video", "args": {{"camera_id": "{{step_0.camera_id}}"}}}}
```

---

### 3.6 主智能体升级（增强现有主图）

**目标**：用户在 Web Tab6 只看到一个"主智能体"选项，背后走增强后的 Plan-Execute 主图。

**实现方式**：直接增强现有 `/api/v1/chat` 端点和主图，**不**单独发布为 agent_registry 产物（保持架构简洁）。

**改动清单**：
1. `skills/init.py`：注册所有新 Skill（复判子图、统计、可视化、录像回溯、写回）
2. `graph/nodes.py`：Executor 补步骤间传参（如 3.5）
3. `mcp_servers/config.yaml`：保持只读 MCP 不变
4. 新增 `mcp_servers/config_write.yaml` 和 `ksipms_write_server.py`
5. `config.yaml`：MCP servers 列表加入 `ksipms_write`

**Web Tab6 适配**：
- `agent/web/agent_chat.py` 的 `published_agent_names()` 补一个硬编码项：`["_main_agent_", ...已发布的...]`
- `_main_agent_` 对应的 `load_agent_run` 返回一个包装函数：
  ```python
  def _main_agent_run(message, trace_id=""):
      # 调用主图 /api/v1/chat
      response = requests.post("http://localhost:8000/api/v1/chat", json={...})
      return {"response": response.json()["response"], "plan": [...], ...}
  ```

**用户体验**：
- Web Tab6 下拉框显示：`[主智能体(增强), alarm_query_agent_v2, ...]`
- 选"主智能体(增强)" → 走升级后的主图，支持复判、可视化、回写
- 选其他 → 走 Agent-of-Agent 生成的智能体

---

## 4. 实施计划（按优先级排序）

### P0: 核心能力（阻塞验证，必须完成）

| 任务 | 产出 | 工作量 | 验证标准 |
|------|------|--------|----------|
| **T1: 测试图片入库** | `agent/data/seed_test_alarms.py` | 1h | 40 条 alarms 记录，status=pending |
| **T2: Executor 传参** | `graph/nodes.py` 改造 | 1h | 单测：步骤2能拿到步骤1的 camera_id |
| **T3: 复判子图** | `skills/vlm_judge_subgraph.py` + `utils/vlm.py` 泛化 | 2h | 单测：8 类图片均能复判 |
| **T4: 只写 MCP** | `mcp_servers/ksipms_write_server.py` + config | 2h | 单测：update_alarm_status 成功写库 + audit_log |
| **T5: 统计/可视化/回溯** | `skills/alarm_skills.py` | 3h | 单测：生成折线图 base64，回溯录像 |
| **T6: 主图注册新 Skill** | `skills/init.py` 补注册 | 0.5h | `list_skills()` 包含全部新工具 |
| **T7: Web Tab6 适配** | `agent/web/agent_chat.py` 补主智能体 | 0.5h | 下拉框显示"主智能体(增强)" |

**小计**：10 小时（约 1.5 个工作日）

### P1: 测试与调优（确保 Demo 跑通）

| 任务 | 工作量 | 验收标准 |
|------|--------|----------|
| **T8: Demo 1 调试**（复判闭环） | 1h | 端到端跑通，audit_log 有记录 |
| **T9: Demo 2 调试**（可视化） | 1h | 生成折线图并返回 base64 |
| **T10: Demo 3 调试**（步骤传参） | 1h | 3 步 plan 正确执行 |
| **T11: 错误处理补强** | 1h | 工具失败时有友好提示 |

**小计**：4 小时

### P2: 文档与交付（可演示）

| 任务 | 产出 | 工作量 |
|------|------|--------|
| **T12: 更新 DEVELOPER_GUIDE** | 补充新 Skill 开发示例 | 1h |
| **T13: 录制演示视频** | 3 个 Demo 的屏幕录像 | 0.5h |
| **T14: 编写验收报告** | `plan/VALIDATION_REPORT.md` | 1h |

**小计**：2.5 小时

**总计**：16.5 小时（约 2 个工作日）

---

## 5. 风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| VLM 推理慢（>5s/张） | 中 | 高 | 复判并发化（批量），或限制 Demo 只复判 1-2 张 |
| 步骤间传参语法复杂 | 低 | 中 | 简化为固定模板 `{{step_N.field}}`，不支持嵌套 |
| 测试图片质量差，VLM 识别不准 | 中 | 低 | 调整 confidence 阈值，记录"不确定"案例 |
| 只写 MCP 引入安全隐患 | 低 | 高 | 强审计 + 字段白名单 + 只允许 2 种 status |
| matplotlib 中文字体缺失 | 高 | 低 | 内置 SimHei 或降级为英文标签 |

---

## 6. 知识库对接预留接口

虽然本轮**不实现** RAG 知识库，但预留清晰接口：

### 预留 Skill 定义

```python
Skill(
    id="query_safety_rules",
    name="查询安全规章制度",
    description="语义检索安全生产规章制度文档",
    parameters={
        "query": {"type": "string", "description": "查询关键词"},
        "top_k": {"type": "integer", "default": 3}
    },
    skill_type=SkillType.TOOL,  # 或 MCP_TOOL（取决于 RAG 服务形态）
    tags=["knowledge", "rag"]
)
```

### 集成方式

- 如果用自建轻量方案（Qdrant + Unstructured）：实现为本地 TOOL
- 如果用 RAGFlow/MaxKB：实现为 MCP_TOOL 或 HTTP Adapter

### 使用场景

```
用户：未戴安全帽违反哪些规定？
主智能体：
  步骤1: query_alarms(alarm_type=no_helmet, limit=1) → 获取一个案例
  步骤2: query_safety_rules(query="安全帽佩戴规定") → 返回条文
  步骤3: 汇总并引用条文回答
```

---

## 7. 后续路线（本规划完成后）

### 阶段 3.5：RAG 知识库集成（1-2 周）

- 按 `ROADMAP.md` 阶段3，选型 Qdrant 自建方案
- 实现 `query_safety_rules` Skill
- 补充"告警 + 规章联动"场景

### 阶段 4：Agent-of-Agent 深化（2 周）

- 元智能体生成的 Agent 默认使用 Skill Registry（更新 RULES.md）
- 主智能体能调度子 Agent（如"帮我生成一个专门查询 smoking 告警的智能体"）

### 阶段 5：生产优化（3 周）

- 重规划与错误恢复（Planner 迭代能力）
- 并发执行无依赖的任务（Executor 并行化）
- 缓存（Redis）、流式输出（WebSocket）
- 监控仪表板（Prometheus + Grafana）

---

## 8. 总结

### 本规划的价值

1. **验证架构真实能力**：不再是"能调一个工具"，而是"能在多步骤、跨模态、需回写的真实链路里端到端跑通"
2. **奠定业务基础**：复判、统计、可视化、回溯，全是监控告警平台的核心功能，后续直接复用
3. **为 RAG 铺路**：编排层、Skill Registry、主智能体都已验证，接入知识库时只需加一个 Skill
4. **可演示性强**：3 个 Demo 直观展示"AI 智能体如何处理真实业务问题"

### 与领导诉求的契合

| 领导关切 | 本规划的解决方案 |
|----------|------------------|
| "先测试编排能力，不急着做知识库" | ✅ RAG 暂缓，全力验证编排 |
| "用更多 skill/MCP 测试复杂任务" | ✅ 新增 7 个 Skill + 1 个只写 MCP |
| "要能演示给客户看" | ✅ 3 个端到端 Demo，覆盖查询、复判、可视化 |
| "知识库接口要预留" | ✅ Skill 定义已预留，后续无缝对接 |

### 里程碑交付物

- [ ] `plan/COMPLEXITY_VALIDATION_PLAN.md`（本文档）
- [ ] 测试图片入库脚本 + 40 条 alarms 记录
- [ ] 大模型复判子图（支持 8 类）
- [ ] 统计/可视化/回溯/回写 4 个 Skill
- [ ] 只写 MCP Server + 审计
- [ ] Executor 步骤间传参
- [ ] Web Tab6 主智能体入口
- [ ] 3 个 Demo 端到端跑通
- [ ] 验收报告 `plan/VALIDATION_REPORT.md`

**预计完成时间**：2 个工作日（16.5 小时）

---

**文档维护**：随实施进展持续更新  
**最后更新**：2026-06-05

