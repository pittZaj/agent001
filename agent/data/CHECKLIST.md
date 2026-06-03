# 数据库准备清单 - Agent-of-Agent ⇄ 安全生产平台对接

> 本文件是您与公司平台对接时的**对照清单**：开发期我们用 SQLite 模拟，等
> 公司平台真实库可访问时，按此清单逐表对齐字段即可，最小化代码改动。
>
> 命名风格故意保留蛇形小写、UTC epoch、JSON 兜底列，与多数事件中台/IoT 平台对齐。

## 0. 一句话原则

**字段命名以"工具调用契约"反推**——`query_alarms(date, alarm_type, camera_id)`
就要求 `alarms` 表里必须有 `ts_event / alarm_type / camera_id` 三列，索引也按这三列建。
凡是工具参数空间之外的字段（如设备厂商、内部业务编号），都进 `raw_payload` JSON 列兜底，
保证未来平台来什么字段都不需要改 schema。

---

## 1. 表与文件清单

| # | 表名 | 行数（mock） | 主要用途 | 是否对接平台 | 备注 |
|---|------|----|---------|-----|-----|
| 1 | `areas` | 5 | 厂区/车间/工位三级 | ✅ 必须 | parent_area_id 自引用 |
| 2 | `cameras` | 12 | 摄像头主数据 | ✅ 必须 | rtsp_url 用占位 |
| 3 | `persons` | 30 | 人员主数据 | ✅ 必须 | photo_url 占位 |
| 4 | `alarm_types` | 8 | 告警类型字典 | ✅ 必须 | 至少 4 类对齐工具参数 |
| 5 | `alarms` ★ | 200~400 | **核心告警表** | ✅ 必须 | 7 天数据 |
| 6 | `video_clips` | ~30% of alarms | 录像片段索引 | ⚠️ 可选 | 占位 file_path |
| 7 | `operators` | 8 | 处置人 | ✅ 推荐 | role: 安全员/班长/管理员 |
| 8 | `audit_log` | runtime | 审计追溯 | ✅ 必须 | RULES §4 强制写 |
| 9 | `safety_rules` | 占位 5 条 | 后期 RAG 入口 | ⚠️ 后期 | 文本 + version |

文件落点：

```
data/
├── schema.sql           ← SQLite DDL（开发用）
├── schema_mysql.sql     ← MySQL 8.0 DDL（平台对接用）
├── seed.py              ← 生成 mock 数据
├── ksipms_dev.db        ← 由 seed.py 生成（gitignore）
└── README.md            ← 表关系图 + 联动说明
```

---

## 2. 核心表 `alarms` 字段清单（重点）

| 字段 | 类型(SQLite/MySQL) | 必填 | 平台对接备注 |
|------|--------|------|-----|
| `alarm_uuid` | TEXT / VARCHAR(36) | ✅ | 平台事件 ID；UUIDv4 |
| `alarm_type` | TEXT / VARCHAR(32) | ✅ | 与 `alarm_types.type_code` 一致；**工具参数对齐** |
| `camera_id` | TEXT / VARCHAR(64) | ✅ | 与 `cameras.camera_id` 一致；**工具参数对齐** |
| `area_id` | TEXT / VARCHAR(64) | ✅ | 冗余字段，方便按区域聚合 |
| `person_id` | TEXT / VARCHAR(64) | ⚠️ | 没识别到人员时为 NULL |
| `severity` | INTEGER (1-5) / TINYINT | ✅ | 1=info, 5=critical |
| `status` | TEXT / VARCHAR(16) | ✅ | pending/acknowledged/processing/closed/false_alarm |
| `ts_event` | INTEGER / BIGINT | ✅ | UTC epoch 秒；**工具参数 date 即对此字段过滤** |
| `snapshot_url` | TEXT / VARCHAR(512) | ⚠️ | 截图 URL，开发期占位 |
| `video_clip_id` | TEXT / VARCHAR(36) | ⚠️ | FK → video_clips |
| `model_conf` | REAL / DOUBLE | ⚠️ | 模型置信度 0-1 |
| `alarm_desc` | TEXT / VARCHAR(512) | ⚠️ | 自然语言描述 |
| `processed_at` | INTEGER / BIGINT | ⚠️ | 处置时间 |
| `processed_by` | TEXT / VARCHAR(64) | ⚠️ | FK → operators.operator_id |
| `processed_note` | TEXT / TEXT | ⚠️ | 处置备注 |
| `raw_payload` | TEXT (JSON) / JSON | ⚠️ | **兜底列**：平台原始字段都塞进来 |
| `created_at` | INTEGER / BIGINT | ✅ | 入库时间，与 ts_event 区分 |

