# Agent-of-Agent 可行性分析与实施方案

> **项目目标**：将 Claude Code 作为元智能体，自动生成、调试和优化 LangGraph 子智能体的提示词、工具配置和工作流程，实现 AI 开发 AI 的闭环。

---

## 一、核心思想对比

### 1.1 现有项目：ConvNeXt-V2 AI 自训练

**已验证可行**的模式：
```
人工定义 → 训练脚本模板（train.py 可编辑区）
         ↓
    Claude API ← AutoResearch 循环
         ↓
    修改超参 → 执行训练 → 采集指标
         ↓
    分析结果 → 决策下一轮参数
         ↓
    自动归档最佳权重 + 代码快照
```

**关键成功因素**：
1. **明确的成功指标**：Rank-1、mAP、TAR@FAR
2. **受限的搜索空间**：20+ 超参，取值范围明确
3. **快速反馈循环**：单轮训练 < 8 小时，crash 日志自动注入下轮
4. **可编辑区隔离**：AI 只改固定区域，不破坏基础架构

### 1.2 Agent-of-Agent 类比映射

| ConvNeXt-V2 自训练 | Agent-of-Agent 开发 |
|---|---|
| 基座模型（ConvNeXt-V2） | 基座框架（LangGraph + FastAPI） |
| 训练超参（LR、batch_size） | Agent 配置（system_prompt、tools、workflow） |
| 评估指标（Rank-1、mAP） | Agent 指标（任务成功率、工具调用准确率、响应质量） |
| 数据集（73 款工作服） | 测试用例（query-answer 对、工具调用场景） |
| train.py 可编辑区 | agent_template.py 可配置区 |
| autoresearch_loop.py | meta_agent_loop.py |

---

## 二、可行性评估

### 2.1 ✅ 技术可行性：高

**支持理由**：
1. **Claude Code 已具备所需能力**：
   - 能读写代码、执行 Python 脚本
   - 能解析错误日志、调试代码
   - 能遵循结构化提示词（如 Karpathy Guidelines）
   - 能使用 MCP / Skill 工具

2. **LangGraph Agent 配置是结构化的**：
   - System Prompt：纯文本，可模板化
   - 工具列表：JSON/YAML 配置
   - 工作流图：Python 代码，节点/边定义可参数化
   - 评估逻辑：单元测试 + 集成测试

3. **反馈循环可量化**：
   - 单元测试通过率
   - 工具调用准确率（期望工具 vs 实际调用）
   - 响应质量评分（LLM-as-judge）
   - 执行时间、错误率

### 2.2 ⚠️ 复杂度：中等偏高

**挑战点**：
1. **搜索空间更大**：
   - 超参：数值范围有限
   - Prompt：自然语言，组合空间巨大
   
2. **成功标准模糊**：
   - 训练：指标明确（Rank-1 > 0.95）
   - Agent：响应质量主观性强
   
3. **调试难度**：
   - 训练：crash 栈清晰（OOM、CUDA error）
   - Agent：可能是逻辑错误、工具参数错误、LLM 理解偏差

**缓解策略**：
- **分阶段迭代**：先优化单一节点（如 Planner），再优化整体流程
- **强化测试覆盖**：每个改动必须通过回归测试套件
- **限制搜索空间**：预定义 prompt 模板库，AI 只调整填充内容
- **人工验收卡点**：关键变更（如工作流图结构）需人工批准

### 2.3 ✅ 业务价值：极高

**收益**：
1. **开发提效**：新建 Agent 从 2-3 天 → 15 分钟（如文章所述）
2. **知识沉淀**：每次迭代的最佳 prompt/配置自动归档
3. **降低门槛**：非专家只需描述需求，无需理解 LangGraph 内部
4. **快速试错**：AI 可并行尝试多种 prompt 策略，人工只需验收

---

## 三、架构设计

### 3.1 整体架构

