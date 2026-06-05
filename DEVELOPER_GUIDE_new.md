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

<!-- APPEND_HERE -->
