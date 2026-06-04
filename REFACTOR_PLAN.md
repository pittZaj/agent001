# KSAgent 架构重构计划

## 问题诊断

### 当前实现的核心问题

1. **数据库访问硬编码**
   - `tool_impl.py` 直接写 SQL 查询
   - 数据库路径、表结构、字段暴露在代码中
   - 无法灵活控制访问权限（哪些表可查、哪些字段可见）

2. **MCP 层缺失**
   - `mcp/client.py` 只是占位符
   - 实际工具调用走 mock（`_execute_task_mock`）
   - 无法通过 MCP 协议实现细粒度的数据访问控制

3. **Skill Registry 不完整**
   - 工具注册是静态字典 `TOOL_REGISTRY`
   - 缺少元数据（描述、参数 schema、权限要求）
   - 无法动态注册/注销工具

4. **架构偏差**
   - 领导期望：**智能体 → MCP Server → 数据库**（数据库可控、隐私保护）
   - 当前实现：**智能体 → 直接 SQL**（数据库完全暴露、写死在代码中）

---

## 重构方案

### 架构目标

```
┌────────────────────────────────────────────────────────┐
│  LangGraph 编排层（Plan-Execute）                       │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐           │
│  │  Planner │ → │ Executor │ → │Formatter │           │
│  └──────────┘   └────┬─────┘   └──────────┘           │
└─────────────────────│──────────────────────────────────┘
                      │ 调用 Skill
┌─────────────────────▼──────────────────────────────────┐
│  Skill Registry（统一工具注册表）                       │
│  每个 Skill 包含：                                      │
│  - id: 工具唯一标识                                     │
│  - description: 功能描述                                │
│  - parameters: JSON Schema                             │
│  - implementation: callable                            │
│  - mcp_backed: bool（是否通过 MCP）                     │
└─────────────────────┬──────────────────────────────────┘
                      │
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
   ┌────────┐   ┌─────────┐   ┌──────────┐
   │ 本地工具│   │MCP Tools│   │VLM Skill │
   │(计算类)│   │(数据类) │   │(子图)    │
   └────────┘   └────┬────┘   └──────────┘
                     │
              ┌──────▼──────┐
              │  MCP Client │
              │  (Protocol) │
              └──────┬──────┘
                     │
              ┌──────▼──────┐
              │  MCP Server │
              │  告警 Server │
              │  人员 Server │
              │  视频 Server │
              └──────┬──────┘
                     │
              ┌──────▼──────┐
              │   SQLite    │
              │   (只读)    │
              └─────────────┘
```

### 核心原则

1. **数据访问必须经过 MCP**：所有数据库查询通过 MCP Server 暴露为工具
2. **MCP Server 控制权限**：在 MCP Server 配置哪些表、字段可访问
3. **Skill Registry 管理能力**：所有工具（包括 MCP 工具）注册到统一注册表
4. **智能体不知道数据库**：智能体只知道 Skill 名称和参数，不知道底层实现

---

## 实施步骤

### 阶段 1：实现 MCP Server（数据访问层）

**目标**：将数据库访问封装为 MCP Server，提供标准 MCP 协议接口

#### 1.1 创建 MCP Server 目录结构

```
agent/
├── mcp_servers/           # MCP Server 实现
│   ├── __init__.py
│   ├── ksipms_server.py   # 告警/人员/视频统一 Server
│   └── config.yaml        # Server 配置（权限、表白名单）
```

#### 1.2 实现 KSIPMS MCP Server

**特性**：
- 标准 MCP 协议（tools/call）
- 配置化权限控制（哪些表可查、字段白名单）
- 只读模式（防止误操作）
- 审计日志（best-effort）

**暴露的工具**：
- `query_alarms`: 查询告警记录
- `query_person`: 查询人员信息
- `query_video`: 查询录像片段
- `query_alarm_stats`: 统计分析（可选）