```
┌──────────────────────────────────────────────────────────┐
│                    Meta-Agent (Claude Code)              │
│  ┌────────────────────────────────────────────────────┐  │
│  │  元提示词生成器 (Meta Prompt Generator)              │  │
│  │  - 输入：任务描述 + 测试用例 + 已有 Agent 库        │  │
│  │  - 输出：System Prompt + Tools 配置 + Workflow      │  │
│  └────────────────┬───────────────────────────────────┘  │
│                   │                                       │
│  ┌────────────────v───────────────────────────────────┐  │
│  │  代码生成器 (Code Generator)                        │  │
│  │  - 基于模板生成 agent.py                            │  │
│  │  - 生成配套测试 test_agent.py                       │  │
│  └────────────────┬───────────────────────────────────┘  │
│                   │                                       │
│  ┌────────────────v───────────────────────────────────┐  │
│  │  执行与评估 (Executor & Evaluator)                  │  │
│  │  - 运行测试套件                                     │  │
│  │  - 采集指标：通过率、工具准确率、响应质量           │  │
│  └────────────────┬───────────────────────────────────┘  │
│                   │                                       │
│  ┌────────────────v───────────────────────────────────┐  │
│  │  反馈分析器 (Feedback Analyzer)                     │  │
│  │  - 解析失败原因（crash / logic / prompt mismatch） │  │
│  │  - 生成改进建议                                     │  │
│  └────────────────┬───────────────────────────────────┘  │
│                   │                                       │
│  ┌────────────────v───────────────────────────────────┐  │
│  │  迭代控制器 (Iteration Controller)                  │  │
│  │  - 决策：继续优化 / 满足要求 / 需人工介入           │  │
│  │  - 归档最佳版本                                     │  │
│  └────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────────┐
│              Sub-Agent Pool (生成的智能体库)              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐               │
│  │ Alarm    │  │ Video    │  │ Person   │  ...          │
│  │ Query    │  │ Search   │  │ Lookup   │               │
│  │ Agent    │  │ Agent    │  │ Agent    │               │
│  └──────────┘  └──────────┘  └──────────┘               │
└──────────────────────────────────────────────────────────┘
```

### 3.2 目录结构

```
/mnt/data3/clip/LangGraph/agent/agent/
├── feasibility_analysis.md          ← 本文档
├── architecture.md                  ← 详细架构设计
├── meta_agent/
│   ├── __init__.py
│   ├── prompt_generator.py          ← 元提示词生成器
│   ├── code_generator.py            ← 代码生成器（基于模板）
│   ├── executor.py                  ← 执行子 Agent 测试
│   ├── evaluator.py                 ← 评估指标计算
│   ├── feedback_analyzer.py         ← 失败原因分析
│   ├── iteration_controller.py      ← 迭代控制主循环
│   └── templates/
│       ├── agent_template.py        ← LangGraph Agent 模板
│       ├── test_template.py         ← 测试模板
│       └── prompt_library/          ← 预定义 prompt 片段库
│           ├── planner.txt
│           ├── executor.txt
│           └── formatter.txt
├── configs/
│   ├── meta_agent_config.yaml       ← 元智能体配置
│   └── evaluation_metrics.yaml      ← 评估指标定义
├── artifacts/
│   ├── generated_agents/            ← 生成的子 Agent 代码
│   ├── best_prompts/                ← 最佳 prompt 归档
│   ├── test_results/                ← 测试结果
│   └── iteration_logs/              ← 迭代日志
├── tests/
│   ├── test_meta_agent.py           ← 元智能体单元测试
│   └── fixtures/
│       └── sample_tasks.yaml        ← 示例任务定义
├── run_meta_agent.py                ← 启动脚本
└── README.md                        ← 使用说明
```

---

## 四、实施方案

### 4.1 阶段划分

#### **阶段 0：环境准备**（1 小时）
- [x] 创建 conda 环境 `agent`
- [x] 安装依赖：LangGraph + FastAPI + pytest
- [x] 验证 vLLM Qwen3-VL 服务可用

#### **阶段 1：最小可验证原型（MVP）**（2-3 天）
**目标**：证明元智能体能生成一个可运行的简单子 Agent

**交付物**：
1. ✅ `prompt_generator.py`：接收任务描述，生成 system prompt
2. ✅ `code_generator.py`：基于模板 + prompt 生成 `simple_agent.py`
3. ✅ `executor.py`：执行生成的 Agent，返回成功/失败
4. ✅ 一个示例任务：「查询今天的告警记录」
   - 输入：任务描述 + MCP 工具定义
   - 输出：能调用 `query_alarms` 工具的 LangGraph Agent
