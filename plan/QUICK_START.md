# 复杂任务编排验证 — 快速启动指南

**适用角色**: 演示人员、测试人员、领导  
**预计阅读时间**: 3 分钟  
**前置条件**: vLLM 服务已启动（Qwen3-VL-4B，端口 8004）

---

## 一、最快体验路径（5 分钟）

### 启动 Gradio 控制台

**⚠️ 重要：必须在 `agent` conda 环境下启动**（Python 3.10）

```bash
# 1. 激活 conda 环境
source /root/anaconda3/bin/activate agent

# 2. 启动 Gradio 控制台
cd /mnt/data3/clip/LangGraph/agent/agent/web
python app.py

# 浏览器访问 http://localhost:7860
# 打开 Tab6: Agent 对话测试
```

**首次运行若报 `ModuleNotFoundError: No module named 'mcp'`**：
```bash
source /root/anaconda3/bin/activate agent
pip install mcp
```

### 三个推荐 Demo（按顺序体验）

#### Demo A: 统计+可视化（最直观）✨

**输入框输入**:
```
统计每种告警类型数量并画柱状图
```

**预期效果**（15-20s）:
- 自动执行 2 步：聚合统计 → 生成柱状图
- 返回表格摘要 + "已生成柱状图"提示
- 调试面板显示 plan 和 base64 图片

**关键亮点**: 步骤间传参（`{{step_0.data}}`），中文图表

---

#### Demo B: 复判闭环（最震撼）🔥

**准备工作**: 先拿一个测试告警的 UUID
```bash
sqlite3 /mnt/data3/clip/LangGraph/agent/agent/data/ksipms_dev.db \
  "SELECT alarm_uuid FROM alarms WHERE status='pending' AND alarm_desc LIKE '%测试数据%' LIMIT 1"
```

**输入框输入**（替换 UUID）:
```
复判告警 <刚才查到的UUID>，并根据复判结论回写它的状态
```

**预期效果**（25-30s）:
- 步骤1: VLM 多模态推理（图片 → 判定 + 理由 + 置信度）
- 步骤2: 根据 verdict 自动回写数据库（confirmed→closed 或 rejected→false_alarm）
- 返回完整的复判报告（含推理过程）

**验证回写成功**:
```bash
sqlite3 /mnt/data3/clip/LangGraph/agent/agent/data/ksipms_dev.db \
  "SELECT status, processed_by, processed_note FROM alarms WHERE alarm_uuid='<UUID>'"
# 应看到 status 变化 + processed_by='agent:vlm_judge'

# 查看审计日志
sqlite3 /mnt/data3/clip/LangGraph/agent/agent/data/ksipms_dev.db \
  "SELECT payload FROM audit_log WHERE alarm_id='<UUID>' ORDER BY ts DESC LIMIT 1"
```

**关键亮点**: VLM 推理 + 步骤引用 + 受控回写 + 审计

---

#### Demo C: 自由提问（测试编排灵活性）

**推荐问题**（任选）:
```
最近 7 天每天的告警数量趋势，给我一个折线图

查询昨天的 smoking 告警，并统计每种告警类型的数量

查一下有录像的告警，然后回溯它前后 10 秒的录像片段
```

**关键亮点**: Planner 自动生成多步 plan，Executor 正确传参执行

---

## 二、常见问题排查

### Q1: "主智能体(增强)" 首次调用很慢（10s+）

**原因**: 首次加载 Skill Registry + 预热主图  
**解决**: 正常现象，后续对话会复用已加载的图（2-3s 响应）

---

### Q2: 复判报错 "VLM 调用失败"

**排查**:
```bash
# 1. 确认 vLLM 服务在 8004 端口
curl http://127.0.0.1:8004/v1/models

# 2. 确认配置正确
grep -A5 "^llm:" /mnt/data3/clip/LangGraph/agent/config.yaml

# 3. 手动测试 VLM
python3 -c "
from utils.vlm import get_vlm_client
vlm = get_vlm_client()
print('VLM 可用:', vlm.client is not None)
"
```

---

### Q3: 可视化图表中文显示为方块

**原因**: matplotlib 字体未加载（理论上已修复）  
**验证**:
```python
python3 -c "
from skills.alarm_skills import *
import matplotlib
print('当前字体:', matplotlib.rcParams['font.sans-serif'])
"
# 应显示 ['Noto Sans CJK JP', 'DejaVu Sans']
```

---

### Q4: FastAPI 服务启动失败（8001 端口）

**已知问题**: 
1. starlette 版本不兼容 → 已降级到 0.38.6
2. 8000 端口被占用 → config.yaml 已改为 8001
3. 启动耗时长（30-40s）→ Skill Registry + 图预热正常

**跳过方案**: 直接用 Gradio 控制台演示（不依赖 FastAPI）

---

## 三、数据管理

### 重置测试数据

```bash
cd /mnt/data3/clip/LangGraph/agent
python agent/data/seed_test_alarms.py

# 验证入库结果
sqlite3 agent/data/ksipms_dev.db \
  "SELECT alarm_type, COUNT(*) FROM alarms WHERE alarm_desc LIKE '%测试数据%' GROUP BY alarm_type"
```

### 清理已复判的测试数据

```bash
sqlite3 agent/data/ksipms_dev.db \
  "UPDATE alarms SET status='pending', processed_at=NULL, processed_by=NULL, processed_note=NULL 
   WHERE alarm_desc LIKE '%测试数据%'"
```

---

## 四、核心文件速查

| 文件路径 | 作用 | 何时查看 |
|---------|------|---------|
| `plan/COMPLEXITY_VALIDATION_PLAN.md` | 规划书 | 了解架构设计思路 |
| `plan/DELIVERY_SUMMARY.md` | 交付总结 | 确认完成情况 + 验收标准 |
| `skills/alarm_skills.py` | 告警业务 Skills | 查看聚合/可视化实现 |
| `skills/vlm_judge_subgraph.py` | 复判子图 | 了解 VLM 复判流程 |
| `graph/nodes.py` | Plan-Execute 主图 | 调试步骤间传参逻辑 |
| `agent/data/ksipms_dev.db` | SQLite 数据库 | 验证回写结果 |

---

## 五、演示建议（给领导看）

### 5 分钟标准流程

1. **打开 Gradio 控制台** → Tab6，展示"主智能体(增强)"在下拉框首位
2. **Demo B（复判闭环）** → 强调 VLM 推理 + 自动回写 + 审计
3. **打开 DBeaver** → 展示 `alarms` 表 status 变化 + `audit_log` 记录
4. **Demo A（可视化）** → 展示中文柱状图生成
5. **总结价值** → "从调一个工具到多步骤跨模态编排，为 RAG 知识库奠定基础"

### 关键话术

- "这是**端到端的业务闭环**：查询 → VLM 复判 → 数据库回写 → 审计"
- "**步骤间传参**让后续步骤能引用前面的输出，实现真正的数据流编排"
- "**8 类告警泛化**：从 4 类硬编码升级到表驱动，可随业务动态扩展"
- "**中文可视化**：折线图/柱状图/饼图，直接嵌入前端或报告"

---

## 六、下一步

完成本次验证后，按 `ROADMAP.md` 进入：

- **阶段 3**: RAG 知识库集成（Qdrant + 规章制度文档）
- **阶段 4**: Agent-of-Agent 深化（元智能体默认用 Skill Registry）
- **阶段 5**: 生产优化（重规划、并发执行、缓存、流式输出）

---

**文档维护者**: KSAgent 项目组  
**最后更新**: 2026-06-05