**配置示例**（`mcp_servers/config.yaml`）：

```yaml
ksipms_server:
  db_path: data/ksipms_dev.db
  read_only: true
  
  # 白名单模式：只暴露这些表
  allowed_tables:
    - alarms
    - persons
    - video_clips
    - audit_log  # 只允许写
  
  # 字段白名单（隐私保护）
  table_fields:
    alarms:
      - alarm_uuid
      - alarm_type
      - camera_id
      - area_id
      - severity
      - status
      - ts_event
      - alarm_desc
      # 不暴露: person_id（隐私）
    
    persons:
      - person_id
      - name
      - department
      # 不暴露: id_card, phone（隐私）
  
  # 工具定义
  tools:
    query_alarms:
      description: "查询告警记录"
      parameters:
        date: {type: string, format: date, optional: true}
        alarm_type: {type: string, optional: true}
        camera_id: {type: string, optional: true}
      max_results: 20
    
    query_person:
      description: "查询人员信息"
      parameters:
        person_id: {type: string}
      # 限制：不允许批量查询所有人员
```

#### 1.3 启动方式

```bash
# 方式 1：stdio（LangGraph 集成）
python -m mcp_servers.ksipms_server

# 方式 2：HTTP（调试/独立部署）
python -m mcp_servers.ksipms_server --http --port 3000
```

---

### 阶段 2：实现 MCP Client（协议客户端）

**目标**：LangGraph 通过 MCP Client 调用 MCP Server

#### 2.1 升级 `mcp/client.py`

```python
class MCPClient:
    """MCP 客户端（标准协议）"""
    
    async def list_tools(self, server_name: str) -> list[dict]:
        """列出 MCP Server 暴露的工具"""
    
    async def call_tool(self, server_name: str, tool_name: str, 
                       args: dict) -> dict:
        """调用 MCP 工具"""
```

#### 2.2 支持多 Server

```yaml
# config.yaml
mcp:
  servers:
    ksipms:
      command: python
      args: ["-m", "mcp_servers.ksipms_server"]
      env:
        DB_PATH: data/ksipms_dev.db
    
    # 未来扩展：其他 Server
    # knowledge_base:
    #   command: ...
```

---

### 阶段 3：实现 Skill Registry（能力注册表）

**目标**：统一管理所有工具（MCP + 本地），提供动态注册和发现

#### 3.1 创建 Skill 抽象

```python
# skills/base.py
from dataclasses import dataclass
from typing import Callable, Any

@dataclass
class Skill:
    """Skill 元数据"""
    id: str                      # 唯一标识（如 "query_alarms"）
    name: str                    # 显示名称
    description: str             # 功能描述（给 Planner 看）
    parameters: dict             # JSON Schema
    implementation: Callable     # 实现函数或子图
    skill_type: str              # "tool" | "subgraph" | "mcp_tool"
    mcp_server: str | None       # 如果是 mcp_tool，指定 server
    tags: list[str]              # ["data", "vision", "kb"]
```

#### 3.2 实现 Skill Registry

```python
# skills/registry.py
class SkillRegistry:
    def __init__(self):
        self._skills: dict[str, Skill] = {}
    
    def register(self, skill: Skill):
        """注册 Skill"""
        self._skills[skill.id] = skill
    
    def get(self, skill_id: str) -> Skill | None:
        """获取 Skill"""
        return self._skills.get(skill_id)
    
    def list_skills(self, tags: list[str] | None = None) -> list[Skill]:
        """列出可用 Skill（可按 tag 过滤）"""
        ...
    
    async def invoke(self, skill_id: str, args: dict, 
                    context: dict) -> dict:
        """调用 Skill（统一入口）"""
        skill = self.get(skill_id)
        if skill.skill_type == "mcp_tool":
            # 通过 MCP Client 调用
            return await mcp_client.call_tool(
                skill.mcp_server, skill_id, args
            )
        else:
            # 本地实现
            return skill.implementation(args, context)
```

