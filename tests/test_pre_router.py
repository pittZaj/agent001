"""预路由单元测试：仅闲聊/规章走快路径，业务问题统一交 Planner。"""
import importlib.util
from pathlib import Path

# 直接加载 pre_router.py，避免 graph/__init__ 拉取 LangGraph 依赖
_spec = importlib.util.spec_from_file_location(
    "pre_router",
    Path(__file__).resolve().parents[1] / "graph" / "pre_router.py",
)
pre_router = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pre_router)


def _route(msg: str):
    r = pre_router.pre_route(msg)
    if r is None:
        return None
    return r["plan"][0]["task"], r["plan"][0].get("args", {})


def test_video_queries_go_to_planner():
    """录像/告警类问题不再走正则快路径，统一交 Planner。"""
    for msg in (
        "查看公司大门口主码流的录像情况",
        "查看公司大门口录像",
        "查看录像任务",
        "查看今天AI告警情况",
        "统计今天的告警",
        "查看录像计划列表",
    ):
        assert _route(msg) is None, msg


def test_chitchat_still_works():
    task, _ = _route("你好")
    assert task == "direct_response"


def test_kb_still_works():
    task, args = _route("吸烟会被怎么处罚")
    assert task == "kb_regulation"
    assert "吸烟" in args["query"]


if __name__ == "__main__":
    test_video_queries_go_to_planner()
    test_chitchat_still_works()
    test_kb_still_works()
    print("all pre_router tests passed")
