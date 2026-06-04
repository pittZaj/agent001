# KSAgent 文档索引

> **更新日期**: 2026-06-04  
> **文档版本**: v2.0 (阶段2完成版)

---

## 📚 文档清单

本项目包含以下核心文档，涵盖架构设计、任务规划、开发指南等方面：

### 1. 架构设计文档

| 文档 | 说明 | 读者 |
|------|------|------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | 原始架构设计（领导编写） | 全员 |
| **[ARCHITECTURE_V2.md](ARCHITECTURE_V2.md)** | ⭐ **阶段2完成版架构说明** | 全员 |
| [REFACTOR_PLAN.md](REFACTOR_PLAN.md) | 阶段2重构计划（已完成） | 开发团队 |
| [STAGE2_SUMMARY.md](STAGE2_SUMMARY.md) | 阶段2完成总结 | 开发团队 |

### 2. 项目规划文档

| 文档 | 说明 | 读者 |
|------|------|------|
| [README.md](README.md) | 项目概述与快速开始 | 新人入职 |
| **[ROADMAP.md](ROADMAP.md)** | ⭐ **后期任务规划（阶段3-5）** | 项目管理 |

### 3. 开发者文档

| 文档 | 说明 | 读者 |
|------|------|------|
| **[DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md)** | ⭐ **完整开发操作手册** | 开发者 |
| [RULES.md](agent/meta_agent/RULES.md) | Agent-of-Agent 生成规则 | 元智能体开发者 |

---

## 🎯 快速导航

### 我想了解架构设计
→ 阅读 [ARCHITECTURE_V2.md](ARCHITECTURE_V2.md)

**重点内容**:
- 总体架构分层图
- MCP Server 权限控制机制
- Skill Registry 工作原理
- Plan-Execute 编排流程
- 数据流示例

### 我想了解后期规划
→ 阅读 [ROADMAP.md](ROADMAP.md)

**重点内容**:
- 阶段3: RAG 知识库集成（RAGFlow 选型、适配器设计）
- 阶段4: Agent-of-Agent 元智能体平台化
- 阶段5: 生产优化（性能、监控、安全）
- 时间表与成功标准

### 我想开发新功能
→ 阅读 [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md)

**重点内容**:
- Skill 开发指南（TOOL/MCP_TOOL/SUBGRAPH）
- MCP Server 开发指南
- 智能体开发流程（手动 vs Agent-of-Agent）
- 测试与调试技巧
- 发布与部署

---

## 📖 文档详解

### ARCHITECTURE_V2.md（架构设计说明）

**内容概要**:
1. **总体架构**: 分层图、设计原则
2. **核心组件详解**: MCP Server、Skill Registry、Plan-Execute、MCP Adapter
3. **数据流示例**: 文本查询流程、数据访问控制流程
4. **架构优势**: 与阶段1对比、满足 ARCHITECTURE.md 设计思路
5. **项目结构**: 目录说明
6. **配置文件**: config.yaml、mcp_servers/config.yaml
7. **部署方式**: 本地开发、Docker 部署
8. **技术决策**: ADR (为什么选 MCP、为什么 Skill Registry、临时方案)
9. **性能与监控**: 指标、日志、告警

**关键亮点**:
- ✅ 说明如何满足 ARCHITECTURE.md 的设计思路
- ✅ MCP 协议实现细粒度权限控制
- ✅ Skill Registry 统一工具管理
- ✅ 数据库可控、隐私保护

---

### ROADMAP.md（后期任务规划）

**内容概要**:
1. **阶段3: RAG 知识库集成**
   - 知识库选型对比（RAGFlow 首选）
   - 集成架构设计
   - 适配器接口设计
   - Skill 注册
   - 实施步骤（P0-P4）

2. **阶段4: Agent-of-Agent 元智能体**
   - 架构集成
   - 关键改进（生成的 Agent 使用 Registry）
   - 更新 RULES.md
   - Web 界面集成
   - 实施步骤（P0-P3）

3. **阶段5: 生产优化**
   - 性能优化
   - 监控与告警
   - 安全加固
   - 文档完善

4. **总体时间表**: 7周（约1.5个月）
5. **风险与挑战**
6. **成功标准**

**关键亮点**:
- ✅ RAGFlow 选型分析（PDF 解析能力强、Citation 支持）
- ✅ 适配器模式（易于切换知识库产品）
- ✅ Agent-of-Agent 集成规划（使用 Skill Registry）
- ✅ 明确时间表与验收标准

---

### DEVELOPER_GUIDE.md（开发操作手册）

**内容概要**:
1. **环境准备**: 系统要求、依赖安装、配置文件、初始化数据库
2. **Skill 开发指南**: 
   - TOOL 类型（同步/异步函数）
   - SUBGRAPH 类型（多步骤流程）
   - 注册到 Registry
3. **MCP Server 开发指南**:
   - 何时需要 MCP Server
   - 创建新 Server
   - 权限控制
   - 测试与注册
