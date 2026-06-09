# MCP Server 开发规范与对接指南

**文档版本**: V1.0  
**编写日期**: 2026-06-09  
**适用场景**: 阶段 2.5 真实平台对接，指导同事快速实现新平台的 MCP Server  
**目标读者**: 后端开发工程师、平台对接工程师

---

## 📋 目录

1. [概述](#1-概述)
2. [架构设计原则](#2-架构设计原则)
3. [MCP Server 核心规范](#3-mcp-server-核心规范)
4. [完整示例：KSIPMS 只读 Server](#4-完整示例ksipms-只读-server)
5. [完整示例：KSIPMS 只写 Server](#5-完整示例ksipms-只写-server)
6. [配置文件规范](#6-配置文件规范)
7. [数据库对接最佳实践](#7-数据库对接最佳实践)
8. [多 Server 策略](#8-多-server-策略)
9. [测试与验证](#9-测试与验证)
10. [常见问题与解决方案](#10-常见问题与解决方案)

---

## 1. 概述

### 1.1 为什么需要 MCP Server？

在智能体平台架构中，**MCP Server 是唯一与外部数据源交互的层**。当对接真实平台时：

- ✅ **核心架构无需改动**：Planner、Executor、Formatter 保持不变
- ✅ **Skill 定义基本不变**：只需微调参数字段名（如果平台 Schema 不同）
- ✅ **变化集中在 MCP Server**：这正是 MCP 协议的设计目的

### 1.2 本文档的作用

本文档提供：
1. **标准化开发模板**：参考现有 KSIPMS Server 实现，快速编写新平台的 MCP Server
2. **配置规范**：统一的配置文件结构，便于维护
3. **多 Server 策略指南**：何时拆分、如何拆分
4. **端到端示例**：从数据库 Schema → MCP Server → Skill 注册的完整链路

---

## 2. 架构设计原则

### 2.1 单一职责原则

**MCP Server 只做一件事**：将平台数据源（数据库/API）暴露为标准化的 MCP 工具。

```
┌─────────────────┐
│  LangGraph 主图  │
│ (Plan-Execute)  │
└────────┬────────┘
         │ 调用 Skill
         ↓
┌─────────────────┐
│ Skill Registry  │  ← 自动注册 MCP 工具为 Skill
└────────┬────────┘
         │ MCP 协议（stdio）
         ↓
┌─────────────────┐
│   MCP Server    │  ← 你需要编写的部分
└────────┬────────┘
         │ SQL/HTTP
         ↓
┌─────────────────┐
│  真实平台数据源  │
│ (DB / REST API) │
└─────────────────┘
```

**关键点**：
- MCP Server **不包含业务逻辑**（如复判、聚合统计）
- 业务逻辑在 **Skill** 或 **LangGraph 主图** 中实现
- MCP Server 只负责"读"或"写"数据

### 2.2 读写分离原则

**强烈建议**将读操作和写操作拆分为 **2 个独立的 MCP Server**：

| Server 类型 | 职责 | 权限 | 示例 |
|-------------|------|------|------|
| **只读 Server** | 查询数据 | 数据库 `mode=ro`，只暴露 SELECT | `ksipms_server.py` |
| **只写 Server** | 修改数据 | 受控写入（白名单字段 + 审计） | `ksipms_write_server.py` |

**为什么分离？**
1. **安全性**：只读 Server 即使被滥用，也无法破坏数据
2. **审计清晰**：写操作强制记录 audit_log，便于追溯
3. **权限最小化**：大部分 Skill 只需只读权限

---

## 3. MCP Server 核心规范

### 3.1 文件结构

```
agent/
├── mcp_servers/
│   ├── __init__.py
│   ├── your_platform_server.py        # 只读 Server
│   ├── your_platform_write_server.py  # 只写 Server
│   ├── config_your_platform.yaml      # 只读配置
│   └── config_your_platform_write.yaml # 只写配置
```

### 3.2 必需的依赖

```python
# MCP 协议核心库
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# 配置管理
import yaml
from pathlib import Path

# 数据库（如适用）
import sqlite3  # 或 pymysql, psycopg2 等
```

### 3.3 Server 实现的标准结构

每个 MCP Server 必须包含以下部分：

```python
# 1. 配置加载
def load_config() -> dict:
    config_path = Path(__file__).parent / "config_xxx.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)["your_server_name"]

CONFIG = load_config()

# 2. 工具实现函数
def tool_impl(param1: str, param2: int = 0) -> dict:
    """工具的实际逻辑（查询数据库、调用 API 等）"""
    # ... 实现细节
    return {"success": True, "data": [...]}

# 3. MCP Server 实例
server = Server("your_server_name")

# 4. 注册工具列表
@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="tool_name",
            description="工具描述（会被 LLM 看到）",
            inputSchema={...},  # JSON Schema
        ),
    ]

# 5. 工具调用分发
@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "tool_name":
        result = tool_impl(**arguments)
    else:
        result = {"error": f"unknown tool: {name}"}
    return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]

# 6. 启动入口
async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

---

## 4. 完整示例：KSIPMS 只读 Server

### 4.1 文件路径

**代码**：`agent/mcp_servers/ksipms_server.py`  
**配置**：`agent/mcp_servers/config.yaml`

### 4.2 核心特性

1. **只读权限**：SQLite 以 `mode=ro` 打开
2. **白名单字段**：只暴露配置文件中声明的字段，隐藏敏感信息（如身份证号）
3. **审计日志**：每次查询记录 `audit_log` 表（best-effort）
4. **3 个工具**：
   - `query_alarms`：查询告警记录
   - `query_person`：查询人员信息
   - `query_video`：查询录像片段

### 4.3 代码片段解析

#### (1) 配置加载与数据库连接

```python
def load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)["ksipms_server"]

CONFIG = load_config()

def _ro_conn(db_path: Path) -> sqlite3.Connection:
    """只读连接（关键：mode=ro 防止写入）"""
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row  # 返回字典形式的行
    return conn
```

**为什么用 `mode=ro`？**
- SQLite 特有的只读模式，即使代码误写 INSERT/UPDATE，也会抛出异常
- 对于 MySQL/PostgreSQL，用只读用户或事务级 READ ONLY

#### (2) 白名单字段过滤

```python
def query_alarms_impl(date: str | None = None,
                      alarm_type: str | None = None,
                      camera_id: str | None = None) -> dict:
    # 从配置读取允许的字段（隐私保护）
    allowed_fields = CONFIG["table_fields"]["alarms"]
    sql = [f"SELECT {', '.join(allowed_fields)} FROM alarms WHERE 1=1"]
    # ... 构造 WHERE 条件 ...
```

**配置文件示例**（`config.yaml`）：

```yaml
ksipms_server:
  table_fields:
    alarms:
      - alarm_uuid
      - alarm_type
      - camera_id
      - severity
      - status
      # 不暴露: person_id（通过单独工具查询）
```

**为什么白名单？**
- 即使数据库表新增敏感字段（如 `internal_note`），MCP Server 也不会自动暴露
- 显式声明，便于审计

#### (3) 审计日志

```python
def _audit(db_path: Path, *, tool_name: str, args: dict) -> None:
    """Best-effort 审计：记录工具调用历史"""
    try:
        conn = _rw_conn(db_path)  # 审计需要写权限
        args_digest = hashlib.sha1(
            json.dumps(args, sort_keys=True).encode()
        ).hexdigest()[:8]
        conn.execute(
            "INSERT INTO audit_log(alarm_id,action,operator_id,payload,ts) VALUES (?,?,?,?,?)",
            (None, "mcp_tool_call", "mcp_server", 
             json.dumps({"tool": tool_name, "args_digest": args_digest}), int(time.time()))
        )
        conn.commit()
    except Exception:
        pass  # 失败不阻塞查询
```

**为什么 best-effort？**
- 审计失败（如权限不足）不应导致查询失败
- 适用于只读用户也能正常工作的场景

### 4.4 完整代码参考

详见：`agent/mcp_servers/ksipms_server.py`（约 270 行）

---

## 5. 完整示例：KSIPMS 只写 Server

### 5.1 文件路径

**代码**：`agent/mcp_servers/ksipms_write_server.py`  
**配置**：`agent/mcp_servers/config_write.yaml`

### 5.2 核心特性

1. **受控写入**：只能更新白名单字段（如 `status`, `processed_note`）
2. **状态值校验**：`status` 只能设为配置中声明的值（如 `closed`, `false_alarm`）
3. **强制审计**：写操作前强制记录 audit_log，失败则回滚事务
4. **1 个工具**：
   - `update_alarm_status`：复判后回写告警状态

### 5.3 代码片段解析

#### (1) 状态值白名单校验

```python
def update_alarm_status_impl(alarm_uuid: str, status: str, note: str = "") -> dict:
    # 1. 校验 status 白名单
    allowed_status = CONFIG["allowed_status_values"]
    if status not in allowed_status:
        return {"success": False, "error": f"status 必须是 {allowed_status} 之一"}
    
    # 2. 校验告警存在
    cur = conn.execute("SELECT status FROM alarms WHERE alarm_uuid=?", (alarm_uuid,))
    row = cur.fetchone()
    if not row:
        return {"success": False, "error": f"告警不存在: {alarm_uuid}"}
    old_status = row[0]
    
    # 3. 强制审计（事务内，失败则回滚）
    conn.execute(
        "INSERT INTO audit_log(...) VALUES (...)",
        (alarm_uuid, "agent_update_status", "agent:vlm_judge", 
         json.dumps({"old_status": old_status, "new_status": status}), now)
    )
    
    # 4. 受控更新（只动白名单字段）
    conn.execute(
        "UPDATE alarms SET status=?, processed_note=?, processed_at=?, processed_by=? WHERE alarm_uuid=?",
        (status, note, now, "agent:vlm_judge", alarm_uuid)
    )
    conn.commit()
```

**为什么强制审计？**
- 写操作必须可追溯，审计失败 = 写入失败
- 满足合规要求（如 SOC 2）

#### (2) 配置文件示例

```yaml
ksipms_write_server:
  db_path: "agent/data/ksipms_dev.db"
  allowed_status_values:
    - closed
    - false_alarm
  # 不允许: pending（避免智能体误改）
```

### 5.4 完整代码参考

详见：`agent/mcp_servers/ksipms_write_server.py`（约 130 行）

---

## 6. 配置文件规范

### 6.1 标准结构

```yaml
your_platform_server:
  # 数据源配置
  db_path: "path/to/db"
  db_type: "sqlite"  # 或 mysql, postgresql
  read_only: true    # 只读 Server 必须为 true
  
  # 白名单模式（推荐）
  allowed_tables:
    - table1
    - table2
  
  table_fields:
    table1:
      - field1
      - field2
      # 不暴露敏感字段
  
  # 工具定义
  tools:
    tool_name:
      description: "工具描述（会被 LLM 看到，需清晰准确）"
      parameters:
        type: object
        properties:
          param1:
            type: string
            description: "参数说明"
        required:
          - param1
      max_results: 20  # 限制返回结果数，防止爆内存
```

### 6.2 必需字段说明

| 字段 | 必需 | 说明 |
|------|------|------|
| `db_path` | ✅ | 数据库路径（相对 agent/ 目录） |
| `read_only` | ✅ | 只读 Server 必须为 `true` |
| `tools` | ✅ | 工具列表，每个工具对应一个 MCP Tool |
| `tools.<name>.description` | ✅ | LLM 根据此字段判断何时调用工具，需清晰 |
| `tools.<name>.parameters` | ✅ | JSON Schema，定义参数类型与必填项 |

---

## 7. 数据库对接最佳实践

### 7.1 支持的数据库类型

| 数据库 | Python 库 | 只读模式 |
|--------|-----------|----------|
| SQLite | `sqlite3` | `file:xxx?mode=ro` |
| MySQL | `pymysql` | 用只读用户 |
| PostgreSQL | `psycopg2` | `SET TRANSACTION READ ONLY` |
| Oracle | `cx_Oracle` | 用只读用户 |

### 7.2 连接池推荐

**单连接模式**（当前 KSIPMS 实现）：
```python
def _ro_conn(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path}?mode=ro"
    return sqlite3.connect(uri, uri=True)
```

**连接池模式**（高并发场景）：
```python
from dbutils.pooled_db import PooledDB
import pymysql

pool = PooledDB(
    creator=pymysql,
    maxconnections=10,
    host='localhost',
    user='readonly_user',
    password='xxx',
    database='platform_db',
)

def _ro_conn():
    return pool.connection()
```

### 7.3 数据库 Schema 映射

**场景**：真实平台的表名/字段名与 KSIPMS 不同。

**解决方案 1**：在 MCP Server 内部映射（推荐）

```python
# 配置文件
field_mapping:
  alarm_uuid: "platform_alarm_id"  # 左边=Skill期望, 右边=平台实际
  alarm_type: "event_type"

# 代码
def query_alarms_impl(...):
    mapping = CONFIG["field_mapping"]
    # 查询时用平台字段
    sql = f"SELECT {mapping['alarm_uuid']} as alarm_uuid, {mapping['alarm_type']} as alarm_type FROM alarms"
    # 返回时统一为 Skill 期望的字段名
```

**解决方案 2**：修改 Skill 参数定义

如果字段差异太大，直接修改 `skills/alarm_skills.py` 中的参数定义。

---

继续下一部分...

## 8. 多 Server 策略

### 8.1 何时使用单个 MCP Server？

**场景 1：简单只读平台**
- 只需查询数据，无写入需求
- 数据量小（表 < 10 张，工具 < 5 个）
- 无复杂权限控制

**示例**：报表查询平台、日志查询系统

### 8.2 何时拆分为多个 MCP Server？

**场景 2：读写分离**（强烈推荐）
- **只读 Server**：查询告警、人员、录像等
- **只写 Server**：更新告警状态、添加备注

**优点**：
- 安全性：只读 Server 无写权限，即使被滥用也无法破坏数据
- 审计清晰：写操作强制记录 audit_log
- 权限最小化：大部分 Skill 只需只读权限

**示例**：KSIPMS 当前实现（`ksipms_server.py` + `ksipms_write_server.py`）

---

**场景 3：按业务域拆分**
- **告警域 Server**：查询告警、统计、复判
- **视频域 Server**：查询录像、导出视频
- **人员域 Server**：查询人员、权限管理

**优点**：
- 职责清晰，易维护
- 可独立升级某个域的 Server
- 不同域可对接不同数据源（如告警在 MySQL，视频在 MongoDB）

**适用场景**：平台业务域清晰，每个域有独立数据库或 API

---

**场景 4：按数据源类型拆分**
- **DB Server**：查询数据库（SQL）
- **API Server**：调用 REST API
- **File Server**：读取文件（日志、配置）

**优点**：
- 不同 Server 用不同技术栈（SQL / HTTP / 文件 IO）
- 可并行开发

**适用场景**：平台数据分散在多种存储（关系型 DB + 对象存储 + 外部 API）

---

### 8.3 当前 KSIPMS 的多 Server 设计

**现状**：2 个 Server（读写分离）

| Server 名称 | 文件 | 职责 | 工具数 |
|-------------|------|------|--------|
| `ksipms` | `ksipms_server.py` | 只读查询 | 3 个 |
| `ksipms_write` | `ksipms_write_server.py` | 受控写入 | 1 个 |

**为什么这样设计？**
1. **安全第一**：只读 Server 即使被攻击，也无法篡改数据
2. **审计合规**：写操作强制记录 audit_log，满足 SOC 2 要求
3. **权限最小化**：95% 的 Skill 只需只读权限（如统计、查询、复判推理）

**是否还需要更多 Server？**

**当前不需要**：
- 当前业务域单一（告警管理），无需按域拆分
- 数据源单一（SQLite），无需按数据源拆分

**未来可能拆分的场景**：
- **视频域独立**：如果录像存储在独立的视频平台（如海康 API），拆分为 `video_server.py`
- **人员域独立**：如果人员数据来自 HR 系统外部 API，拆分为 `hr_server.py`

---

### 8.4 多 Server 对 Skill 的影响

**Skill Registry 自动注册**：
```python
# skills/mcp_skills.py
async def register_mcp_skills(registry: SkillRegistry, mcp_client) -> None:
    for server_name in mcp_client.list_servers():  # 遍历所有已连接的 Server
        tools = await mcp_client.list_tools(server_name)
        for tool in tools:
            skill = Skill(
                id=tool["name"],
                mcp_server=server_name,  # 记录来自哪个 Server
                # ...
            )
            registry.register(skill)
```

**对 Skill 开发者透明**：
- 无需关心工具来自哪个 Server
- Skill 调用 `query_alarms` 时，Executor 自动路由到 `ksipms` Server
- Skill 调用 `update_alarm_status` 时，Executor 自动路由到 `ksipms_write` Server

**字段名一致性要求**：
- 多个 Server 如果暴露同一实体（如 `alarms`），字段名必须一致
- 示例：`ksipms` Server 的 `query_alarms` 返回 `alarm_uuid`，`ksipms_write` Server 的 `update_alarm_status` 也用 `alarm_uuid`
- 避免：一个 Server 用 `alarm_uuid`，另一个用 `alarm_id`（会导致 Skill 逻辑混乱）

---

### 8.5 多 Server 策略决策树

```
是否需要写操作？
├─ 否 → 单个只读 Server
└─ 是 → 拆分为读 + 写两个 Server
        │
        └─ 业务域是否清晰分离？
           ├─ 否 → 保持 2 个 Server（读 + 写）
           └─ 是 → 按域拆分（如告警域 + 视频域 + 人员域）
                   │
                   └─ 数据源是否异构？
                      ├─ 否 → 保持当前拆分
                      └─ 是 → 按数据源再拆分（如 DB + API + File）
```

**推荐策略**：
1. **初期对接**：2 个 Server（读 + 写），满足 90% 场景
2. **业务复杂化**：按域拆分（告警 + 视频 + 人员），每个域 2 个 Server（读 + 写）
3. **数据源异构**：按数据源拆分（DB + API + File）

---

## 9. 测试与验证

### 9.1 单元测试

**测试文件**：`agent/tests/test_mcp_tools.py`

```python
import pytest
from mcp_servers.ksipms_server import query_alarms_impl

def test_query_alarms_basic():
    """测试基本查询"""
    result = query_alarms_impl(date="2026-06-01", alarm_type="smoking")
    assert result["total"] >= 0
    assert "items" in result
    for item in result["items"]:
        assert "alarm_uuid" in item
        assert item["alarm_type"] == "smoking"

def test_query_alarms_invalid_date():
    """测试非法日期"""
    result = query_alarms_impl(date="invalid")
    assert "error" in result
    assert "invalid date" in result["error"]

def test_query_alarms_max_results():
    """测试结果数限制"""
    result = query_alarms_impl()
    assert len(result["items"]) <= 20  # 配置的 max_results
```

### 9.2 端到端测试

**测试文件**：`agent/tests/test_e2e_mcp.py`

```python
import pytest
from mcp_adapter.client import MCPClient
from skills.registry import SkillRegistry
from skills.mcp_skills import register_mcp_skills

@pytest.mark.asyncio
async def test_mcp_integration():
    """测试 MCP Client → Server → Skill 完整链路"""
    # 1. 启动 MCP Client
    client = MCPClient()
    await client.connect("ksipms", "python -m mcp_servers.ksipms_server")
    
    # 2. 注册 Skill
    registry = SkillRegistry()
    await register_mcp_skills(registry, client)
    
    # 3. 验证工具已注册
    assert registry.has("query_alarms")
    
    # 4. 调用工具
    result = await client.call_tool("ksipms", "query_alarms", {"date": "2026-06-01"})
    assert "total" in result
```

### 9.3 手动验证清单

对接新平台后，按以下步骤验证：

**步骤 1**：独立启动 MCP Server
```bash
cd agent
python -m mcp_servers.your_platform_server
# 应输出：等待 stdio 输入（正常，MCP Server 是被动调用）
```

**步骤 2**：验证配置文件
```bash
python -c "from mcp_servers.your_platform_server import load_config; print(load_config())"
# 应输出配置内容，无报错
```

**步骤 3**：单元测试工具实现
```bash
python -c "
from mcp_servers.your_platform_server import tool_impl
result = tool_impl(param1='test')
print(result)
"
# 检查返回格式是否正确
```

**步骤 4**：集成测试（通过 LangGraph 调用）
```bash
cd agent/agent/web
python app.py
# 打开 Tab6 主智能体对话，输入："查询最近的告警"
# 观察是否调用了你的 MCP 工具
```

---

## 10. 常见问题与解决方案

### 10.1 字段名不匹配

**问题**：真实平台的字段名与 Skill 期望的不同。

**示例**：
- Skill 期望：`alarm_uuid`
- 平台实际：`event_id`

**解决方案 1**：MCP Server 内部映射（推荐）

```python
# config.yaml
field_mapping:
  alarm_uuid: "event_id"
  alarm_type: "event_category"

# Server 代码
mapping = CONFIG["field_mapping"]
sql = f"SELECT {mapping['alarm_uuid']} as alarm_uuid, {mapping['alarm_type']} as alarm_type FROM events"
```

**解决方案 2**：修改 Skill 定义

如果字段差异太大，直接修改 `skills/alarm_skills.py` 中的参数定义。

---

### 10.2 数据类型不兼容

**问题**：平台时间戳是字符串（ISO 格式），Skill 期望整数（epoch 秒）。

**解决方案**：在 MCP Server 内转换

```python
from datetime import datetime

def _iso_to_epoch(iso_str: str) -> int:
    return int(datetime.fromisoformat(iso_str).timestamp())

# 查询后转换
for row in rows:
    row["ts_event"] = _iso_to_epoch(row["ts_event"])
```

---

### 10.3 平台无审计表

**问题**：真实平台数据库没有 `audit_log` 表。

**解决方案 1**：跳过审计（不推荐）

```python
def _audit(...):
    pass  # 空实现
```

**解决方案 2**：审计到本地日志文件

```python
import logging
logger = logging.getLogger("mcp_audit")
logger.addHandler(logging.FileHandler("mcp_audit.log"))

def _audit(tool_name: str, args: dict):
    logger.info(f"tool={tool_name}, args={args}")
```

**解决方案 3**：创建独立审计表（推荐）

```sql
-- 在你的平台数据库或独立 SQLite 文件中
CREATE TABLE mcp_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_name TEXT,
    args_json TEXT,
    ts INTEGER,
    server_name TEXT
);
```

---

### 10.4 平台是 REST API 而非数据库

**问题**：真实平台只暴露 REST API，无直接数据库访问。

**解决方案**：用 `requests` 库调用 API

```python
import requests

def query_alarms_impl(date: str = None) -> dict:
    url = CONFIG["api_base_url"] + "/alarms"
    headers = {"Authorization": f"Bearer {CONFIG['api_token']}"}
    params = {"date": date} if date else {}
    
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        # 转换为统一格式
        return {
            "total": data["count"],
            "items": data["results"],
        }
    except requests.RequestException as e:
        return {"total": 0, "items": [], "error": str(e)}
```

---

### 10.5 平台返回字段过多

**问题**：API 返回 50 个字段，但 Skill 只需要 5 个。

**解决方案**：在 MCP Server 过滤

```python
allowed_fields = CONFIG["table_fields"]["alarms"]

def _filter_fields(items: list[dict]) -> list[dict]:
    return [{k: v for k, v in item.items() if k in allowed_fields} for item in items]

# 查询后过滤
result = api_call()
result["items"] = _filter_fields(result["items"])
```

---

### 10.6 如何调试 MCP Server？

**方法 1**：直接运行工具实现函数

```python
# 不通过 MCP 协议，直接测试逻辑
from mcp_servers.your_platform_server import query_alarms_impl
result = query_alarms_impl(date="2026-06-01")
print(result)
```

**方法 2**：MCP Server 日志

```python
import logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

def query_alarms_impl(...):
    logger.debug(f"查询参数: date={date}, alarm_type={alarm_type}")
    # ...
    logger.debug(f"返回结果: total={result['total']}")
```

**方法 3**：MCP Inspector（官方调试工具）

```bash
npm install -g @modelcontextprotocol/inspector
mcp-inspector python -m mcp_servers.your_platform_server
# 打开浏览器：http://localhost:5173
# 可视化调试 MCP Server
```

---

## 11. 快速上手：对接新平台的步骤

### 11.1 准备阶段（10 分钟）

**任务清单**：
- [ ] 确认平台数据源类型（数据库 / REST API / 文件）
- [ ] 获取访问凭证（数据库连接串 / API Token）
- [ ] 梳理需要暴露的实体（如 alarms / persons / videos）
- [ ] 梳理需要的操作（只读 / 读写 / 只写）

**输出**：
- 数据源类型：SQLite / MySQL / REST API / ...
- 需要暴露的表/接口：3-5 张表或 API 端点
- 操作类型：只读 / 读写分离

---

### 11.2 编写 MCP Server（60 分钟）

**步骤 1**：复制模板

```bash
cd agent/mcp_servers
cp ksipms_server.py your_platform_server.py
cp config.yaml config_your_platform.yaml
```

**步骤 2**：修改配置文件

```yaml
your_platform_server:
  db_path: "path/to/your/db"  # 或 api_base_url
  read_only: true
  
  table_fields:
    your_table:
      - field1
      - field2
  
  tools:
    your_tool:
      description: "清晰描述工具功能"
      parameters:
        type: object
        properties:
          param1:
            type: string
            description: "参数说明"
```

**步骤 3**：修改工具实现

```python
def your_tool_impl(param1: str) -> dict:
    # 1. 连接数据源
    conn = _ro_conn(db_path)  # 或调用 API
    
    # 2. 执行查询/操作
    rows = conn.execute("SELECT ... WHERE param1=?", (param1,)).fetchall()
    
    # 3. 转换为统一格式
    items = [dict(r) for r in rows]
    
    # 4. 审计（可选）
    _audit(db_path, tool_name="your_tool", args={"param1": param1})
    
    return {"total": len(items), "items": items}
```

**步骤 4**：注册工具

```python
@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="your_tool",
            description=CONFIG["tools"]["your_tool"]["description"],
            inputSchema=CONFIG["tools"]["your_tool"]["parameters"],
        ),
    ]

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "your_tool":
        result = your_tool_impl(**arguments)
    else:
        result = {"error": f"unknown tool: {name}"}
    return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]
```

---

### 11.3 集成到平台（20 分钟）

**步骤 1**：配置 LangGraph 连接新 Server

编辑 `agent/agent/web/app.py` 或配置文件：

```python
# 启动时连接 MCP Server
await mcp_client.connect("your_platform", "python -m mcp_servers.your_platform_server")
```

**步骤 2**：验证 Skill 自动注册

```bash
cd agent/agent/web
python app.py
# 打开 Tab6，输入："列出所有可用的工具"
# 应看到你的 your_tool
```

**步骤 3**：测试端到端调用

在主智能体对话框输入：
```
查询 <平台实体名称>
```

观察是否调用了你的 MCP 工具。

---

### 11.4 编写 Skill（可选，30 分钟）

如果 MCP 工具需要业务逻辑封装（如聚合统计、复杂过滤），编写对应 Skill：

```python
# skills/your_platform_skills.py
from skills.base import Skill, SkillType

def your_business_logic_impl(args: dict, context: dict) -> dict:
    # 调用 MCP 工具
    mcp_result = context["mcp_client"].call_tool("your_platform", "your_tool", args)
    
    # 业务逻辑处理（如聚合）
    processed = aggregate(mcp_result["items"])
    
    return {"result": processed}

# 注册 Skill
def register_your_platform_skills(registry: SkillRegistry):
    registry.register(Skill(
        id="your_business_logic",
        name="你的业务逻辑",
        description="描述",
        parameters={...},
        implementation=your_business_logic_impl,
        skill_type=SkillType.TOOL,
    ))
```

---

## 12. 总结与检查清单

### 12.1 核心要点回顾

1. **MCP Server = 数据源适配器**：不包含业务逻辑，只负责读/写数据
2. **读写分离**：强烈推荐拆分为只读 + 只写两个 Server
3. **白名单模式**：只暴露配置文件声明的字段，隐私保护第一
4. **审计必需**：写操作强制记录 audit_log，满足合规要求
5. **配置驱动**：所有可变参数放配置文件，避免硬编码

### 12.2 对接前检查清单

**开发阶段**：
- [ ] 确认 MCP Server 文件结构符合规范
- [ ] 配置文件包含所有必需字段（`db_path`, `read_only`, `tools`）
- [ ] 工具描述清晰准确（LLM 根据描述判断何时调用）
- [ ] 白名单字段已声明，敏感信息已隐藏
- [ ] 审计日志已实现（写操作必需）
- [ ] 只读 Server 用 `mode=ro` 或只读用户
- [ ] 只写 Server 有状态值白名单校验

**测试阶段**：
- [ ] 单元测试通过（工具实现函数）
- [ ] 端到端测试通过（MCP Client → Server → Skill）
- [ ] 手动验证通过（通过 Web 控制台调用）
- [ ] 字段名与 Skill 期望一致
- [ ] 数据类型与 Skill 期望一致（时间戳、枚举值等）

**上线阶段**：
- [ ] 配置文件已更新为生产数据源
- [ ] 数据库连接池已配置（如适用）
- [ ] 审计日志已启用
- [ ] 监控已接入（可选）
- [ ] 文档已更新（工具列表、字段说明）

---

## 13. 附录

### 13.1 完整代码索引

| 文件 | 说明 |
|------|------|
| `agent/mcp_servers/ksipms_server.py` | 只读 Server 完整实现（270 行） |
| `agent/mcp_servers/ksipms_write_server.py` | 只写 Server 完整实现（130 行） |
| `agent/mcp_servers/config.yaml` | 只读 Server 配置示例 |
| `agent/mcp_servers/config_write.yaml` | 只写 Server 配置示例 |
| `agent/skills/mcp_skills.py` | MCP 工具自动注册为 Skill（40 行） |
| `agent/tests/test_mcp_tools.py` | 单元测试示例 |
| `agent/tests/test_e2e_mcp.py` | 端到端测试示例 |

### 13.2 参考资源

- **MCP 协议规范**：https://spec.modelcontextprotocol.io/
- **MCP Python SDK**：https://github.com/modelcontextprotocol/python-sdk
- **项目背景文档**：`/root/.claude/projects/-mnt-data3-clip-LangGraph/memory/project_background.md`
- **RAG 实施文档**：`agent/plan/RAG_IMPLEMENTATION_PLAN.md`

---

**文档维护人**: Claude Opus 4.8  
**最后更新**: 2026-06-09  
**版本**: V1.0

