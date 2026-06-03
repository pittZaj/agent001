"""
生成 ksipms_dev.db 的模拟数据。

固定 seed，多次运行结果一致；可重入（DROP TABLE 后重建）。
规模见 data/CHECKLIST.md §1。
"""
from __future__ import annotations

import json
import random
import sqlite3
import time
import uuid
from pathlib import Path

DATA_DIR = Path(__file__).parent
DB_PATH = DATA_DIR / "ksipms_dev.db"
SCHEMA_PATH = DATA_DIR / "schema.sql"

random.seed(42)
NOW = int(time.time())
DAY = 86400
DAYS_BACK = 7

ALARM_TYPES = [
    ("smoking",    "抽烟",         "SMOKE",     4, "禁烟区域检测到抽烟行为"),
    ("no_helmet",  "未戴安全帽",   "NO_HELMET", 3, "进入工地未佩戴安全帽"),
    ("phone",      "接打电话",     "PHONE",     2, "工作期间接打电话"),
    ("no_mask",    "未戴口罩",     "NO_MASK",   2, "洁净区未佩戴口罩"),
    ("intrusion",  "区域入侵",     "INTRUSION", 5, "未授权进入危险区域"),
    ("fall_down",  "人员摔倒",     "FALL",      5, "检测到人员摔倒"),
    ("fire_smoke", "明火/烟雾",    "FIRE",      5, "明火或烟雾告警"),
    ("ppe_other",  "其他防护缺失", "PPE",       3, "其它 PPE 缺失"),
]

AREAS = [
    ("AREA01", "总厂区",         None,     1),
    ("AREA02", "1号车间",        "AREA01", 2),
    ("AREA03", "2号车间",        "AREA01", 2),
    ("AREA04", "焊接工位A",      "AREA02", 3),
    ("AREA05", "装配工位B",      "AREA03", 3),
]

DEPARTMENTS = ["施工部", "电气部", "质量部", "安环部", "总务"]
PERSON_ROLES = ["工人", "班长", "技术员", "巡检"]

OPERATORS = [
    ("OP001", "李安全",   "安全员"),
    ("OP002", "王主管",   "主管"),
    ("OP003", "张管理员", "管理员"),
    ("OP004", "刘班长",   "班长"),
    ("OP005", "赵巡检",   "巡检员"),
    ("OP006", "孙安全",   "安全员"),
    ("OP007", "周经理",   "经理"),
    ("OP008", "吴值班",   "值班员"),
]

SAFETY_RULES = [
    ("R001", "禁烟管理规定",     "厂区内全面禁止吸烟，违者按规定处罚。", "v1.0", "smoking"),
    ("R002", "安全帽佩戴规定",   "进入工地必须佩戴安全帽并系紧帽带。",   "v1.0", "ppe"),
    ("R003", "工作期间通讯规定", "高危作业期间不得接打电话。",           "v1.0", "phone"),
    ("R004", "洁净区进入规定",   "洁净区内必须佩戴口罩与防护服。",       "v1.0", "ppe"),
    ("R005", "应急处置预案",     "明火/烟雾告警发生 5 分钟内必须到场。", "v1.0", "emergency"),
]


def init_db(db_path: Path):
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    return conn


def seed_areas(conn):
    rows = [(a, n, p, lv, NOW) for a, n, p, lv in AREAS]
    conn.executemany(
        "INSERT INTO areas(area_id,name,parent_area_id,level,created_at) VALUES (?,?,?,?,?)",
        rows,
    )


def seed_alarm_types(conn):
    rows = [(c, dn, pc, sv, ds) for c, dn, pc, sv, ds in ALARM_TYPES]
    conn.executemany(
        "INSERT INTO alarm_types(type_code,display_name,platform_code,severity_default,description) VALUES (?,?,?,?,?)",
        rows,
    )


def seed_operators(conn):
    rows = [(oid, n, r, f"ext-{oid[-3:]}", NOW) for oid, n, r in OPERATORS]
    conn.executemany(
        "INSERT INTO operators(operator_id,name,role,contact,created_at) VALUES (?,?,?,?,?)",
        rows,
    )


def seed_cameras(conn):
    workshop_areas = ["AREA02", "AREA03"]
    workstation_areas = ["AREA04", "AREA05"]
    cameras = []
    cam_idx = 1
    for area in workshop_areas:
        for _ in range(2):
            cid = f"CAM-{cam_idx:03d}"
            cameras.append((cid, f"{area}-{cam_idx}号摄像头", area,
                            f"rtsp://10.0.0.{cam_idx}/stream", "online", NOW))
            cam_idx += 1
    for area in workstation_areas:
        for _ in range(4):
            cid = f"CAM-{cam_idx:03d}"
            status = "online" if random.random() > 0.1 else "maintenance"
            cameras.append((cid, f"{area}-{cam_idx}号摄像头", area,
                            f"rtsp://10.0.0.{cam_idx}/stream", status, NOW))
            cam_idx += 1
    conn.executemany(
        "INSERT INTO cameras(camera_id,name,area_id,rtsp_url,status,created_at) VALUES (?,?,?,?,?,?)",
        cameras,
    )
    return [c[0] for c in cameras], {c[0]: c[2] for c in cameras}