4. **智能体开发流程**:
   - 手动开发
   - Agent-of-Agent 生成
   - 调用已发布 Agent
5. **测试与调试**: 单元测试、集成测试、调试技巧
6. **发布与部署**: 发布 Agent、热重载、Docker 部署
7. **最佳实践**: Skill/MCP/Agent 开发规范
8. **常见问题**: FAQ

**关键亮点**:
- ✅ 完整的开发链路（从 Skill 开发到 Agent 发布）
- ✅ 详细的代码示例
- ✅ 最佳实践与避坑指南
- ✅ 测试与调试技巧

---

## 🔑 核心概念速查

### MCP (Model Context Protocol)
- **定义**: Anthropic 提出的标准协议，用于 AI 应用访问外部数据
- **本项目作用**: 封装数据库访问，实现细粒度权限控制
- **配置**: `mcp_servers/config.yaml`（表白名单、字段白名单）

### Skill Registry
- **定义**: 统一的工具注册表，管理所有可用能力
- **三类 Skill**: TOOL（本地函数）、MCP_TOOL（数据访问）、SUBGRAPH（子图）
- **核心接口**: `register()`, `get()`, `list_skills()`, `invoke()`

### Plan-Execute
- **定义**: LangGraph 编排模式，规划与执行分离
- **流程**: Router → Planner → Executor → Formatter
- **优势**: 可观测、可测试、步骤可审计

### Agent-of-Agent
- **定义**: 元智能体，根据需求自动生成专属智能体
- **流程**: 需求描述 → 生成代码 → 测试执行 → 验收评估 → 发布
- **当前状态**: 核心逻辑已完成，待集成 Skill Registry

---

## 📊 文档关系图

```
ARCHITECTURE.md (原始设计)
    │
    ├─→ REFACTOR_PLAN.md (重构计划)
    │       │
    │       └─→ STAGE2_SUMMARY.md (阶段2完成)
    │               │
    │               └─→ ARCHITECTURE_V2.md (最新架构) ⭐
    │
    └─→ README.md (项目概述)
            │
            ├─→ ROADMAP.md (后期规划) ⭐
            │
            └─→ DEVELOPER_GUIDE.md (开发手册) ⭐
```

---

## ✅ 文档完整性检查

### 架构说明 ✅
- [x] 总体架构图
- [x] 核心组件说明（MCP Server、Skill Registry、Plan-Execute）
- [x] 数据流示例
- [x] 配置文件说明
- [x] 技术决策（ADR）
- [x] 说明如何满足 ARCHITECTURE.md 设计思路

### 后期规划 ✅
- [x] 阶段3: RAG 知识库集成
- [x] 知识库选型（RAGFlow 推荐）
- [x] 适配器设计
- [x] 阶段4: Agent-of-Agent 集成
- [x] 阶段5: 生产优化
- [x] 时间表与验收标准

### 开发手册 ✅
- [x] 环境准备
- [x] Skill 开发（TOOL/MCP_TOOL/SUBGRAPH）
- [x] MCP Server 开发
- [x] 智能体开发（手动/Agent-of-Agent）
- [x] Skill 如何写
- [x] MCP 设计规范
- [x] 智能体如何发布
- [x] 测试与调试
- [x] 最佳实践

---

## 🚀 下一步行动

### 开发团队
1. 阅读 `ARCHITECTURE_V2.md` 了解最新架构
2. 根据 `ROADMAP.md` 规划工作
3. 参考 `DEVELOPER_GUIDE.md` 开始开发

### 新人入职
1. 阅读 `README.md` 快速了解项目
2. 阅读 `ARCHITECTURE_V2.md` 理解架构
3. 按照 `DEVELOPER_GUIDE.md` 搭建环境

### 项目管理
1. 根据 `ROADMAP.md` 分配任务
2. 跟踪阶段3-5进度
3. 评估风险与资源

---

## 📝 文档维护

**更新频率**:
- `ARCHITECTURE_V2.md`: 架构重大变更时更新
- `ROADMAP.md`: 每个阶段完成后更新
- `DEVELOPER_GUIDE.md`: 新增功能/最佳实践时更新

**维护责任人**: 技术负责人

**反馈渠道**: 
- 提交 Issue
- 联系开发团队
- 代码审查时更新

---

## 📦 附录

### 相关文件
- `config.yaml`: 主配置文件
- `mcp_servers/config.yaml`: MCP Server 配置
- `agent/registry/agent_registry.json`: Agent 注册表
- `agent/meta_agent/RULES.md`: Agent 生成规则

### 外部资源
- [MCP 协议官网](https://modelcontextprotocol.io/)
- [LangGraph 文档](https://langchain-ai.github.io/langgraph/)
- [RAGFlow GitHub](https://github.com/infiniflow/ragflow)
- [Qwen2-VL 模型](https://huggingface.co/Qwen/Qwen2-VL-7B-Instruct)

---

**文档索引版本**: v1.0  
**最后更新**: 2026-06-04  
**维护**: KSAgent 开发团队
