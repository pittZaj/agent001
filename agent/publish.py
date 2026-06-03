"""把 artifacts/<job_id>/ 发布为生产 Agent。

CLI:
  python -m agent.publish <job_id>           # 验收通过才发布
  python -m agent.publish <job_id> --force   # 强制发布
  python -m agent.publish --list             # 列出所有已发布
  python -m agent.publish --remove <name>    # 撤销发布
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from registry import list_agents, publish, unpublish


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("job_id", nargs="?")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--remove", default=None)
    args = ap.parse_args()

    if args.list:
        print(json.dumps(list_agents(), ensure_ascii=False, indent=2))
        return
    if args.remove:
        ok = unpublish(args.remove)
        print(f"unpublished={ok}")
        return
    if not args.job_id:
        ap.error("job_id required (or use --list / --remove)")
    info = publish(args.job_id, force=args.force)
    print(json.dumps(info, ensure_ascii=False, indent=2))
    print("\n✅ 发布成功。请重启 agent/main.py FastAPI 以挂载新路由 (端口 8000)。")


if __name__ == "__main__":
    main()
