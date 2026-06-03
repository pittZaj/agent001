# Agent Spec: <agent_name>

> 这是 Agent-of-Agent 的**任务描述模板**。元智能体根据本文件生成 Agent 的 system prompt、
> 代码、并跑测试。所有字段会被 `meta_agent/spec_parser.py` 解析，**不要随意改章节标题**
> （`## N.` 必须保留）。
>
> Web 控制台 Tab 1 会自动展示当前可用工具/表/告警类型的真实清单（基于
> `web/spec_helper.py`），填写时直接照抄即可。

---

## 0. 填写规则速查（开发期一定要看这一节）

### 0.1 工具从哪来

工具实现在 `meta_agent/tool_impl.py::TOOL_REGISTRY`。**当前已实现** 3 个：

| name | 主要参数 | 用途 |
|---|---|---|
| `query_alarms` | `date`, `alarm_type`, `camera_id`（全部可选） | 查告警，返回 total/by_type/items |
| `query_video`  | `camera_id`, `start_time`, `end_time`（全部必填） | 查录像片段索引 |
| `query_person` | `person_id`（必填） | 查人员 + 最近告警次数 |

> ⚠️ 写入新工具的步骤：先在 `tool_impl.py` 加函数 + `TOOL_REGISTRY[name] = fn`，
> 然后才能在 spec 里引用。Spec 不允许引用未实现的工具——评估器会判失败。

### 0.2 数据库从哪来

底座是 `data/ksipms_dev.db`（SQLite，由 `data/seed.py` 生成）。共 9 张表，与公司平台
对接时换成 MySQL（用 `data/schema_mysql.sql`）。表清单：

```
areas, cameras, persons, alarm_types, operators,
alarms (核心), video_clips, audit_log, safety_rules
```

每张表的字段定义见 `data/schema.sql`（开发期）和 `data/CHECKLIST.md`（对接平台时的字段映射清单）。

### 0.3 枚举值从哪来（不要乱写！）

- `alarm_type`：必须是 `alarm_types.type_code` 之一。当前 8 个：
  `smoking, no_helmet, phone, no_mask, intrusion, fall_down, fire_smoke, ppe_other`
- `camera_id`：必须是 `cameras.camera_id` 之一，例如 `CAM-001`~`CAM-012`
- `person_id`：必须是 `persons.person_id` 之一，例如 `P001`~`P030`
- `date`：UTC `YYYY-MM-DD`，相对日期（今天/昨天）由 LLM 自己换算，**不能写中文**

### 0.4 `<TODAY>` 占位符

测试用例的 `expected_args_contains` 里写 `<TODAY>` 会被运行时（执行测试的当下）替换成
当天 UTC 日期。这样测试用例不会因为时间变化失效。

### 0.5 写不出来怎么办

**最简策略**：抄 `templates/AGENT_SPEC_EXAMPLE.md`（告警查询智能体已填好），改名/描述/测试用例即可。

---

## 1. 元数据 ✅

- name: <唯一蛇形小写, e.g., alarm_query_agent>
- version: 0.1.0
- owner: <负责人>
- created_at: <YYYY-MM-DD>

## 2. 业务目标 ✅

一句话说明这个 Agent 解决什么业务问题。**不要超过 50 字**——业务目标过宽会让 Claude
生成的 prompt 太散。

> 好例子：查询安全生产平台的告警记录，按日期/类型/摄像头筛选并给出聚合统计。
> 坏例子：做一个安全生产的全功能助手，支持各种查询和统计……

## 3. 用户场景 ✅（至少 3 条）

每条用"用户问什么 / Agent 应该做什么"的句式，覆盖**典型 + 边界 + 否定**三类。

> 好例子：
> - 用户问"今天的告警"，Agent 调用 query_alarms(date=今天)，返回按类型聚合
> - 用户提到具体日期+类型（"2026-06-01 的抽烟告警"），Agent 提取 date 与 alarm_type
> - 用户用别名（"抽烟"对应 smoking、"没戴安全帽"对应 no_helmet），Agent 完成中英映射
> - **(否定意图)** 用户问"明天会有哪些告警"，Agent 应说明无法预测，不调用工具

## 4. 可用工具 ✅

格式必须是 markdown 表格，**列名固定**为 `name | description | parameters | data_source`。
`parameters` 用 `key:type` 逗号分隔，类型注解里的 `|`（如 `str | None`）请改写为 `/`（如 `str/None`），
否则 markdown 解析会出错。

| name | description | parameters | data_source |
|---|---|---|---|
| query_alarms | 查询告警记录, 可按日期/类型/摄像头筛选 | date:str/None, alarm_type:str/None, camera_id:str/None | sqlite:data/ksipms_dev.db.alarms |

## 5. 数据访问 ✅

- 数据库: <sqlite | mysql | none>
- 路径: <data/ksipms_dev.db>
- 表: <逗号分隔, 如 alarms, video_clips>
- 只读: <true | false>

## 6. 知识库 ⚠️

当前阶段**未启用 RAG**。如需引用规章/文档，写 `暂未支持，后续 RAG 阶段填充。`，
后续阶段会从 `safety_rules` 表切片到向量库。

## 7. 测试用例 ✅（至少 3 条）

格式必须是 markdown 表格，**列名固定**为
`input | expected_tool | expected_args_contains | expected_output_contains`。

- `expected_tool`：必须出现在 §4 工具表里
- `expected_args_contains`：JSON 子集，会校验 plan 里对应 step 的 args
  - `<TODAY>` 占位：替换成运行时 UTC 日期
- `expected_output_contains`：子串匹配（中英不敏感），用来粗校验最终 response

| input | expected_tool | expected_args_contains | expected_output_contains |
|---|---|---|---|
| 今天的告警 | query_alarms | {"date":"<TODAY>"} | 告警 |
| 查询2026-06-01的抽烟告警 | query_alarms | {"alarm_type":"smoking"} | smoking |
| 最近未戴安全帽 | query_alarms | {"alarm_type":"no_helmet"} | helmet |

## 8. 验收指标 ✅

- tool_accuracy >= 0.8       # 期望工具确实出现在 plan 中的比例
- execution_success >= 0.9   # 没抛异常且 agent.error 为空的比例
- overall_score >= 0.7       # 加权综合：tool 0.3 + exec 0.2 + case_pass 0.5

## 9. Token 预算 ✅

- max_iterations: 1          # 失败后最多重试几轮（每轮都会再调 Claude）
- max_input_tokens: 50000    # Claude 累计输入超此抛 BudgetExceeded
- max_output_tokens: 20000   # 同上, 输出
