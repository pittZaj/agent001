-- Agent-of-Agent 模拟数据库 Schema (MySQL 8.0)
-- 与 schema.sql (SQLite) 字段语义保持一致；平台对接时直接用此文件建库。

SET NAMES utf8mb4;
SET foreign_key_checks = 1;

-- ------------------------------------------------------------------
-- 1. areas
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS areas (
    area_id         VARCHAR(64)  NOT NULL PRIMARY KEY,
    name            VARCHAR(128) NOT NULL,
    parent_area_id  VARCHAR(64)  NULL,
    level           TINYINT      NOT NULL,
    created_at      BIGINT       NOT NULL,
    CONSTRAINT chk_areas_level CHECK (level BETWEEN 1 AND 3),
    CONSTRAINT fk_areas_parent FOREIGN KEY (parent_area_id) REFERENCES areas(area_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------------
-- 2. cameras
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cameras (
    camera_id       VARCHAR(64)  NOT NULL PRIMARY KEY,
    name            VARCHAR(128) NOT NULL,
    area_id         VARCHAR(64)  NOT NULL,
    rtsp_url        VARCHAR(512) NULL,
    status          VARCHAR(16)  NOT NULL DEFAULT 'online',
    created_at      BIGINT       NOT NULL,
    KEY idx_cameras_area (area_id),
    CONSTRAINT chk_cameras_status CHECK (status IN ('online','offline','maintenance')),
    CONSTRAINT fk_cameras_area FOREIGN KEY (area_id) REFERENCES areas(area_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------------
-- 3. persons
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS persons (
    person_id       VARCHAR(64)  NOT NULL PRIMARY KEY,
    name            VARCHAR(128) NOT NULL,
    department      VARCHAR(128) NULL,
    role            VARCHAR(64)  NULL,
    photo_url       VARCHAR(512) NULL,
    data_class      VARCHAR(32)  DEFAULT 'internal',
    created_at      BIGINT       NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------------
-- 4. alarm_types
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS alarm_types (
    type_code           VARCHAR(32)  NOT NULL PRIMARY KEY,
    display_name        VARCHAR(128) NOT NULL,
    platform_code       VARCHAR(64)  NULL,
    severity_default    TINYINT      NOT NULL,
    description         VARCHAR(512) NULL,
    CONSTRAINT chk_at_sev CHECK (severity_default BETWEEN 1 AND 5)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------------
-- 5. operators
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS operators (
    operator_id     VARCHAR(64)  NOT NULL PRIMARY KEY,
    name            VARCHAR(128) NOT NULL,
    role            VARCHAR(64)  NOT NULL,
    contact         VARCHAR(128) NULL,
    created_at      BIGINT       NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------------
-- 6. alarms ★（先建，因为 video_clips 引用它）
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS alarms (
    alarm_uuid          VARCHAR(36)  NOT NULL PRIMARY KEY,
    alarm_type          VARCHAR(32)  NOT NULL,
    camera_id           VARCHAR(64)  NOT NULL,
    area_id             VARCHAR(64)  NOT NULL,
    person_id           VARCHAR(64)  NULL,
    severity            TINYINT      NOT NULL,
    status              VARCHAR(16)  NOT NULL DEFAULT 'pending',
    ts_event            BIGINT       NOT NULL,
    snapshot_url        VARCHAR(512) NULL,
    video_clip_id       VARCHAR(36)  NULL,
    model_conf          DOUBLE       NULL,
    alarm_desc          VARCHAR(512) NULL,
    processed_at        BIGINT       NULL,
    processed_by        VARCHAR(64)  NULL,
    processed_note      TEXT         NULL,
    raw_payload         JSON         NULL,
    created_at          BIGINT       NOT NULL,
    KEY idx_alarms_ts          (ts_event),
    KEY idx_alarms_type_ts     (alarm_type, ts_event),
    KEY idx_alarms_camera_ts   (camera_id, ts_event),
    KEY idx_alarms_status_ts   (status, ts_event),
    KEY idx_alarms_person      (person_id),
    CONSTRAINT chk_alarms_sev    CHECK (severity BETWEEN 1 AND 5),
    CONSTRAINT chk_alarms_status CHECK (status IN ('pending','acknowledged','processing','closed','false_alarm')),
    CONSTRAINT fk_alarms_type    FOREIGN KEY (alarm_type) REFERENCES alarm_types(type_code),
    CONSTRAINT fk_alarms_camera  FOREIGN KEY (camera_id)  REFERENCES cameras(camera_id),
    CONSTRAINT fk_alarms_area    FOREIGN KEY (area_id)    REFERENCES areas(area_id),
    CONSTRAINT fk_alarms_person  FOREIGN KEY (person_id)  REFERENCES persons(person_id),
    CONSTRAINT fk_alarms_oper    FOREIGN KEY (processed_by) REFERENCES operators(operator_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------------
-- 7. video_clips
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS video_clips (
    clip_id         VARCHAR(36)  NOT NULL PRIMARY KEY,
    alarm_id        VARCHAR(36)  NULL,
    camera_id       VARCHAR(64)  NOT NULL,
    ts_start        BIGINT       NOT NULL,
    ts_end          BIGINT       NOT NULL,
    file_path       VARCHAR(512) NULL,
    duration_sec    INT          NOT NULL,
    created_at      BIGINT       NOT NULL,
    KEY idx_video_camera_ts (camera_id, ts_start),
    CONSTRAINT fk_video_alarm  FOREIGN KEY (alarm_id)  REFERENCES alarms(alarm_uuid),
    CONSTRAINT fk_video_camera FOREIGN KEY (camera_id) REFERENCES cameras(camera_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------------
-- 8. audit_log
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_log (
    id              BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
    alarm_id        VARCHAR(36)  NULL,
    action          VARCHAR(64)  NOT NULL,
    operator_id     VARCHAR(64)  NULL,
    payload         JSON         NULL,
    ts              BIGINT       NOT NULL,
    KEY idx_audit_alarm    (alarm_id),
    KEY idx_audit_action_ts (action, ts)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------------
-- 9. safety_rules
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS safety_rules (
    rule_id         VARCHAR(64)  NOT NULL PRIMARY KEY,
    title           VARCHAR(256) NOT NULL,
    content         TEXT         NOT NULL,
    version         VARCHAR(32)  NOT NULL,
    category        VARCHAR(64)  NULL,
    created_at      BIGINT       NOT NULL,
    KEY idx_rules_category (category)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
