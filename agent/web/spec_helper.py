"""为 Web 表单提供"我能填什么"的真实候选项。

读取项目当前实际状态：
  - 工具：来自 meta_agent.tool_impl.TOOL_REGISTRY（自描述）
  - 数据库表 + 字段：从 data/ksipms_dev.db 反射
  - 告警类型字典 / 摄像头列表：从 SQLite 取实际值
  - 已发布 Agent：从 registry 读
"""
from __future__ import annotations

import inspect
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from meta_agent.tool_impl import TOOL_REGISTRY  # noqa: E402

DB_PATH = PROJECT_ROOT / "data" / "ksipms_dev.db"


def list_tools() -> list[dict[str, Any]]:
    """枚举 TOOL_REGISTRY 中所有工具的签名 + docstring。"""
    out = []
    for name, fn in TOOL_REGISTRY.items():
        sig = inspect.signature(fn)
        params = []
        for p_name, p in sig.parameters.items():
            if p_name.startswith("_"):
                continue
            ann = "str"
            if p.annotation is not inspect.Parameter.empty:
                ann = getattr(p.annotation, "__name__", str(p.annotation))
            params.append({
                "name": p_name,
                "type": ann,
                "required": p.default is inspect.Parameter.empty,
                "default": None if p.default is inspect.Parameter.empty else p.default,
            })
        out.append({
            "name": name,
            "doc": (fn.__doc__ or "").strip().splitlines()[0] if fn.__doc__ else "",
            "parameters": params,
            "data_source": "sqlite:data/ksipms_dev.db",
        })
    return out


def list_tables() -> list[str]:
    if not DB_PATH.exists():
        return []
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        return [r[0] for r in rows if not r[0].startswith("sqlite_")]
    finally:
        conn.close()


def describe_table(table: str) -> list[dict[str, Any]]:
    """表结构 + 是否主键 + 类型。"""
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        rows = conn.execute(f"PRAGMA table_info({_safe_ident(table)})").fetchall()
    finally:
        conn.close()
    return [
        {"name": r[1], "type": r[2], "notnull": bool(r[3]),
         "default": r[4], "pk": bool(r[5])}
        for r in rows
    ]


def alarm_type_choices() -> list[dict[str, str]]:
    """从 alarm_types 表实际取，避免 spec 写错值。"""
    if not DB_PATH.exists():
        return []
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT type_code, display_name FROM alarm_types ORDER BY type_code"
        ).fetchall()
    finally:
        conn.close()
    return [{"code": r[0], "display": r[1]} for r in rows]


def camera_choices(limit: int = 30) -> list[str]:
    if not DB_PATH.exists():
        return []
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT camera_id FROM cameras ORDER BY camera_id LIMIT ?", (limit,)
        ).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


def _safe_ident(name: str) -> str:
    """只允许 [A-Za-z0-9_] 标识符，防 SQL 注入。"""
    if not name.replace("_", "").isalnum():
        raise ValueError(f"unsafe identifier: {name!r}")
    return name


def render_help_markdown() -> str:
    """渲染给 Web 表单用的"如何填写"指南，基于真实工具/表/枚举。"""
    tools = list_tools()
    types = alarm_type_choices()
    cams = camera_choices(8)
    tbls = list_tables()

    lines = ["## 怎么填写 Spec？三句话规则", ""]
    lines.append("1. **工具表格** 只能从下面已实现工具里选；写 `name | description | parameters | data_source`。")
    lines.append("2. **数据访问** 数据库填 `sqlite`，路径 `data/ksipms_dev.db`，表名从下面表清单中选。")
    lines.append("3. **测试用例** input 必须能映射到一个工具，参数值用真实枚举（见 alarm_types / cameras）。")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("### 已实现工具（可在工具表格中引用）")
    lines.append("")
    for t in tools:
        params_str = ", ".join(
            f"{p['name']}({p['type']}{'*' if p['required'] else ''})" for p in t["parameters"]
        ) or "-"
        lines.append(f"- **`{t['name']}`** — {t['doc']}")
        lines.append(f"  - 参数: `{params_str}`  *(标 `*` 为必填)*")
        lines.append(f"  - data_source: `{t['data_source']}`")
    lines.append("")
    lines.append("### 数据库表清单（可在 §5 数据访问 / §4 工具的 data_source 中引用）")
    lines.append("")
    lines.append(f"`{', '.join(tbls)}`")
    lines.append("")
    lines.append("### 告警类型枚举（可在测试用例 expected_args_contains 中使用）")
    lines.append("")
    for at in types:
        lines.append(f"- `{at['code']}` — {at['display']}")
    lines.append("")
    lines.append("### 摄像头 ID 示例（前 8 个）")
    lines.append("")
    lines.append(f"`{', '.join(cams)}` ...")
    lines.append("")
    lines.append("### 写测试用例的 4 条原则")
    lines.append("")
    lines.append("1. **覆盖典型场景**：每个工具至少 1 条")
    lines.append("2. **覆盖参数提取边界**：日期相对(今天/昨天)、绝对(2026-06-01)、别名(抽烟->smoking)")
    lines.append("3. **覆盖否定意图**：用户要的不是工具能做的，期望 plan 为空或合理拒答")
    lines.append("4. **`<TODAY>` 占位**：评估时会替换为运行当天 UTC 日期，避免硬编码")
    return "\n".join(lines)


def example_tools_table_row() -> str:
    """给表单的占位文本（已转义类型注解里的 `|` 为 `/`，避免冲突 markdown 列分隔符）。"""
    rows = []
    for t in list_tools():
        params_str = ", ".join(
            f"{p['name']}:{p['type'].replace('|', '/')}" for p in t["parameters"]
        )
        rows.append(f"| {t['name']} | {t['doc']} | {params_str} | {t['data_source']} |")
    return "\n".join(rows)


def example_test_cases() -> str:
    types = alarm_type_choices()
    common = next((t["code"] for t in types if t["code"] == "smoking"), "smoking")
    helmet = next((t["code"] for t in types if t["code"] == "no_helmet"), "no_helmet")
    cams = camera_choices(1)
    cam = cams[0] if cams else "CAM-001"
    return "\n".join([
        '| 今天发生了哪些告警？ | query_alarms | {"date":"<TODAY>"} | 告警 |',
        f'| 查询昨天的抽烟告警 | query_alarms | {{"alarm_type":"{common}"}} | {common} |',
        f'| 最近未戴安全帽的告警 | query_alarms | {{"alarm_type":"{helmet}"}} | helmet |',
        f'| {cam} 的告警 | query_alarms | {{"camera_id":"{cam}"}} | {cam} |',
    ])


if __name__ == "__main__":
    print(render_help_markdown())