5. ✅ 手动验证流程能跑通

**验收标准**：
```python
# test_mvp.py
def test_meta_agent_generates_alarm_query_agent():
    task = {
        "name": "alarm_query_agent",
        "description": "查询安全生产平台的告警记录",
        "tools": ["query_alarms"],
        "test_cases": [
            {"input": "今天发生了几次告警？", "expected_tool": "query_alarms"}
        ]
    }
    
    # 1. 生成 prompt
    prompt = prompt_generator.generate(task)
    assert "查询告警" in prompt
    
    # 2. 生成代码
    code = code_generator.generate(prompt, task["tools"])
    assert "query_alarms" in code
    
    # 3. 执行测试
    result = executor.run_tests(code, task["test_cases"])
    assert result.success_rate == 1.0
```

#### **阶段 2：自动迭代优化**（1 周）
**目标**：实现类似 AutoResearch 的闭环优化

**新增功能**：
1. ✅ `evaluator.py`：
   - 工具调用准确率（期望工具 vs 实际调用）
   - 响应质量评分（LLM-as-judge）
   - 执行时间、错误率
   
2. ✅ `feedback_analyzer.py`：
   - 解析测试失败日志
   - 识别常见错误模式（工具参数错误、逻辑错误、prompt 理解偏差）
   - 生成具体改进建议

3. ✅ `iteration_controller.py`：
   - 主循环：生成 → 测试 → 评估 → 分析 → 优化 → 重复
   - 终止条件：达到目标指标 / 连续 N 轮无改进 / 超时
   - 归档最佳版本到 `artifacts/best_prompts/`

**验收标准**：
- 给定 5 个测试用例，元智能体能在 10 轮内生成通过率 > 80% 的 Agent
- 最佳 prompt 自动保存，可直接复制到 Claude Project 使用

#### **阶段 3：工具封装与 MCP/Skill 集成**（1 周）
**目标**：将 ksipms 平台功能封装为可复用工具

**任务**：
1. ✅ 设计工具描述格式（JSON Schema）：
   ```json
   {
     "name": "query_alarms",
     "description": "查询告警记录，支持按日期、类型筛选",
     "parameters": {
       "date": {"type": "string", "description": "日期 YYYY-MM-DD"},
       "alarm_type": {"type": "string", "enum": ["smoking", "no_helmet", "phone", "no_mask"]}
     },
     "returns": {"type": "array", "items": "AlarmRecord"}
   }
   ```

2. ✅ 实现 MCP 客户端封装：
   - `mcp/client.py`：统一调用接口
   - 自动重试、超时控制、错误处理

3. ✅ 构建工具库索引：
   - `tools_registry.yaml`：所有可用工具的元数据
   - 元智能体根据任务描述自动选择合适的工具子集

4. ✅ （可选）将元智能体本身封装为 Claude Code Skill：
   ```bash
   /meta-agent-create --task "查询告警" --tools query_alarms,query_video
   ```

#### **阶段 4：多 Agent 协同与知识沉淀**（1-2 周）
**目标**：管理多个子 Agent，支持组合调用

**功能**：
1. ✅ Agent 注册表：
   - 记录每个生成的 Agent 的能力、工具、适用场景
   - 支持版本管理（v1, v2, ...）

2. ✅ Agent 组合器：
   - 根据复杂任务自动选择多个子 Agent 串联/并联
   - 例如：「找出今天抽烟的人并调出录像」→ `alarm_query_agent` + `person_lookup_agent` + `video_search_agent`

3. ✅ 知识库集成：
   - 将成功的 prompt、失败案例存入向量数据库
   - 新任务来临时检索相似案例，加速生成

---

### 4.2 关键技术点

#### 4.2.1 Prompt 模板化

**目标**：缩小搜索空间，提高生成成功率

**设计**：
```python
# templates/prompt_library/planner.txt
PLANNER_TEMPLATE = """
你是一个任务规划专家。给定用户的自然语言请求，你需要将其拆解为可执行的步骤。

可用工具：
{tools_list}

用户请求：{user_message}

请生成 JSON 格式的执行计划：
[
  {{"task": "tool_name", "args": {{...}}, "reason": "为什么需要这一步"}},
  ...
]

规则：
- 优先使用提供的工具，避免臆测
- 参数必须从用户请求中提取或合理推断
- 步骤顺序符合逻辑依赖（例如：先查询再格式化）
"""

# 元智能体填充变量
prompt = PLANNER_TEMPLATE.format(
    tools_list="\n".join([f"- {t['name']}: {t['description']}" for t in tools]),
    user_message="今天发生了哪几种告警？"
)
```