def seed_persons(conn):
    persons = []
    surnames = ["王", "李", "张", "刘", "陈", "杨", "黄", "赵", "周", "吴"]
    given = ["磊", "强", "伟", "刚", "勇", "军", "平", "明", "辉", "斌"]
    for i in range(30):
        pid = f"P{i + 1:03d}"
        name = random.choice(surnames) + random.choice(given)
        persons.append((
            pid, name,
            random.choice(DEPARTMENTS),
            random.choice(PERSON_ROLES),
            f"https://ksipms.internal/photos/{pid}.jpg",
            "internal",
            NOW,
        ))
    conn.executemany(
        "INSERT INTO persons(person_id,name,department,role,photo_url,data_class,created_at) VALUES (?,?,?,?,?,?,?)",
        persons,
    )
    return [p[0] for p in persons]


def seed_alarms_and_videos(conn, camera_ids, cam_to_area, person_ids):
    type_codes = [t[0] for t in ALARM_TYPES]
    type_to_sev = {t[0]: t[3] for t in ALARM_TYPES}
    workstation_cams = [c for c in camera_ids if int(c.split("-")[1]) >= 5]

    alarms = []
    videos = []
    audit_rows = []
    operator_ids = [op[0] for op in OPERATORS]

    for d in range(DAYS_BACK):
        day_start = NOW - (DAYS_BACK - d) * DAY
        n_today = random.randint(30, 50)
        for _ in range(n_today):
            alarm_uuid = str(uuid.uuid4())
            atype = random.choice(type_codes)
            cam = random.choices(
                camera_ids,
                weights=[(3 if c in workstation_cams else 1) for c in camera_ids],
                k=1,
            )[0]
            area = cam_to_area[cam]
            person = random.choice(person_ids) if random.random() > 0.2 else None
            sev = type_to_sev[atype] + random.choice([-1, 0, 0, 1])
            sev = max(1, min(5, sev))
            ts_event = day_start + random.randint(8 * 3600, 18 * 3600)
            model_conf = round(random.uniform(0.55, 0.97), 3)

            has_video = random.random() < 0.30
            video_clip_id = None
            if has_video:
                video_clip_id = str(uuid.uuid4())
                videos.append((
                    video_clip_id, alarm_uuid, cam,
                    ts_event - 30, ts_event + 30,
                    f"/video/{cam}/{ts_event}.mp4", 60, NOW,
                ))

            is_processed = random.random() < 0.70
            if is_processed:
                status = random.choices(
                    ["closed", "false_alarm"], weights=[0.85, 0.15], k=1
                )[0]
                processed_at = ts_event + random.randint(60, 1800)
                processed_by = random.choice(operator_ids)
                processed_note = "现场已处置，复查无异常" if status == "closed" else "误报，已驳回"
                audit_rows.append((
                    alarm_uuid, "manual_close", processed_by,
                    json.dumps({"status": status, "note": processed_note}, ensure_ascii=False),
                    processed_at,
                ))
            else:
                status = random.choice(["pending", "acknowledged", "processing"])
                processed_at = None
                processed_by = None
                processed_note = None

            raw_payload = json.dumps({
                "platform_event_id": f"EV{random.randint(10000, 99999)}",
                "device_vendor": random.choice(["hikvision", "dahua", "uniview"]),
                "model_version": "yolov8s_v3.2",
            }, ensure_ascii=False)

            alarms.append((
                alarm_uuid, atype, cam, area, person,
                sev, status, ts_event,
                f"https://ksipms.internal/snap/{alarm_uuid}.jpg",
                video_clip_id, model_conf,
                f"{[t[1] for t in ALARM_TYPES if t[0] == atype][0]}告警 (置信度 {model_conf})",
                processed_at, processed_by, processed_note,
                raw_payload, NOW,
            ))

    conn.executemany("""
        INSERT INTO alarms(alarm_uuid,alarm_type,camera_id,area_id,person_id,
            severity,status,ts_event,snapshot_url,video_clip_id,model_conf,
            alarm_desc,processed_at,processed_by,processed_note,raw_payload,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, alarms)

    conn.executemany("""
        INSERT INTO video_clips(clip_id,alarm_id,camera_id,ts_start,ts_end,file_path,duration_sec,created_at)
        VALUES (?,?,?,?,?,?,?,?)
    """, videos)

    conn.executemany("""
        INSERT INTO audit_log(alarm_id,action,operator_id,payload,ts)
        VALUES (?,?,?,?,?)
    """, audit_rows)

    return len(alarms), len(videos), len(audit_rows)


def seed_safety_rules(conn):
    rows = [(rid, t, c, v, cat, NOW) for rid, t, c, v, cat in SAFETY_RULES]
    conn.executemany(
        "INSERT INTO safety_rules(rule_id,title,content,version,category,created_at) VALUES (?,?,?,?,?,?)",
        rows,
    )


def main():
    print(f"[seed] init {DB_PATH}")
    conn = init_db(DB_PATH)
    try:
        seed_areas(conn)
        seed_alarm_types(conn)
        seed_operators(conn)
        cam_ids, cam_to_area = seed_cameras(conn)
        person_ids = seed_persons(conn)
        n_alarm, n_video, n_audit = seed_alarms_and_videos(conn, cam_ids, cam_to_area, person_ids)
        seed_safety_rules(conn)
        conn.commit()
        print(f"[seed] areas=5 cameras={len(cam_ids)} persons={len(person_ids)}")
        print(f"[seed] alarms={n_alarm} videos={n_video} audit_init={n_audit}")
        print(f"[seed] DONE -> {DB_PATH}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
