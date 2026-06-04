# KSAgent 智能体开发操作手册

> **版本**: v1.0 (2026-06-04)  
> **目标读者**: 智能体开发者、MCP Server 开发者  
> **前置知识**: Python、LangGraph、MCP 协议基础

---

## 目录

1. [环境准备](#1-环境准备)
2. [Skill 开发指南](#2-skill-开发指南)
3. [MCP Server 开发指南](#3-mcp-server-开发指南)
4. [智能体开发流程](#4-智能体开发流程)
5. [测试与调试](#5-测试与调试)
6. [发布与部署](#6-发布与部署)
7. [最佳实践](#7-最佳实践)

---

## 1. 环境准备

### 1.1 系统要求

- Python 3.11+
- Docker (可选，用于 RAGFlow 等服务)
- Git
- 8GB+ 内存
- GPU (可选，用于本地 VLM)

### 1.2 安装依赖

```bash
# 1. 克隆项目
git clone <repo_url>
cd LangGraph/agent

# 2. 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 安装 MCP SDK
pip install mcp

# 5. 验证安装
python -c "import langgraph; import mcp; print('OK')"
```

### 1.3 配置文件

**复制配置模板**:
```bash
cp config.yaml.example config.yaml
cp mcp_servers/config.yaml.example mcp_servers/config.yaml
```

**编辑 `config.yaml`**:
```yaml
llm:
  base_url: "http://127.0.0.1:8002/v1"  # 修改为你的 LLM 服务地址
  model: "Qwen3-VL-4B-Instruct"
  api_key: "EMPTY"

mcp:
  enabled: true

database:
  url: "sqlite:///data/ksipms_dev.db"
```

### 1.4 初始化数据库

```bash
# 生成测试数据
python -m agent.data.seed

# 验证
sqlite3 data/ksipms_dev.db "SELECT COUNT(*) FROM alarms;"
```

### 1.5 启动服务

```bash
# 启动 FastAPI
python main.py

# 访问 API 文档
# http://localhost:8000/docs
```

---

## 2. Skill 开发指南

### 2.1 Skill 类型选择

| 类型 | 适用场景 | 示例 |
|------|----------|------|
| **TOOL** | 简单计算、格式化、单步操作 | `format_text`, `calculate_stats` |
| **MCP_TOOL** | 数据访问（需权限控制） | `query_alarms`, `query_person` |
| **SUBGRAPH** | 多步骤流程、有内部状态 | `vlm_judge`, `rag_query` |

**决策树**:
```
需要访问数据库？
  ├─ 是 → MCP_TOOL (参考 §3 MCP Server 开发)
  └─ 否 → 有多步骤流程？
           ├─ 是 → SUBGRAPH
           └─ 否 → TOOL
```

### 2.2 开发 TOOL 类型 Skill

#### 2.2.1 创建 Skill 文件

```bash
# 创建新的 Skill 模块
touch skills/my_skill.py
```

#### 2.2.2 实现 Skill 函数

```python
# skills/my_skill.py
"""自定义 Skill 示例"""
import asyncio
from typing import Any

async def my_skill_impl(args: dict, context: dict) -> dict:
    """
    Skill 实现函数
    
    Args:
        args: Skill 参数（由 Planner 生成）
        context: 上下文（session_id, trace_id, etc.）
    
    Returns:
        结果字典，失败时包含 error 字段
    """
    try:
        # 1. 参数校验
        required_field = args.get("required_field")
        if not required_field:
            return {"error": "missing required_field"}
        
        # 2. 业务逻辑
        result = await some_async_operation(required_field)
        
        # 3. 返回结果
        return {
            "data": result,
            "status": "success"
        }
    
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)}"}


# 可选：同步函数也支持
def sync_skill_impl(args: dict, context: dict) -> dict:
    """同步 Skill（如果不需要异步）"""
    return {"result": "OK"}
```

#### 2.2.3 注册 Skill

```python
# skills/my_skill.py (续)
from skills.base import Skill, SkillType
from skills.registry import SkillRegistry

def register_my_skill(registry: SkillRegistry):
    """注册 Skill 到 Registry"""
    skill = Skill(
        id="my_skill",
        name="我的自定义 Skill",
        description="这是一个示例 Skill，用于演示如何开发",
        parameters={
            "type": "object",
            "properties": {
                "required_field": {
                    "type": "string",
                    "description": "必填字段"
                },
                "optional_field": {
                    "type": "integer",
                    "description": "可选字段",
                    "default": 10
                }
            },
            "required": ["required_field"]
        },
        implementation=my_skill_impl,  # 或 sync_skill_impl
        skill_type=SkillType.TOOL,
        tags=["custom", "example"]
    )
    registry.register(skill)
```

#### 2.2.4 集成到启动流程

```python
# skills/init.py
from skills.my_skill import register_my_skill

def init_skill_registry() -> SkillRegistry:
    """初始化 Skill Registry"""
    registry = get_skill_registry()
    
    # 注册 MCP Skills
    register_mcp_tools_as_local(registry)
    
    # 注册自定义 Skills
    register_my_skill(registry)  # 新增这行
    
    return registry
```

### 2.3 开发 SUBGRAPH 类型 Skill

#### 2.3.1 定义子图状态

```python
# skills/my_subgraph.py
from typing import TypedDict

class MySubgraphState(TypedDict):
    """子图状态"""
    input: str
    step1_result: str | None
    step2_result: str | None
    final_output: str | None
    error: str | None


#### 2.3.2 实现子图节点

```python
# skills/my_subgraph.py (续)
from langgraph.graph import StateGraph, END

def step1_node(state: MySubgraphState) -> dict:
    """第一步处理"""
    try:
        result = process_step1(state["input"])
        return {"step1_result": result}
    except Exception as e:
        return {"error": str(e)}


def step2_node(state: MySubgraphState) -> dict:
    """第二步处理（依赖第一步）"""
    if state.get("error"):
        return {}  # 跳过
    
    try:
        result = process_step2(state["step1_result"])
        return {"step2_result": result}
    except Exception as e:
        return {"error": str(e)}


def finalize_node(state: MySubgraphState) -> dict:
    """汇总结果"""
    if state.get("error"):
        return {"final_output": None}
    
    output = combine_results(state["step1_result"], state["step2_result"])
    return {"final_output": output}


def build_my_subgraph() -> StateGraph:
    """构建子图"""
    graph = StateGraph(MySubgraphState)
    
    graph.add_node("step1", step1_node)
    graph.add_node("step2", step2_node)
    graph.add_node("finalize", finalize_node)
    
    graph.set_entry_point("step1")
    graph.add_edge("step1", "step2")
    graph.add_edge("step2", "finalize")
    graph.add_edge("finalize", END)
    
    return graph.compile()
```

#### 2.3.3 注册 SUBGRAPH Skill

```python
# skills/my_subgraph.py (续)
def register_my_subgraph(registry: SkillRegistry):
    compiled_graph = build_my_subgraph()
    
    skill = Skill(
        id="my_subgraph",
        name="复杂流程子图",
        description="多步骤处理流程示例",
        parameters={
            "type": "object",
            "properties": {
                "input": {"type": "string", "description": "输入数据"}
            },
            "required": ["input"]
        },
        implementation=compiled_graph,  # 传入编译后的图
        skill_type=SkillType.SUBGRAPH,
        tags=["subgraph", "complex"]
    )
    registry.register(skill)
```

---

## 3. MCP Server 开发指南

### 3.1 何时需要 MCP Server

**场景**:
- 需要访问数据库
- 需要细粒度权限控制
- 需要隐藏敏感字段
- 需要审计所有数据访问

**不需要 MCP Server**:
- 纯计算逻辑
- 无状态处理
- 不涉及数据存储

### 3.2 创建新的 MCP Server

#### 3.2.1 目录结构

```bash
mcp_servers/
├── __init__.py
├── ksipms_server.py      # 已有：告警/人员/录像
├── new_server.py         # 新增：你的 Server
└── config.yaml           # 配置文件
```

#### 3.2.2 实现 MCP Server

```python
# mcp_servers/new_server.py
"""新的 MCP Server 示例"""
import json
from pathlib import Path
from mcp.server import Server
from mcp.types import Tool, TextContent

# 1. 创建 Server 实例
server = Server("new_server")


# 2. 定义工具列表
@server.list_tools()
async def list_tools() -> list[Tool]:
    """列出所有可用工具"""
    return [
        Tool(
            name="my_tool",
            description="我的自定义工具",
            inputSchema={
                "type": "object",
                "properties": {
                    "param1": {
                        "type": "string",
                        "description": "参数1"
                    },
                    "param2": {
                        "type": "integer",
                        "description": "参数2",
                        "default": 10
                    }
                },
                "required": ["param1"]
            }
        )
    ]


# 3. 实现工具调用
@server.call_tool()
async def call_tool(name: str, arguments: dict):
    """调用工具"""
    if name == "my_tool":
        result = await my_tool_impl(
            param1=arguments["param1"],
            param2=arguments.get("param2", 10)
        )
        return [TextContent(type="text", text=json.dumps(result))]
    else:
        raise ValueError(f"Unknown tool: {name}")


async def my_tool_impl(param1: str, param2: int) -> dict:
    """工具实现逻辑"""
    # 这里可以访问数据库、调用 API 等
    return {
        "result": f"Processed {param1} with {param2}",
        "status": "success"
    }


# 4. 启动 Server
if __name__ == "__main__":
    from mcp.server.stdio import stdio_server
    import asyncio
    
    asyncio.run(stdio_server(server))
```

#### 3.2.3 添加权限控制

```python
# mcp_servers/new_server.py (续)
import yaml

def load_config() -> dict:
    """加载配置"""
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)["new_server"]

CONFIG = load_config()


def check_permission(table: str, operation: str) -> bool:
    """权限检查"""
    allowed_tables = CONFIG.get("allowed_tables", [])
    if table not in allowed_tables:
        raise PermissionError(f"Table {table} not allowed")
    
    if operation == "write" and CONFIG.get("read_only", True):
        raise PermissionError("Server is read-only")
    
    return True


def filter_fields(table: str, data: dict) -> dict:
    """字段白名单过滤"""
    allowed_fields = CONFIG.get("table_fields", {}).get(table, [])
    if not allowed_fields:
        return data
    
    return {k: v for k, v in data.items() if k in allowed_fields}
```

#### 3.2.4 配置文件

```yaml
# mcp_servers/config.yaml
new_server:
  db_path: data/new_db.sqlite
  read_only: true
  
  allowed_tables:
    - table1
    - table2
  
  table_fields:
    table1:
      - id
      - name
      - status
      # 不暴露: secret_field
    
    table2:
      - id
      - description
```

### 3.3 测试 MCP Server

```bash
# 1. 直接运行（stdio 模式）
python -m mcp_servers.new_server

# 2. 测试工具列表
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python -m mcp_servers.new_server

# 3. 测试工具调用
echo '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"my_tool","arguments":{"param1":"test"}}}' | python -m mcp_servers.new_server
```

### 3.4 注册到 Skill Registry

```python
# skills/init.py
from mcp_adapter import MCPClient

async def register_new_server_skills(registry: SkillRegistry):
    """注册新 Server 的工具"""
    # 临时方案：直接调用实现
    from mcp_servers.new_server import my_tool_impl
    
    async def wrapper(args: dict, context: dict) -> dict:
        return await my_tool_impl(
            param1=args["param1"],
            param2=args.get("param2", 10)
        )
    
    skill = Skill(
        id="my_tool",
        name="我的工具",
        description="我的自定义工具",
        parameters={
            "type": "object",
            "properties": {
                "param1": {"type": "string"},
                "param2": {"type": "integer", "default": 10}
            },
            "required": ["param1"]
        },
        implementation=wrapper,
        skill_type=SkillType.TOOL,  # 临时使用 TOOL
        tags=["mcp", "custom"]
    )
    registry.register(skill)
```

---

## 4. 智能体开发流程

### 4.1 开发方式选择

| 方式 | 适用场景 | 优势 | 劣势 |
|------|----------|------|------|
| **手动开发** | 复杂逻辑、需精细控制 | 灵活、可调试 | 开发慢 |
| **Agent-of-Agent** | 标准CRUD、数据查询 | 快速生成 | 代码质量依赖 LLM |

### 4.2 手动开发智能体

#### 4.2.1 创建 Agent 文件

```python
# agent/artifacts/published/my_agent_v1_0_0.py
"""
My Agent - 自定义智能体

功能：根据用户输入执行特定任务
"""
from skills import get_skill_registry

async def run(user_input: str) -> str:
    """
    Agent 入口函数
    
    Args:
        user_input: 用户输入
    
    Returns:
        响应文本
    """
    registry = get_skill_registry()
    
    # 示例：调用 Skill
    if "告警" in user_input:
        result = await registry.invoke(
            skill_id="query_alarms",
            args={"date": "2026-06-04"},
            context={"agent": "my_agent"}
        )
        
        if result.get("error"):
            return f"查询失败: {result['error']}"
        
        alarms = result.get("alarms", [])
        return f"查询到 {len(alarms)} 条告警"
    
    return "我不理解您的请求"
```

#### 4.2.2 注册 Agent

```python
# agent/registry.py
from agent.registry import publish

# 手动注册
registry = {
    "my_agent": {
        "version": "1.0.0",
        "published_path": "agent/artifacts/published/my_agent_v1_0_0.py",
        "module_name": "my_agent_v1_0_0",
        "route": "/agents/my_agent/chat",
        "registered_at": "2026-06-04T10:00:00Z"
    }
}
```

### 4.3 使用 Agent-of-Agent 生成

#### 4.3.1 编写需求描述

```python
# agent/run_meta_agent.py
from agent.meta_agent.generator import generate_agent

# 需求描述
requirement = """
Agent Name: weekly_alarm_stats
Description: 统计本周告警情况

功能需求：
1. 查询本周（周一到周日）的所有告警
2. 按告警类型分组统计数量
3. 返回格式化的统计结果

示例输入：
- "本周告警统计"
- "这周发生了哪些告警"

示例输出：
本周告警统计（2026-06-02 至 2026-06-08）：
- 未戴安全帽: 15次
- 抽烟: 3次
- 接打电话: 7次
总计: 25次
"""

# 生成 Agent
job_id = await generate_agent(requirement)
print(f"Job ID: {job_id}")
```

#### 4.3.2 运行元智能体

```bash
# 启动元智能体
python -m agent.run_meta_agent

# 输入需求描述
# > Agent Name: weekly_alarm_stats
# > Description: ...

# 等待生成、测试、验收
# ✅ Code generated
# ✅ Tests passed (3/3)
# ✅ Acceptance score: 85/100
```

#### 4.3.3 查看生成结果

```bash
# 生成的代码在
cat agent/artifacts/<job_id>/agent_code.py

# 验收报告
cat agent/artifacts/<job_id>/REGISTER.json
```

#### 4.3.4 发布 Agent

```bash
# 通过验收后发布
python -m agent.publish <job_id>

# 查看注册表
cat agent/registry/agent_registry.json
```

### 4.4 调用已发布 Agent

```bash
# API 调用
curl -X POST http://localhost:8000/agents/weekly_alarm_stats/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "本周告警统计"}'
```

---

## 5. 测试与调试

### 5.1 单元测试

#### 5.1.1 测试 Skill

```python
# tests/test_my_skill.py
import pytest
from skills.my_skill import my_skill_impl

@pytest.mark.asyncio
async def test_my_skill_success():
    """测试 Skill 正常执行"""
    args = {"required_field": "test"}
    context = {"session_id": "test123"}
    
    result = await my_skill_impl(args, context)
    
    assert "error" not in result
    assert result["status"] == "success"


@pytest.mark.asyncio
async def test_my_skill_missing_param():
    """测试缺少参数"""
    args = {}
    context = {}
    
    result = await my_skill_impl(args, context)
    
    assert "error" in result
    assert "required_field" in result["error"]
```

#### 5.1.2 测试 MCP Server

```python
# tests/test_mcp_new_server.py
import pytest
from mcp_servers.new_server import my_tool_impl

@pytest.mark.asyncio
async def test_my_tool():
    """测试 MCP 工具"""
    result = await my_tool_impl(param1="test", param2=10)
    
    assert result["status"] == "success"
    assert "result" in result
```

### 5.2 集成测试

```python
# tests/test_e2e_my_agent.py
import pytest
from main import app
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_my_agent_e2e():
    """端到端测试"""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post(
            "/agents/my_agent/chat",
            json={"message": "查询告警"}
        )
    
    assert response.status_code == 200
    data = response.json()
    assert "查询到" in data["response"]
```

### 5.3 调试技巧

#### 5.3.1 启用详细日志

```python
# main.py
from loguru import logger

logger.add("logs/debug.log", level="DEBUG", rotation="100 MB")
```

#### 5.3.2 查看 Skill Registry

```python
# 临时调试脚本
from skills import get_skill_registry

registry = get_skill_registry()
skills = registry.list_skills()

for skill in skills:
    print(f"{skill.id}: {skill.description}")
    print(f"  Type: {skill.skill_type}")
    print(f"  Params: {skill.parameters}")
```

#### 5.3.3 测试 Planner

```python
# 直接测试 Planner 节点
from graph.nodes import planner_node
from graph.state import AgentState

state = AgentState(
    session_id="test",
    user_message="今天有哪些告警？",
    messages=[],
    plan=[],
    current_task_idx=0,
    tool_results=[],
    final_response=None,
    error=None
)

result = planner_node(state)
print(f"Plan: {result['plan']}")
```

---

## 6. 发布与部署

### 6.1 发布 Agent

```bash
# 1. 确认 Agent 通过验收
cat agent/artifacts/<job_id>/REGISTER.json | grep passed_acceptance

# 2. 发布
python -m agent.publish <job_id>

# 3. 验证注册表
cat agent/registry/agent_registry.json
```

### 6.2 热重载 Agent

```python
# main.py
from fastapi import FastAPI
from agent.registry import list_agents, load_agent_run

app = FastAPI()

@app.post("/agents/{agent_name}/chat")
async def agent_chat(agent_name: str, request: ChatRequest):
    """动态路由到已发布 Agent"""
    try:
        run_func = load_agent_run(agent_name)
        response = await run_func(request.message)
        return {"response": response}
    except KeyError:
        raise HTTPException(404, f"Agent {agent_name} not found")


@app.get("/agents")
async def list_all_agents():
    """列出所有已发布 Agent"""
    return list_agents()
```

### 6.3 Docker 部署

```dockerfile
# Dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["python", "main.py"]
```

```yaml
# docker-compose.yml
version: '3.8'

services:
  ksagent:
    build: .
    ports:
      - "8000:8000"
    volumes:
      - ./data:/app/data
      - ./config.yaml:/app/config.yaml
      - ./agent/registry:/app/agent/registry
    environment:
      - LLM_BASE_URL=http://vllm:8002/v1
    depends_on:
      - vllm
  
  vllm:
    image: vllm/vllm-openai:latest
    command: >
      --model Qwen/Qwen2-VL-7B-Instruct
      --port 8000
    ports:
      - "8002:8000"
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
```

### 6.4 监控部署

```bash
# 启动服务
docker-compose up -d

# 查看日志
docker-compose logs -f ksagent

# 健康检查
curl http://localhost:8000/health

# 查看已发布 Agent
curl http://localhost:8000/agents
```

---

## 7. 最佳实践

### 7.1 Skill 开发

✅ **推荐**:
- 保持 Skill 功能单一（Single Responsibility）
- 参数使用 JSON Schema 严格校验
- 错误返回 `{"error": "..."}` 格式
- 异步函数优先（性能更好）
- 添加详细的 docstring

❌ **避免**:
- Skill 内部直接访问数据库（使用 MCP）
- 硬编码配置（使用 config.yaml）
- 捕获异常后不返回错误信息
- 循环依赖其他 Skill

### 7.2 MCP Server 开发

✅ **推荐**:
- 只读模式（read_only: true）
- 字段白名单（隐藏敏感信息）
- 审计日志（记录所有调用）
- 参数校验（防止 SQL 注入）

❌ **避免**:
- 暴露所有表和字段
- 允许写操作（除非必要）
- 跳过审计日志
- 返回原始错误信息（可能泄露内部结构）

### 7.3 Agent 开发

✅ **推荐**:
- 通过 Skill Registry 调用工具
- 错误处理友好（返回用户可理解的信息）
- 添加示例输入输出
- 版本号语义化（v1.0.0）

❌ **避免**:
- 直接导入 `tool_impl`
- 硬编码 SQL 查询
- 忽略错误（无错误处理）
- 返回技术错误信息给用户

### 7.4 测试

✅ **推荐**:
- 每个 Skill 至少 3 个测试（正常/异常/边界）
- 端到端测试覆盖核心流程
- Mock 外部依赖（数据库、API）
- 使用 pytest fixtures

❌ **避免**:
- 跳过测试直接发布
- 测试依赖真实数据库（应 mock）
- 没有边界条件测试
- 测试间有依赖关系

---

## 8. 常见问题

### Q1: 如何查看可用的 Skill？

```python
from skills import get_skill_registry

registry = get_skill_registry()
skills = registry.list_skills()

for skill in skills:
    print(f"{skill.id}: {skill.description}")
```

### Q2: MCP Server 调用失败怎么办？

1. 检查配置文件 `mcp_servers/config.yaml`
2. 测试 MCP Server 单独运行
3. 查看审计日志 `audit_log` 表
4. 启用详细日志 `logger.level = DEBUG`

### Q3: Agent 生成质量不高？

1. 优化需求描述（更详细、更明确）
2. 提供示例输入输出
3. 更新 `RULES.md`（添加约束）
4. 人工审核生成代码后再发布

### Q4: 如何回滚 Agent？

```bash
# 1. 从注册表删除
python -c "from agent.registry import unpublish; unpublish('agent_name')"

# 2. 重新发布旧版本
python -m agent.publish <old_job_id>
```

---

## 9. 参考资源

- **MCP 协议**: https://modelcontextprotocol.io/
- **LangGraph 文档**: https://langchain-ai.github.io/langgraph/
- **项目架构**: `ARCHITECTURE_V2.md`
- **后期规划**: `ROADMAP.md`

---

**文档维护**: 随项目演进持续更新  
**问题反馈**: 提交 Issue 或联系开发团队  
**最后更新**: 2026-06-04

```
