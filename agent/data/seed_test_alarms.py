"""
将测试告警图片导入数据库。

从 /mnt/ksnas/AI_Dataset/V5-test/test-agent/ 读取 8 类告警图片，
每张图生成一条 alarms 记录，status=pending，便于后续复判测试。

运行方式：
    cd /mnt/data3/clip/LangGraph/agent
    python agent/data/seed_test_alarms.py
"""
import json
import random
import sqlite3
import time
import uuid
from pathlib import Path

# 配置
TEST_IMAGE_ROOT = Path("/mnt/ksnas/AI_Dataset/V5-test/test-agent")
DB_PATH = Path(__file__).parent / "ksipms_dev.db"
NOW = int(time.time())

random.seed(42)  # 可重现

def get_cameras_and_types(conn):
    """从数据库读取可用摄像头和告警类型"""
    cursor = conn.cursor()
    cursor.execute("SELECT camera_id, area_id FROM cameras WHERE status='online'")
    cameras = cursor.fetchall()

    cursor.execute("SELECT type_code, display_name, severity_default FROM alarm_types")
    alarm_types = {row[0]: {"display_name": row[1], "severity": row[2]} for row in cursor.fetchall()}

    return cameras, alarm_types


def seed_test_alarms():
    """遍历测试图片目录，为每张图生成一条 alarms 记录"""
    if not TEST_IMAGE_ROOT.exists():
        print(f"❌ 测试图片目录不存在: {TEST_IMAGE_ROOT}")
        return 0

    conn = sqlite3.connect(DB_PATH)
    cameras, alarm_types = get_cameras_and_types(conn)

    if not cameras:
        print("❌ 数据库中无可用摄像头")
        conn.close()
        return 0

    # 幂等：先清理上一次导入的测试数据（避免重复累积）
    deleted = conn.execute(
        "DELETE FROM alarms WHERE alarm_desc LIKE '%测试数据%'"
    ).rowcount
    if deleted:
        print(f"[seed_test_alarms] 清理旧测试数据 {deleted} 条")

    inserted = 0
    for type_folder in TEST_IMAGE_ROOT.iterdir():
        if not type_folder.is_dir():
            continue

        type_code = type_folder.name
        if type_code not in alarm_types:
            print(f"⚠️  跳过未知类型: {type_code}")
            continue

        type_info = alarm_types[type_code]
        images = list(type_folder.glob("*.png")) + list(type_folder.glob("*.jpg"))

        for img_path in images:
            alarm_uuid = str(uuid.uuid4())
            camera_id, area_id = random.choice(cameras)
            ts_event = NOW - random.randint(0, 48 * 3600)  # 最近2天
            model_conf = round(random.uniform(0.75, 0.95), 3)

            raw_payload = json.dumps({
                "source": "test_dataset",
                "file": img_path.name,
                "imported_at": NOW
            }, ensure_ascii=False)

            conn.execute("""
                INSERT INTO alarms(
                    alarm_uuid, alarm_type, camera_id, area_id, person_id,
                    severity, status, ts_event, snapshot_url, video_clip_id,
                    model_conf, alarm_desc, processed_at, processed_by,
                    processed_note, raw_payload, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                alarm_uuid, type_code, camera_id, area_id, None,
                type_info["severity"], "pending", ts_event, str(img_path.resolve()), None,
                model_conf, f"{type_info['display_name']}告警 (测试数据)", None, None,
                None, raw_payload, NOW
            ))
            inserted += 1

    conn.commit()
    conn.close()
    return inserted


if __name__ == "__main__":
    print(f"[seed_test_alarms] 开始导入测试图片...")
    n = seed_test_alarms()
    print(f"[seed_test_alarms] ✅ 已导入 {n} 条测试告警记录")
    print(f"[seed_test_alarms] 验证: sqlite3 {DB_PATH} \"SELECT alarm_type, COUNT(*) FROM alarms WHERE alarm_desc LIKE '%测试数据%' GROUP BY alarm_type\"")
