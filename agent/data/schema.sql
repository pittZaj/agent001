-- Agent-of-Agent 模拟数据库 Schema (SQLite)
-- 与 schema_mysql.sql 字段语义保持完全一致；切平台仅需替换为 schema_mysql.sql。

PRAGMA foreign_keys = ON;

-- ------------------------------------------------------------------
-- 1. areas: 厂区/车间/工位三级
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS areas (
    area_id         TEXT    PRIMARY KEY,
    name            TEXT    NOT NULL,
    parent_area_id  TEXT    REFERENCES areas(area_id),
    level           INTEGER NOT NULL CHECK (level BETWEEN 1 AND 3),
    created_at      INTEGER NOT NULL
);

-- ------------------------------------------------------------------
-- 2. cameras: 摄像头主数据
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cameras (
    camera_id       TEXT    PRIMARY KEY,
    name            TEXT    NOT NULL,
    area_id         TEXT    NOT NULL REFERENCES areas(area_id),
    rtsp_url        TEXT,
    status          TEXT    NOT NULL DEFAULT 'online' CHECK (status IN ('online','offline','maintenance')),
    created_at      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cameras_area ON cameras(area_id);

-- ------------------------------------------------------------------
-- 3. persons: 人员主数据
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS persons (
    person_id       TEXT    PRIMARY KEY,
    name            TEXT    NOT NULL,
    department      TEXT,
    role            TEXT,
    photo_url       TEXT,
    data_class      TEXT    DEFAULT 'internal',
    created_at      INTEGER NOT NULL
);

-- ------------------------------------------------------------------
-- 4. alarm_types: 告警类型字典
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS alarm_types (
    type_code           TEXT    PRIMARY KEY,
    display_name        TEXT    NOT NULL,
    platform_code       TEXT,                 -- 平台对接时填映射
    severity_default    INTEGER NOT NULL CHECK (severity_default BETWEEN 1 AND 5),
    description         TEXT
);

-- ------------------------------------------------------------------
-- 5. operators: 处置人
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS operators (
    operator_id     TEXT    PRIMARY KEY,
    name            TEXT    NOT NULL,
    role            TEXT    NOT NULL,
    contact         TEXT,
    created_at      INTEGER NOT NULL
);

-- ------------------------------------------------------------------
-- 6. video_clips: 录像片段索引
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS video_clips (
    clip_id         TEXT    PRIMARY KEY,
    alarm_id        TEXT    REFERENCES alarms(alarm_uuid),
    camera_id       TEXT    NOT NULL REFERENCES cameras(camera_id),
    ts_start        INTEGER NOT NULL,
    ts_end          INTEGER NOT NULL,
    file_path       TEXT,
    duration_sec    INTEGER NOT NULL,
    created_at      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_video_camera_ts ON video_clips(camera_id, ts_start);

-- ------------------------------------------------------------------
-- 7. alarms ★ 核心表
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS alarms (
    alarm_uuid          TEXT    PRIMARY KEY,
    alarm_type          TEXT    NOT NULL REFERENCES alarm_types(type_code),
    camera_id           TEXT    NOT NULL REFERENCES cameras(camera_id),
    area_id             TEXT    NOT NULL REFERENCES areas(area_id),
    person_id           TEXT    REFERENCES persons(person_id),
    severity            INTEGER NOT NULL CHECK (severity BETWEEN 1 AND 5),
    status              TEXT    NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','acknowledged','processing','closed','false_alarm')),
    ts_event            INTEGER NOT NULL,
    snapshot_url        TEXT,
    video_clip_id       TEXT,
    model_conf          REAL,
    alarm_desc          TEXT,
    processed_at        INTEGER,
    processed_by        TEXT    REFERENCES operators(operator_id),
    processed_note      TEXT,
    raw_payload         TEXT,                 -- JSON 兜底
    created_at          INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alarms_ts                ON alarms(ts_event);
CREATE INDEX IF NOT EXISTS idx_alarms_type_ts           ON alarms(alarm_type, ts_event);
CREATE INDEX IF NOT EXISTS idx_alarms_camera_ts         ON alarms(camera_id, ts_event);
CREATE INDEX IF NOT EXISTS idx_alarms_status_ts         ON alarms(status, ts_event);
CREATE INDEX IF NOT EXISTS idx_alarms_person            ON alarms(person_id);

-- ------------------------------------------------------------------
-- 8. audit_log: Agent 审计追溯
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    alarm_id        TEXT,                       -- 不强制 FK, agent 调用未必针对单 alarm
    action          TEXT    NOT NULL,           -- tool_call / manual_close / publish ...
    operator_id     TEXT,                       -- agent:<name> 或真实 operator_id
    payload         TEXT,                       -- JSON: trace_id, tool, args_digest...
    ts              INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_alarm    ON audit_log(alarm_id);
CREATE INDEX IF NOT EXISTS idx_audit_action_ts ON audit_log(action, ts);

-- ------------------------------------------------------------------
-- 9. safety_rules: 后期 RAG 入口（占位）
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS safety_rules (
    rule_id         TEXT    PRIMARY KEY,
    title           TEXT    NOT NULL,
    content         TEXT    NOT NULL,
    version         TEXT    NOT NULL,
    category        TEXT,
    created_at      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rules_category ON safety_rules(category);