#### 4.2.2 评估指标体系

| 指标 | 计算方法 | 权重 |
|---|---|---|
| **工具调用准确率** | 实际调用工具 == 测试用例期望工具 | 40% |
| **参数正确率** | 调用参数与期望参数的匹配度（JSON diff） | 30% |
| **响应质量** | LLM-as-judge（Qwen3-VL 评分 1-5） | 20% |
| **执行成功率** | 未抛出异常 | 10% |

**综合得分**：加权平均 > 0.8 视为通过

#### 4.2.3 失败模式分类

| 失败类型 | 识别特征 | 改进策略 |
|---|---|---|
| **工具选择错误** | 调用了不在工具列表的工具 | 强化 prompt 中工具列表的描述 |
| **参数错误** | KeyError / TypeError | 在 prompt 中添加参数示例 |
| **逻辑错误** | 执行顺序不合理 | 添加依赖关系说明 |
| **超时** | Timeout | 优化工作流，减少冗余步骤 |
| **Prompt 理解偏差** | LLM 输出格式不符合预期 | 添加输出格式约束（JSON Schema） |

---

## 五、与现有项目对比

| 维度 | ConvNeXt-V2 自训练 | Agent-of-Agent 开发 | 相似度 |
|---|---|---|---|
| **基础设施** | PyTorch 训练脚本 | LangGraph + FastAPI | ⭐⭐⭐⭐ |
| **优化目标** | 数值超参（LR, batch_size） | 文本 prompt + 工具配置 | ⭐⭐⭐ |
| **评估指标** | Rank-1, mAP（客观） | 工具准确率 + LLM 评分（半主观） | ⭐⭐⭐ |
| **搜索空间** | 受限（20+ 超参） | 更大（自然语言） | ⭐⭐ |
| **反馈延迟** | 长（单轮数小时） | 短（单轮数分钟） | ⭐⭐⭐⭐⭐ |
| **实现难度** | 已验证 | 需探索 | ⭐⭐⭐ |

**结论**：
- ✅ **核心机制高度相似**：都是「AI 生成 → 执行 → 评估 → 反馈 → 优化」的闭环
- ⚠️ **搜索空间更大**：需要更强的约束（模板化 prompt）
- ✅ **反馈更快**：测试运行时间短，迭代速度更快
- ⚠️ **评估更难**：响应质量主观性强，需要设计好测试用例

---

## 六、风险与缓解

| 风险 | 严重性 | 缓解措施 |
|---|---|---|
| **生成的 Agent 质量不稳定** | 高 | 1. 强化测试覆盖 2. 预定义 prompt 模板库 3. 人工验收卡点 |
| **评估指标不够客观** | 中 | 1. 多维度指标组合 2. 引入人工标注的 golden 测试集 |
| **搜索空间爆炸** | 中 | 1. 限制 prompt 可变部分 2. 分阶段迭代（先优化单节点） |
| **依赖 LLM 能力上限** | 低 | 1. 选择能力强的模型（Qwen3-VL） 2. 提供详细示例 |
| **过度自动化导致不可控** | 中 | 1. 关键变更需人工批准 2. 保留手动编辑接口 |

---

## 七、预期效果

### 7.1 开发效率

**当前流程**（手动开发 LangGraph Agent）：
1. 阅读 LangGraph 文档：1-2 小时
2. 编写 system prompt：2-4 小时
3. 配置工具、工作流：2-3 小时
4. 调试测试：4-8 小时
**总计**：1-2 天

**使用元智能体后**：
1. 描述任务需求：5 分钟
2. 提供测试用例：10 分钟
3. 元智能体自动生成 + 迭代优化：10-30 分钟
4. 人工验收：5-10 分钟
**总计**：30-60 分钟

**提效**：**20-40 倍**

### 7.2 质量提升

