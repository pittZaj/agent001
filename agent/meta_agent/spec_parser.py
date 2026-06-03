"""把 markdown spec 解析为 task dict，可双向（dict ⇄ markdown）。

Spec 的章节是固定 9 段（见 templates/AGENT_SPEC_TEMPLATE.md）。
解析容错：缺段返回默认值，列表/表格按行抽取。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any


SECTION_ORDER = [
    "metadata",
    "business_goal",
    "user_scenarios",
    "tools",
    "data_access",
    "knowledge_base",
    "test_cases",
    "acceptance",
    "token_budget",
]


@dataclass
class AgentSpec:
    name: str
    description: str
    version: str = "0.1.0"
    owner: str = ""
    user_scenarios: list[str] = field(default_factory=list)
    tools: list[dict] = field(default_factory=list)
    data_access: dict = field(default_factory=dict)
    knowledge_base: str = ""
    test_cases: list[dict] = field(default_factory=list)
    acceptance: dict = field(default_factory=lambda: {
        "tool_accuracy": 0.8,
        "execution_success": 0.9,
        "overall_score": 0.7,
    })
    token_budget: dict = field(default_factory=lambda: {
        "max_iterations": 1,
        "max_input_tokens": 50000,
        "max_output_tokens": 20000,
    })
    raw_md: str = ""

    def asdict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "owner": self.owner,
            "user_scenarios": self.user_scenarios,
            "tools": self.tools,
            "data_access": self.data_access,
            "knowledge_base": self.knowledge_base,
            "test_cases": self.test_cases,
            "acceptance": self.acceptance,
            "token_budget": self.token_budget,
        }


def parse_spec_file(path: str | Path) -> AgentSpec:
    text = Path(path).read_text(encoding="utf-8")
    return parse_spec_text(text)


def parse_spec_text(text: str) -> AgentSpec:
    sections = _split_sections(text)
    md = sections.get("metadata", "")
    name = _extract_kv(md, "name") or "unknown_agent"
    version = _extract_kv(md, "version") or "0.1.0"
    owner = _extract_kv(md, "owner") or ""

    description = _strip_section_body(sections.get("business_goal", "")).strip()
    user_scenarios = _extract_bullets(sections.get("user_scenarios", ""))

    tools = _parse_tools_table(sections.get("tools", ""))
    data_access = _parse_data_access(sections.get("data_access", ""))
    knowledge_base = _strip_section_body(sections.get("knowledge_base", "")).strip()

    test_cases = _parse_test_cases_table(sections.get("test_cases", ""))
    acceptance = _parse_acceptance(sections.get("acceptance", ""))
    token_budget = _parse_token_budget(sections.get("token_budget", ""))

    return AgentSpec(
        name=name,
        description=description,
        version=version,
        owner=owner,
        user_scenarios=user_scenarios,
        tools=tools,
        data_access=data_access,
        knowledge_base=knowledge_base,
        test_cases=test_cases,
        acceptance=acceptance,
        token_budget=token_budget,
        raw_md=text,
    )


# ---------------- internal helpers ----------------

_SECTION_KEYWORDS = [
    ("metadata",       ("元数据", "metadata")),
    ("business_goal",  ("业务目标", "business goal")),
    ("user_scenarios", ("用户场景", "user scenarios")),
    ("tools",          ("可用工具", "tools")),
    ("data_access",    ("数据访问", "data access")),
    ("knowledge_base", ("知识库", "knowledge base")),
    ("test_cases",     ("测试用例", "test cases")),
    ("acceptance",     ("验收指标", "acceptance")),
    ("token_budget",   ("token 预算", "token budget", "预算")),
]


def _split_sections(text: str) -> dict[str, str]:
    """按 `## <数字>.` 行切段，识别中文/英文标题关键字。"""
    pattern = re.compile(r"^##\s+\d+\.\s*(.+)$", re.MULTILINE)
    sections: dict[str, str] = {}
    matches = list(pattern.finditer(text))
    for i, m in enumerate(matches):
        title = m.group(1).strip().lower()
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end]
        key = _match_section_key(title)
        if key:
            sections[key] = body
    return sections


def _match_section_key(title: str) -> str | None:
    for key, keywords in _SECTION_KEYWORDS:
        for kw in keywords:
            if kw in title:
                return key
    return None


def _strip_section_body(body: str) -> str:
    """去掉首行里残留的（必填）等括注，保留正文。"""
    return re.sub(r"^[（(].*?[)）]\s*", "", body.strip())


def _extract_kv(body: str, key: str) -> str | None:
    """从 `- key: value` 行抽值。"""
    pat = re.compile(rf"^[-*]\s*{re.escape(key)}\s*[:：]\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)
    m = pat.search(body)
    if not m:
        return None
    val = m.group(1).strip()
    val = re.sub(r"^[<《]|[>》]$", "", val).strip()
    if val.startswith("<") and val.endswith(">"):
        return None
    return val


def _extract_bullets(body: str) -> list[str]:
    out = []
    for line in body.splitlines():
        line = line.rstrip()
        m = re.match(r"^\s*[-*]\s+(.+)$", line)
        if m:
            out.append(m.group(1).strip())
    return out


def _parse_table(body: str) -> list[dict[str, str]]:
    """Markdown 表格 → list of dict (键为表头小写)。"""
    rows = []
    lines = [l for l in body.splitlines() if l.strip().startswith("|")]
    if len(lines) < 2:
        return rows
    headers = [h.strip().lower() for h in lines[0].strip("|").split("|")]
    # 第二行是分隔行，跳过
    for line in lines[2:]:
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) != len(headers):
            continue
        if all(set(c) <= {"-", " "} for c in cells):
            continue
        rows.append(dict(zip(headers, cells)))
    return rows


def _parse_tools_table(body: str) -> list[dict]:
    tools = []
    for row in _parse_table(body):
        params_raw = row.get("parameters", "")
        params = {}
        for p in re.split(r"[,，;；]\s*", params_raw):
            p = p.strip()
            if not p:
                continue
            if ":" in p:
                k, v = p.split(":", 1)
                params[k.strip()] = v.strip()
            else:
                params[p] = ""
        tools.append({
            "name": row.get("name", ""),
            "description": row.get("description", ""),
            "parameters": params,
            "data_source": row.get("data_source", ""),
        })
    return [t for t in tools if t["name"]]


def _parse_data_access(body: str) -> dict:
    out = {}
    for line in body.splitlines():
        m = re.match(r"^\s*[-*]\s*(.+?)\s*[:：]\s*(.+)$", line)
        if m:
            out[m.group(1).strip()] = m.group(2).strip()
    return out


def _parse_test_cases_table(body: str) -> list[dict]:
    cases = []
    for row in _parse_table(body):
        case = {
            "input": row.get("input", "").strip().strip('"'),
            "expected_tool": row.get("expected_tool", "").strip(),
        }
        ea = row.get("expected_args_contains", "").strip()
        if ea and ea not in ("-", "—"):
            try:
                case["expected_args_contains"] = json.loads(ea)
            except json.JSONDecodeError:
                case["expected_args_contains"] = {"_raw": ea}
        eo = row.get("expected_output_contains", "").strip()
        if eo and eo not in ("-", "—"):
            case["expected_output_contains"] = eo
        if case["input"]:
            cases.append(case)
    return cases


def _parse_acceptance(body: str) -> dict:
    out = {}
    for line in body.splitlines():
        m = re.match(r"^\s*[-*]\s*([a-z_]+)\s*>?=?\s*([0-9.]+)\s*$", line)
        if m:
            out[m.group(1)] = float(m.group(2))
    return out


def _parse_token_budget(body: str) -> dict:
    out = {}
    for line in body.splitlines():
        m = re.match(r"^\s*[-*]\s*([a-z_]+)\s*[:：]\s*([0-9]+)\s*$", line)
        if m:
            out[m.group(1)] = int(m.group(2))
    return out


if __name__ == "__main__":
    import sys
    spec = parse_spec_file(sys.argv[1])
    print(json.dumps(spec.asdict(), ensure_ascii=False, indent=2))
