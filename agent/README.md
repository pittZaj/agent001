# Agent-of-Agent 元智能体框架

> 自动生成、调试和优化 LangGraph 子智能体的元智能体系统

## 快速开始

### 1. 激活环境

```bash
conda activate agent
cd /mnt/data3/clip/LangGraph/agent/agent
```

### 2. 运行示例

```bash
python run_meta_agent.py
```

## 功能特性

- ✅ **自动生成 System Prompt**：根据任务描述和工具定义自动生成
- ✅ **代码自动生成**：基于模板生成可运行的 LangGraph Agent
- ✅ **自动化测试**：执行测试用例，评估性能指标
- ✅ **反馈优化循环**：分析失败原因，自动优化 Prompt
- ✅ **版本管理**：保存最佳代码、Prompt 和评估指标

## 项目结构

```
agent/agent/
├── README.md                        # 本文档
├── feasibility_analysis.md          # 可行性分析与架构设计
├── run_meta_agent.py                # 主运行脚本
├── meta_agent/
│   ├── __init__.py
│   ├── prompt_generator.py          # 元提示词生成器
│   ├── code_generator.py            # 代码生成器
│   ├── executor.py                  # 执行器
│   ├── evaluator.py                 # 评估器
│   └── feedback_analyzer.py         # 反馈分析器
├── artifacts/
│   ├── generated_agents/            # 生成的子 Agent 代码
│   ├── best_prompts/                # 最佳 Prompt 归档
│   └── test_results/                # 测试结果
└── tests/
    └── fixtures/                    # 测试用例

```

## 使用示例

### 定义任务

```python
task = {
    "name": "alarm_query_agent",
    "description": "查询安全生产平台的告警记录",
    "tools": [
        {
            "name": "query_alarms",
            "description": "查询告警记录",
            "parameters": {
                "date": "日期 YYYY-MM-DD",
                "alarm_type": "告警类型：smoking/no_helmet/phone/no_mask"
            }
        }
    ],
    "test_cases": [
        {
            "input": "今天发生了哪几种告警？",
            "expected_tool": "query_alarms"
        }
    ]
}
```

### 运行元智能体

```python
from run_meta_agent import run_meta_agent

result = run_meta_agent(
    task=task,
    max_iterations=5,      # 最大迭代次数
    target_score=0.8,      # 目标得分
    save_artifacts=True    # 保存产物
)

print(f"成功: {result['success']}")
print(f"得分: {result['score']:.1%}")
print(f"迭代次数: {result['iterations']}")
```

### 查看生成的 Agent

```bash
# 生成的代码
cat artifacts/generated_agents/alarm_query_agent.py

# 最佳 Prompt
cat artifacts/best_prompts/alarm_query_agent_prompt.txt

# 评估指标
cat artifacts/test_results/alarm_query_agent_metrics.json
```

## 评估指标

| 指标 | 说明 | 权重 |
|---|---|---|
| **tool_accuracy** | 工具调用准确率（调用了正确的工具） | 60% |
| **execution_success** | 执行成功率（未抛出异常） | 40% |
| **overall_score** | 综合得分 | - |

**目标**：overall_score ≥ 0.8

## 工作流程

```
1. Prompt 生成
   ↓
2. 代码生成（基于模板）
   ↓
3. 执行测试用例
   ↓
4. 评估性能指标
   ↓
5. 分析失败原因
   ↓
6. 优化 Prompt
   ↓
7. 重复 2-6（直到达标或超过最大迭代次数）
```

## 下一步开发

### 阶段 2：自动迭代优化（当前 MVP 已实现基础版）
- [ ] 更智能的 Prompt 优化策略
- [ ] 支持多种失败模式识别
- [ ] 历史最佳 Prompt 复用

### 阶段 3：工具封装与 MCP/Skill 集成
- [ ] 真实 MCP 工具调用（替换 mock）
- [ ] 工具注册表
- [ ] 封装为 Claude Code Skill

### 阶段 4：多 Agent 协同
- [ ] Agent 注册表与版本管理
- [ ] 复杂任务自动拆解为多 Agent 协同
- [ ] 知识库集成（向量数据库存储成功案例）

## 对比：ConvNeXt-V2 自训练

| 维度 | ConvNeXt-V2 自训练 | Agent-of-Agent |
|---|---|---|
| 基础设施 | PyTorch 训练脚本 | LangGraph + FastAPI |
| 优化目标 | 数值超参 | 文本 Prompt + 工具配置 |
| 评估指标 | Rank-1, mAP | 工具准确率 + 执行成功率 |
| 反馈延迟 | 长（数小时） | 短（数分钟） |
| 实现机制 | ✅ 已验证 | ✅ MVP 已实现 |

## 常见问题

### Q: 为什么工具调用是 mock 的？
A: MVP 阶段先验证核心流程。阶段 3 会接入真实 MCP 工具。

### Q: 如何添加新工具？
A: 在任务定义的 `tools` 列表中添加工具描述即可。

### Q: 如何提高生成质量？
A: 提供更详细的工具描述、增加测试用例覆盖、提高 target_score。

## 参考资料

- 可行性分析：`feasibility_analysis.md`
- ConvNeXt-V2 自训练：`/mnt/data3/clip/work-clothes/ConvNeXt-V2-wc/`
- LangGraph 官方文档：https://docs.langchain.com/langgraph

## 许可

MIT License

---

**创建时间**：2026-06-02  
**作者**：Claude Opus 4.6 + 算法工程师  
**状态**：✅ MVP 已完成，可运行
