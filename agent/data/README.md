# data/ — 模拟数据库

模拟"安全生产平台"的数据底座，开发期用 SQLite，生产对接公司平台时用 `schema_mysql.sql`。

## 文件

- `CHECKLIST.md` — 您的对接清单（必读）
- `schema.sql` — SQLite DDL
- `schema_mysql.sql` — MySQL 8.0 DDL（与 SQLite 字段语义 1:1 对齐）
- `seed.py` — 生成 mock 数据（固定 seed=42，可复现）
- `ksipms_dev.db` — 由 `seed.py` 生成（gitignore）

## 表关系（ER 简图）

```
areas (1) ──< cameras (1) ──< alarms >── (N) alarm_types
              │                  │
              │                  ├── (N) video_clips
              │                  └── (N) audit_log
              └────< (M) persons >── (N) alarms
                              ↓
                          operators (处置)
safety_rules (独立, 后期 RAG)
```

## 重新生成

```bash
conda activate agent
cd /mnt/data3/clip/LangGraph/agent/agent
python data/seed.py    # 会先 unlink 旧 .db
```

## 工具契约对应

| 工具 | 主表 | 主索引 |
|---|---|---|
| `query_alarms(date, alarm_type, camera_id)` | `alarms` | `idx_alarms_type_ts`, `idx_alarms_camera_ts` |
| `query_video(camera_id, start_time, end_time)` | `video_clips` | `idx_video_camera_ts` |
| `query_person(person_id)` | `persons` + `alarms` 聚合 | `idx_alarms_person` |

## 平台对接时

1. 导入 `schema_mysql.sql` 到平台数据库
2. 把字段映射填到 `CHECKLIST.md §2` 表格
3. 改 `agent_code.py` 中工具实现的连接串（仅 1 行）
4. 字段名差异通过 `alarm_types.platform_code` + `alarms.raw_payload` 兜底
