"""
端到端冒烟测试
启动 main.py 后运行此脚本验证
"""
import requests
import base64
import time
from pathlib import Path

BASE_URL = "http://127.0.0.1:8000"


def test_health():
    """健康检查"""
    print("=" * 60)
    print("Test 1: GET /health")
    print("=" * 60)
    r = requests.get(f"{BASE_URL}/health")
    print(f"Status: {r.status_code}")
    print(f"Response: {r.json()}")
    assert r.status_code == 200
    print("✅ PASS\n")


def test_chat():
    """文本对话"""
    print("=" * 60)
    print("Test 2: POST /api/v1/chat")
    print("=" * 60)
    r = requests.post(
        f"{BASE_URL}/api/v1/chat",
        json={
            "session_id": "test_session_001",
            "message": "今天发生了哪几种告警事件？",
        },
        timeout=60,
    )
    print(f"Status: {r.status_code}")
    print(f"Response: {r.json()}")
    assert r.status_code == 200
    data = r.json()
    assert "response" in data
    print("✅ PASS\n")


def test_judge():
    """多模态告警复判（需要测试图片）"""
    print("=" * 60)
    print("Test 3: POST /api/v1/judge")
    print("=" * 60)

    # 用 VLLM 目录下的测试图片
    test_img = Path("/mnt/data3/clip/LangGraph/VLLM/test_image.jpg")
    if not test_img.exists():
        print("⚠️  跳过：未找到测试图片")
        return

    with open(test_img, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    r = requests.post(
        f"{BASE_URL}/api/v1/judge",
        json={
            "image_base64": b64,
            "yolo_result": {"class": "smoking", "confidence": 0.91},
        },
        timeout=60,
    )
    print(f"Status: {r.status_code}")
    print(f"Response: {r.json()}")
    assert r.status_code == 200
    data = r.json()
    assert "verdict" in data
    assert all(k in data["verdict"] for k in ["smoking", "helmet", "phone", "mask"])
    print("✅ PASS\n")


if __name__ == "__main__":
    print("\n🧪 KSAgent 端到端冒烟测试\n")
    try:
        test_health()
        test_chat()
        test_judge()
        print("=" * 60)
        print("🎉 所有测试通过")
        print("=" * 60)
    except AssertionError as e:
        print(f"❌ 断言失败: {e}")
    except requests.exceptions.ConnectionError:
        print(f"❌ 无法连接 {BASE_URL}，请先启动 main.py")
    except Exception as e:
        print(f"❌ 测试失败: {e}")