索引：`(ts_event)`、`(alarm_type, ts_event)`、`(camera_id, ts_event)`、`(status, ts_event)`。

### 平台对接时的字段映射模板（您填）

> 等拿到公司平台真实 schema 时，把下表填好，就能写出迁移 SQL：

| 我们的字段 | 平台字段 | 转换 |
|---|---|---|
| `alarm_uuid` | `?` | 原样 |
| `alarm_type` | `?` | 平台枚举值 → 我们的 type_code（在 `alarm_types.platform_code` 留映射列） |
| `camera_id` | `?` | 原样或 trim |
| `ts_event` | `?` | 平台时间戳（毫秒/字符串）→ UTC epoch 秒 |
| `raw_payload` | `*` | 平台原始 JSON 全塞这里 |
| ...（其余字段同理） |

---

## 3. 联动逻辑（对应"告警信息数据库存储情况"）

> 这是您问的"与之联动的告警信息数据库存储情况"——我们模拟以下三条联动：

### 3.1 摄像头 → 告警

`cameras.camera_id` 是 `alarms.camera_id` 的外键。每个摄像头每天产生
0~10 条告警，按工位密度加权（`areas.level=3` 工位摄像头告警密度更高）。

### 3.2 告警 → 录像片段

约 30% 的 `alarms` 行有对应 `video_clips` 记录：
- `video_clips.alarm_id` → `alarms.alarm_uuid`
- `video_clips.ts_start = alarms.ts_event - 30s`
- `video_clips.ts_end   = alarms.ts_event + 30s`
- 60 秒片段，对应 `query_video(camera_id, start_time, end_time)` 工具

### 3.3 告警 → 处置审计

约 70% 已闭环的告警（status ∈ {closed, false_alarm}）有 `processed_by/processed_at/processed_note`，
并在 `audit_log` 表写入一条 `'manual_close'` 记录。

`audit_log` 在生产环境还会被 Agent 自身写入（RULES §4 强制）。

---

## 4. 与现有工具契约的对齐校验

| 工具 (config.yaml:12-31) | 用到的列 | 校验 SQL |
|---|---|---|
| `query_alarms(date, alarm_type, camera_id)` | alarms 全表 | `SELECT * FROM alarms WHERE date(ts_event,'unixepoch')=:date [AND alarm_type=:t] [AND camera_id=:c]` |
| `query_video(camera_id, start_time, end_time)` | video_clips | `SELECT * FROM video_clips WHERE camera_id=:c AND ts_start>=:s AND ts_end<=:e` |
| `query_person(person_id)` | persons + (alarms 聚合) | `SELECT p.*, COUNT(a.alarm_uuid) recent FROM persons p LEFT JOIN alarms a ON a.person_id=p.person_id AND a.ts_event >= :t WHERE p.person_id=:pid GROUP BY p.person_id` |

---

## 5. 后期演进（写在这里防止忘）

- **MCP 工具协议接入**：本清单中的工具签名直接搬到 MCP server 的 `tools` 数组即可。
- **RAG/safety_rules**：当前先建表占位 5 条规章，下一阶段把 `content` 切片送向量库。
- **多 Agent 协同**：在 `agents` 表（未建）里记录 Agent 注册信息；当前由 JSON 注册表替代，后期再迁。
- **隐私字段**：`persons.photo_url / persons.name` 等真实数据时需 RBAC，schema 里预留 `data_class` 列。

---

## 6. 验收命令

```bash
cd /mnt/data3/clip/LangGraph/agent/agent
conda activate agent
python data/seed.py

sqlite3 data/ksipms_dev.db <<'SQL'
SELECT 'areas',        COUNT(*) FROM areas       UNION ALL
SELECT 'cameras',      COUNT(*) FROM cameras     UNION ALL
SELECT 'persons',      COUNT(*) FROM persons     UNION ALL
SELECT 'alarm_types',  COUNT(*) FROM alarm_types UNION ALL
SELECT 'alarms',       COUNT(*) FROM alarms      UNION ALL
SELECT 'video_clips',  COUNT(*) FROM video_clips UNION ALL
SELECT 'operators',    COUNT(*) FROM operators   UNION ALL
SELECT 'safety_rules', COUNT(*) FROM safety_rules;
SQL
```

预期输出：alarms 在 200~400 之间，其它表与 §1 数量一致。