- ✅ **测试覆盖更全**：自动生成回归测试套件
- ✅ **最佳实践沉淀**：成功 prompt 自动归档，可复用
- ✅ **降低门槛**：非专家也能快速搭建 Agent

### 7.3 扩展性

- ✅ **快速适配新工具**：新增 MCP 工具后，元智能体自动学习如何使用
- ✅ **并行开发多 Agent**：元智能体可同时生成多个专用 Agent
- ✅ **知识库积累**：每次迭代的经验都沉淀下来，后续任务越来越快

---

## 八、结论与建议

### 8.1 可行性结论

✅ **强烈推荐实施**

**理由**：
1. ✅ 技术可行性高：Claude Code 已具备所需能力
2. ✅ 有成功先例：ConvNeXt-V2 自训练已验证类似机制
3. ✅ 业务价值极高：开发效率提升 20-40 倍
4. ✅ 风险可控：分阶段实施，关键卡点人工介入

### 8.2 实施建议

**优先级排序**：
1. **P0**：阶段 0（环境准备）+ 阶段 1（MVP）→ **立即执行**
   - 目标：2-3 天内验证核心可行性
   - 交付：能生成一个简单的告警查询 Agent

2. **P1**：阶段 2（自动迭代优化）→ **1 周内完成**
   - 目标：实现自动闭环，无需人工调 prompt
   - 交付：元智能体能独立优化 Agent 到目标指标

3. **P2**：阶段 3（工具封装）→ **2 周内完成**
   - 目标：接入真实 MCP 工具，支持生产场景
   - 交付：元智能体能调用 ksipms 平台接口

4. **P3**：阶段 4（多 Agent 协同）→ **1 个月内完成**
   - 目标：管理 Agent 库，支持复杂任务拆解
   - 交付：完整的元智能体平台

**资源需求**：
- 硬件：复用现有 GPU（5880 单卡已够用）
- 人力：1 名算法工程师（你）+ Claude Code
- 时间：MVP 3 天，完整方案 1 个月

### 8.3 下一步行动

**立即执行**（按优先级）：
1. ✅ 创建 conda 环境 `agent`
2. ✅ 安装依赖（LangGraph、FastAPI、pytest）
3. ✅ 编写 `prompt_generator.py`（MVP 版本）
4. ✅ 编写 `code_generator.py`（基于模板）
5. ✅ 手动运行一次完整流程，验证可行性
6. ✅ 如果 MVP 成功，立即启动阶段 2 开发

**我的建议**：
- ✅ **方案可行，立即开始**
- ✅ 先做 MVP，快速验证核心假设
- ✅ 成功后再投入更多资源完善
- ✅ 将整个开发过程记录下来，作为 Agent-of-Agent 的第一个成功案例

---

## 九、附录

### 9.1 参考资料

**Agent-of-Agent 相关**：
- 元提示词 Agent 概念：见 `/mnt/data3/clip/LangGraph/agent&agent.md`
- Coze、Claude Project、GPTs：支持自定义 Agent 的平台

**AutoResearch 借鉴**：
- ConvNeXt-V2 自训练：`/mnt/data3/clip/work-clothes/ConvNeXt-V2-wc/`
- `autoresearch_loop.py`：迭代控制逻辑
- `program.md`：可编辑区设计

**LangGraph 官方文档**：
- [Plan-and-Execute Agents](https://blog.langchain.com/planning-agents/)
- [LangGraph Workflows](https://docs.langchain.com/oss/python/langgraph/workflows-agents)

### 9.2 术语表

| 术语 | 定义 |
|---|---|
| **元智能体** | 用于生成、调试其他智能体的 Agent（Agent-of-Agent） |
| **子智能体** | 由元智能体生成的、执行具体任务的 Agent |
| **System Prompt** | LangGraph Agent 的核心指令，定义其角色、能力、行为 |
| **MCP** | Model Context Protocol，用于连接 LLM 与外部工具的协议 |
| **Plan-Execute** | LangGraph 设计模式：先规划任务，再逐步执行 |
| **LLM-as-judge** | 用 LLM 评估另一个 LLM 的输出质量 |

---

**文档版本**：v1.0  
**创建时间**：2026-06-02  
**作者**：Claude Opus 4.6 + 算法工程师  
**状态**：✅ 可行性分析完成，等待批准启动 MVP 开发
