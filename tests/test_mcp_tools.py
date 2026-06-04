"""测试 MCP Server 工具实现（不通过 stdio）"""
import sys
from pathlib import Path
from datetime import datetime

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from mcp_servers.ksipms_server import query_alarms_impl, query_person_impl

print("=" * 60)
print("测试 MCP Server 工具实现")
print("=" * 60)

# 测试 query_alarms
print("\n[测试 1] query_alarms - 查询今天的告警")
today = datetime.utcnow().strftime("%Y-%m-%d")
result = query_alarms_impl(date=today)
print(f"结果: {result}")

print("\n[测试 2] query_alarms - 查询所有 smoking 告警")
result = query_alarms_impl(alarm_type="smoking")
print(f"结果: {result}")

print("\n[测试 3] query_person - 查询人员信息")
result = query_person_impl(person_id="P001")
print(f"结果: {result}")

print("\n=" * 60)
print("✅ MCP Server 工具实现测试完成")
print("=" * 60)