#### 3.3 注册 MCP Tools 为 Skill

```python
# skills/mcp_skills.py
async def register_mcp_skills(registry: SkillRegistry, 
                             mcp_client: MCPClient):
    """动态注册 MCP Server 暴露的工具"""
    for server_name in ["ksipms"]:
        tools = await mcp_client.list_tools(server_name)
        for tool in tools:
            skill = Skill(
                id=tool["name"],
                name=tool["name"],
                description=tool["description"],
                parameters=tool["inputSchema"],
                implementation=None,  # 通过 MCP 调用
                skill_type="mcp_tool",
                mcp_server=server_name,
                tags=["data", "mcp"]
            )
            registry.register(skill)
```

---

### 阶段 4：升级 Planner 和 Executor

**目标**：Planner 只看到 Skill 元数据，Executor 通过 Registry 调用

#### 4.1 升级 Planner Prompt

```python
# graph/nodes.py
def planner_node(state: AgentState) -> dict:
    registry = get_skill_registry()
    
    # 构造工具列表（只包含元数据）
    available_skills = registry.list_skills()
    tools_desc = "\n".join([
        f"{s.id}: {s.description}\n  参数: {s.parameters}"
        for s in available_skills
    ])
    
    system_prompt = f"""你是任务规划助手。
    
可用工具：
{tools_desc}

请按以下格式返回计划：
[
  {{"task": "query_alarms", "args": {{"date": "2026-06-01"}}, "reason": "..."}}
]
"""
    # ... 调用 LLM
```

#### 4.2 升级 Executor

```python
async def executor_node(state: AgentState) -> dict:
    registry = get_skill_registry()
    task = state["plan"][state["current_task_idx"]]
    
    # 统一通过 Registry 调用
    result = await registry.invoke(
        skill_id=task["task"],
        args=task["args"],
        context={
            "session_id": state["session_id"],
            "trace_id": state.get("trace_id", ""),
        }
    )
    
    # ... 更新 state
```

---

### 阶段 5：重构 Agent-of-Agent

**目标**：生成的 Agent 通过 Skill Registry 调用工具，不再直接访问数据库

#### 5.1 修改生成的 Agent 代码模板

**之前**（直接导入 `tool_impl`）：
```python
from meta_agent.tool_impl import call_tool

result = call_tool("query_alarms", {"date": "2026-06-01"})
```

**之后**（通过 Registry）：
```python
from skills import get_skill_registry

registry = get_skill_registry()
result = await registry.invoke("query_alarms", {"date": "2026-06-01"})
```

#### 5.2 更新 `RULES.md`

添加规则：

```markdown
## 11. 数据访问规则（重要）

- **禁止**直接导入 `tool_impl`、**禁止**直接写 SQL
- **必须**通过 Skill Registry 调用工具：
  ```python
  registry = get_skill_registry()
  result = await registry.invoke("query_alarms", args)
  ```
- 所有数据访问工具已注册为 Skill，通过 MCP 协议调用
- MCP Server 控制权限，Agent 代码不应关心数据库实现
```

---

## 技术选型

### MCP Server 实现

**方案 1：使用 Anthropic 官方 MCP SDK（推荐）**

```python
# mcp_servers/ksipms_server.py
from mcp.server import Server
from mcp.types import Tool, TextContent

server = Server("ksipms")

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="query_alarms",
            description="查询告警记录",
            inputSchema={
                "type": "object",
                "properties": {
                    "date": {"type": "string"},
                    "alarm_type": {"type": "string"},
                },
            },
        ),
    ]

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "query_alarms":
        # 查询 SQLite，返回结果
        result = _query_alarms(**arguments)
        return [TextContent(type="text", text=json.dumps(result))]
```

**启动**：
```python
from mcp.server.stdio import stdio_server

if __name__ == "__main__":
    stdio_server(server)
```

