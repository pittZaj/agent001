"""Agent 注册表与发布管理。

注册表 = `agent/agent/registry/agent_registry.json`，单一事实源。
发布命令把 `artifacts/<job_id>/agent_code.py` 拷到 `artifacts/published/<name>_v<v>.py` 并写入注册表。
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent
REGISTRY_PATH = PROJECT_ROOT / "registry" / "agent_registry.json"
PUBLISHED_DIR = PROJECT_ROOT / "artifacts" / "published"


def _load() -> dict:
    if not REGISTRY_PATH.exists():
        REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        REGISTRY_PATH.write_text("{}", encoding="utf-8")
        return {}
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def _save(data: dict) -> None:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def list_agents() -> dict:
    return _load()


def publish(job_id: str, *, force: bool = False) -> dict:
    """把 artifacts/<job_id>/ 中的 agent 发布到注册表。"""
    job_dir = PROJECT_ROOT / "artifacts" / job_id
    register_path = job_dir / "REGISTER.json"
    code_path = job_dir / "agent_code.py"
    if not register_path.exists() or not code_path.exists():
        raise FileNotFoundError(f"job not found or incomplete: {job_dir}")

    info = json.loads(register_path.read_text(encoding="utf-8"))
    if not info.get("passed_acceptance") and not force:
        raise ValueError(
            f"job {job_id} 未通过验收（score={info.get('score')}），如要强制发布请加 --force"
        )

    name = info["agent_name"]
    version = info.get("version", "0.1.0")
    safe_v = version.replace(".", "_")
    PUBLISHED_DIR.mkdir(parents=True, exist_ok=True)
    target_file = PUBLISHED_DIR / f"{name}_v{safe_v}.py"
    shutil.copyfile(code_path, target_file)

    registry = _load()
    registry[name] = {
        "version": version,
        "published_path": str(target_file),
        "module_name": f"{name}_v{safe_v}",
        "route": f"/agents/{name}/chat",
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "metrics": info.get("metrics", {}),
        "score": info.get("score"),
        "source_job_id": job_id,
        "data_source": info.get("data_source"),
    }
    _save(registry)
    return registry[name]


def unpublish(name: str) -> bool:
    registry = _load()
    if name not in registry:
        return False
    registry.pop(name)
    _save(registry)
    return True


def load_agent_run(name: str):
    """加载已发布 agent 的 run 函数。"""
    registry = _load()
    if name not in registry:
        raise KeyError(name)
    info = registry[name]
    file_path = Path(info["published_path"])
    module_name = info["module_name"]
    if not file_path.exists():
        raise FileNotFoundError(file_path)

    if str(PUBLISHED_DIR) not in sys.path:
        sys.path.insert(0, str(PUBLISHED_DIR))
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {file_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod.run
