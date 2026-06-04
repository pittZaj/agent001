# Agent Spec: alarm_query_agent

## 1. 元数据

- name: alarm_query_agent
- version: 0.1.0
- owner: ksipms-team
- created_at: 2026-06-03

## 2. 业务目标

查询安全生产平台的告警记录：按日期/类型/摄像头筛选，并按类型统计数量。

## 3. 用户场景

- 场景 1：用户问"今天的告警"，Agent 调用 query_alarms(date=今天)，返回按类型聚合的数量
- 场景 2：用户指定日期 + 类型，例如"2026-06-01 的抽烟告警"，Agent 提取参数 date 与 alarm_type
- 场景 3：用户用别名（如"抽烟"对应 smoking、"没戴安全帽"对应 no_helmet），Agent 完成中英映射

## 4. 可用工具

| name | description | parameters | data_source |
|---|---|---|---|
| query_alarms | 查询告警记录, 可按日期/类型/摄像头筛选 | date:YYYY-MM-DD, alarm_type:str, camera_id:str | sqlite:data/ksipms_dev.db.alarms |

## 5. 数据访问

- 数据库: sqlite
- 路径: data/ksipms_dev.db
- 表: alarms
- 只读: true

## 6. 知识库

暂未支持，后续 RAG 阶段填充。

## 7. 测试用例

| input | expected_tool | expected_args_contains | expected_output_contains |
|---|---|---|---|
| 今天发生了哪些告警？ | query_alarms | {"date":"<TODAY>"} | 告警 |
| 查询昨天的抽烟告警 | query_alarms | {"alarm_type":"smoking"} | smoking |
| 最近未戴安全帽的告警 | query_alarms | {"alarm_type":"no_helmet"} | helmet |

## 8. 验收指标

- tool_accuracy >= 0.8
- execution_success >= 0.9
- overall_score >= 0.7

## 9. Token 预算

- max_iterations: 1
- max_input_tokens: 50000
- max_output_tokens: 20000