### MCP Client 实现

```python
# mcp/client.py
from mcp.client.stdio import StdioServerParameters, stdio_client

class MCPClient:
    async def connect_server(self, server_name: str):
        params = StdioServerParameters(
            command="python",
            args=["-m", f"mcp_servers.{server_name}_server"],
        )
        async with stdio_client(params) as (read, write):
            self.clients[server_name] = (read, write)
    
    async def call_tool(self, server_name: str, tool_name: str, args: dict):
        read, write = self.clients[server_name]
        result = await read.call_tool(tool_name, args)
        return result
```

---

## 迁移计划

### P0：MCP 基础设施

- [ ] 实现 KSIPMS MCP Server（告警/人员/视频）
- [ ] 实现 MCP Client（stdio 协议）
- [ ] 配置化权限控制（`config.yaml`）

### P1：Skill Registry

- [ ] 实现 Skill 抽象和 Registry
- [ ] 动态注册 MCP Tools 为 Skill
- [ ] 升级 Planner/Executor 使用 Registry

### P2：重构现有代码

- [ ] 移除 `tool_impl.py` 中的直接 SQL 查询
- [ ] 更新 Agent-of-Agent 生成模板
- [ ] 更新 `RULES.md`

### P3：测试和文档

- [ ] 端到端测试（chat API → Skill Registry → MCP Server → SQLite）
- [ ] 性能测试（MCP 调用延迟）
- [ ] 更新 `ARCHITECTURE.md` 和 `README.md`

---

## 优势分析

### 相比当前实现

| 维度 | 当前实现 | 重构后 |
|------|---------|--------|
| **数据库暴露** | 完全暴露（表结构、字段、SQL） | MCP Server 控制，按需暴露 |
| **权限控制** | 无（代码写死） | MCP 配置化，细粒度控制 |
| **隐私保护** | 无（所有字段可见） | 字段白名单，敏感字段不暴露 |
| **灵活性** | 改工具需改代码 | MCP Server 配置即可 |
| **可测试性** | 需要真实数据库 | 可 mock MCP Server |
| **扩展性** | 添加数据源需改代码 | 新增 MCP Server 即可 |

### 符合架构文档

✅ **LangGraph + Skill Registry + Plan-Execute**  
✅ **MCP 协议控制数据访问**  
✅ **适配器模式（MCP Client）**  
✅ **配置化权限和白名单**  
✅ **智能体不关心数据库实现**  

---

## 风险和挑战

### 1. MCP 调用延迟

**风险**：相比直接 SQL，多一层 MCP 协议会增加延迟

**缓解**：
- MCP Server 使用 stdio（本地进程，延迟 < 10ms）
- 对于频繁调用，实现 Skill 层缓存

### 2. 调试复杂度

**风险**：多一层抽象，问题排查更复杂

**缓解**：
- MCP Client 记录所有调用日志（request/response）
- 提供调试模式：直接调用 MCP Server HTTP 接口

### 3. 向后兼容

**风险**：已生成的 Agent 使用旧 `tool_impl`

**缓解**：
- 分批迁移：先支持 Registry + MCP，保留 `tool_impl` 作为降级
- 元智能体生成新代码时使用 Registry

---

## 总结

### 核心变化

1. **数据访问路径**：`Agent → SQLite` 改为 `Agent → Skill Registry → MCP Client → MCP Server → SQLite`
2. **权限控制**：从"代码写死"改为"MCP 配置化"
3. **工具管理**：从"静态字典"改为"动态 Registry"

### 符合领导要求

✅ **数据库不完全暴露**：MCP Server 白名单控制  
✅ **灵活可控**：配置化权限，无需改代码  
✅ **隐私保护**：敏感字段可屏蔽  
✅ **便于扩展**：新增 MCP Server 即可支持新数据源  

### 下一步

建议先实现 P0（MCP 基础设施），验证方案可行性，再逐步迁移现有代码。
