"""Harness 护栏模块（反馈控制工程实现）

统一的行动前后校验入口，对应《从 Harness 架构到 Token 经济学的探索》中的 PreToolUse / PostToolUse Hooks。

核心设计：
  - GuardResult 统一返回结构（allow / action / reason / fixed_plan）
  - check_plan：行动前 plan 硬校验（H6）
  - needs_confirmation：危险回写确认门（H7）
  - check_result：行动后统一核对（H8）

职责边界：
  - 只对**明确非法**的 deny/ask（工具不存在、编码非法、危险回写无确认）
  - 对**可修正**的走 fixed_plan 自动纠偏（参数越界）
  - 对**存疑**的一律放行（避免过度拦截，负反馈增益 < 1）
"""
from dataclasses import dataclass
from typing import Any, Optional
from loguru import logger


# ==================== 数据结构 ====================

@dataclass
class GuardResult:
    """护栏函数统一返回结构

    字段：
      - allow: bool，是否放行（默认 True）
      - action: str，护栏动作（"allow" / "deny" / "ask"）
      - reason: str，拦截原因（仅在 deny/ask 时有值）
      - fixed_plan: Optional[list]，自动修正后的 plan（仅 check_plan 使用）

    设计说明：
      - allow 是最核心的判定（True=放行，False=拦截）
      - action 细化拦截类型："deny"直接拦截，"ask"需要确认（H7 最小实现时等价 deny）
      - fixed_plan 用于参数越界自动修正（如 pagesize 过大修正为 100）
    """
    allow: bool = True
    action: str = "allow"  # "allow" / "deny" / "ask"
    reason: str = ""
    fixed_plan: Optional[list] = None


# ==================== H6：plan 行动前硬校验 ====================

def check_plan(plan: list[dict], registry: Any) -> GuardResult:
    """行动前 plan 硬校验（H6 实现，PreToolUse Hooks）

    校验维度：
      ① 工具存在性：引用未注册工具 → deny（友好话术，不进 executor）
      ② event_type 合法性：带了非权威编码 → deny（防幻觉最后一道，调 event_types.is_supported）
      ③ 参数越界：pagesize 异常大 → 自动修正（fixed_plan）而非 deny

    设计取舍：
      - 只对**明确非法**的 plan deny（工具不存在、编码非法）
      - 对**可修正**的（pagesize 越界）走 fixed_plan 自动纠偏
      - 对**存疑**的一律放行（避免误伤，宁可让 executor 兜底）

    参数：
      - plan: planner_node 产出的计划（list of {"task": str, "args": dict, "status": str}）
      - registry: Skill Registry 实例（用于校验工具存在性）

    返回：
      - GuardResult(allow=True): 放行，plan 合法
      - GuardResult(allow=False, action="deny", reason=...): 拦截，返回原因
      - GuardResult(allow=True, fixed_plan=[...]): 自动修正，用修正后的 plan

    H6 实现：三类校验（工具存在性/event_type合法性/参数越界修正）
    """
    # 导入依赖（延迟导入避免循环依赖）
    from skills.event_types import is_supported as is_event_type_supported

    # 收集所有有效工具 ID（MCP工具 + 本地工具 + 子图 + 特殊工具）
    all_skills = registry.list_skills()
    valid_tool_ids = {s.id for s in all_skills}
    # 特殊工具：direct_response / stream_chat（不在 registry 但合法）
    valid_tool_ids.add("direct_response")
    valid_tool_ids.add("stream_chat")

    # 环境检测：如果 registry 为空（测试/评估环境，MCP 未连接），跳过工具存在性校验
    # 这是"存疑放行"原则的体现——不确定工具列表是否完整时，宁可放行让 executor 兜底
    skip_tool_check = len(all_skills) == 0
    if skip_tool_check:
        logger.debug(
            "[HARNESS-GUARD] check_plan: registry 为空（测试/评估环境），"
            "跳过工具存在性校验，仅校验 event_type 和参数"
        )

    # 是否需要自动修正
    need_fix = False
    fixed_plan = []

    # 逐步骤校验
    for i, step in enumerate(plan):
        tool_name = step.get("task", "")
        args = step.get("args", {})

        # ① 工具存在性校验：引用未注册工具 → deny（生产环境有效，测试环境跳过）
        if not skip_tool_check and tool_name not in valid_tool_ids:
            return GuardResult(
                allow=False,
                action="deny",
                reason=(
                    f"计划第 {i+1} 步引用了不存在的工具「{tool_name}」。\n\n"
                    f"请检查工具名称是否正确，或联系管理员确认该工具是否已注册。"
                )
            )

        # ② event_type 合法性校验：带了非权威编码 → deny（防幻觉最后一道）
        event_type = args.get("event_type")
        if event_type and not is_event_type_supported(event_type):
            return GuardResult(
                allow=False,
                action="deny",
                reason=(
                    f"计划第 {i+1} 步使用了无效的告警类型编码「{event_type}」。\n\n"
                    f"该编码不在平台权威清单中。请使用平台支持的告警类型，"
                    f"或用自然语言描述（如「未戴安全帽」），由系统自动匹配。"
                )
            )

        # ③ 参数越界自动修正：pagesize 异常大 → fixed_plan（而非 deny）
        # 阈值设定：pagesize > 1000 视为异常（正常查询不会超过这个值）
        pagesize = args.get("pagesize")
        if pagesize is not None and isinstance(pagesize, (int, float)) and pagesize > 1000:
            # 自动修正为 100（T6 排序兜底的安全阈值）
            need_fix = True
            fixed_step = step.copy()
            fixed_step["args"] = args.copy()
            fixed_step["args"]["pagesize"] = 100
            fixed_plan.append(fixed_step)
            logger.info(
                f"[HARNESS-GUARD] check_plan: 第 {i+1} 步 pagesize={pagesize} 越界，"
                f"自动修正为 100（防止无意义全量拉取）"
            )
        else:
            # 无需修正，原样保留
            fixed_plan.append(step)

    # 返回结果
    if need_fix:
        # 有参数越界，返回修正后的 plan
        return GuardResult(allow=True, fixed_plan=fixed_plan)
    else:
        # 全部合法，放行
        return GuardResult(allow=True)


# ==================== H7：危险回写确认门 ====================

def needs_confirmation(task: dict, config: dict) -> GuardResult:
    """危险回写确认门（H7 实现，PreToolUse Hooks）

    职责：
      对会修改生产数据的高风险工具（如 update_alarm_status / ai_event_deal）
      进行二次确认，防止误写不可逆。

    确认逻辑：
      - 复判链路自动化（带 source="vlm_judge"）→ 放行 + 强制留痕
      - 用户直接指令回写（无 VLM 依据、无确认标志）→ deny（最小实现，交互式 interrupt 列待办）

    参数：
      - task: 当前待执行的任务（{"task": str, "args": dict, "status": str}）
      - config: 完整配置（读取 harness.confirm_dangerous_writes / dangerous_tools）

    返回：
      - GuardResult(allow=True): 放行（复判链路 / 已确认）
      - GuardResult(allow=False, action="ask", reason=...): 需要确认（H7 最小实现时等价 deny）

    H7 实现：复判链路自动化放行 + 用户直接回写拦截 + 强制留痕
    """
    # 读取配置：是否启用确认门 + 危险工具列表
    harness_config = config.get("harness", {})
    if not harness_config.get("confirm_dangerous_writes", True):
        # 确认门开关关闭，直接放行（保持现网行为）
        return GuardResult(allow=True)

    dangerous_tools = set(harness_config.get("dangerous_tools", []))
    tool_name = task.get("task", "")

    # 非危险工具，直接放行
    if tool_name not in dangerous_tools:
        return GuardResult(allow=True)

    # 危险工具：需进一步判断场景
    args = task.get("args", {})

    # 场景 1：复判链路自动化（带 source="vlm_judge"）
    # Demo 2：vlm_judge → update_alarm_status，同一 plan 内 VLM 已给出 verdict+confidence
    # 这是设计内的自动化流程，放行但强制留痕（审计日志在 executor 侧补充）
    if args.get("source") == "vlm_judge":
        logger.info(
            f"[HARNESS-GUARD] needs_confirmation: 复判链路自动化 {tool_name}，"
            f"带 source=vlm_judge，放行并留痕"
        )
        return GuardResult(allow=True)

    # 场景 2：用户直接指令回写（无 VLM 依据）
    # 本轮最小实现：要求显式确认标志 confirmed_by_user=true
    # 未来可扩展为交互式 interrupt（LangGraph interrupt + Web 层回显确认按钮）
    if not args.get("confirmed_by_user"):
        # 构造友好话术：精确说明为什么拦截 + 如何绕过
        alarm_uuid = args.get("alarm_uuid") or args.get("event_uuid") or "未知"
        verdict = args.get("verdict") or "未知"
        review_status = args.get("review_status") or "未知"

        return GuardResult(
            allow=False,
            action="ask",
            reason=(
                f"⚠️ 安全护栏：即将执行危险回写操作\n\n"
                f"工具：{tool_name}\n"
                f"告警 UUID：{alarm_uuid}\n"
                f"verdict：{verdict}\n"
                f"review_status：{review_status}\n\n"
                f"该操作会修改生产数据库的告警状态，不可逆。\n\n"
                f"**建议**：\n"
                f"1. 在复判链路中执行（先用 vlm_judge_alarm 复判，再自动回写）\n"
                f"2. 如需直接回写，请确认操作无误后重试\n\n"
                f"**注**：真正的交互式确认需 Web 层配合（待实施）。"
            )
        )

    # 场景 3：带显式确认标志，放行
    logger.info(
        f"[HARNESS-GUARD] needs_confirmation: 危险回写 {tool_name}，"
        f"带 confirmed_by_user=true，放行并留痕"
    )
    return GuardResult(allow=True)


# ==================== H8：行动后统一核对 ====================

def check_result(tool: str, result: dict, config: dict) -> GuardResult:
    """行动后结果核对（H8 实现，PostToolUse Hooks）

    职责：
      收敛现有分散的后置核对逻辑（空结果守卫 / isError 处理），
      作为统一入口，降低"新增工具漏挂核对"风险。

    核对维度：
      - 空结果守卫：查询类工具返回 total=0 / events=[] → 确定性分支（如实告知无数据，不进 LLM）
      - isError 识别：MCP 返回 {"error": ...} → 错误分支
      - 其他异常情况收敛

    设计：
      H8 是"收敛 + 文档化约定"，不推翻现有守卫（现有守卫已验证有效）。
      check_result 作为统一门面（Facade），内部调用现有各守卫逻辑。

    参数：
      - tool: 工具名称（如 "ai_event_list"）
      - result: 工具返回结果（dict，可能含 error / events / total 等字段）
      - config: 完整配置

    返回：
      - GuardResult(allow=True): 结果正常，进入 LLM formatter
      - GuardResult(allow=False, action="deny", reason=...): 命中空结果/错误，走确定性分支

    H8 实现：收敛空结果守卫 + isError 识别
    """
    # ① isError 识别：MCP call_tool 错误返回
    if "error" in result:
        error_msg = result.get("error", "未知错误")
        return GuardResult(
            allow=False,
            action="deny",
            reason=f"工具执行失败：{error_msg}"
        )

    # ② 空结果守卫（查询类工具）
    # ai_event_list：total=0 且 events=[] → 空结果
    if tool == "ai_event_list":
        total = result.get("total", 0)
        events = result.get("events", [])
        if total == 0 and not events:
            return GuardResult(
                allow=False,
                action="deny",
                reason="ai_event_list 返回空结果（total=0, events=[]）"
            )

    # aggregate_alarms：total=0 且 data=[] → 空结果
    if tool == "aggregate_alarms":
        total = result.get("total", 0)
        data = result.get("data", [])
        if total == 0 and not data:
            return GuardResult(
                allow=False,
                action="deny",
                reason="aggregate_alarms 返回空结果（total=0, data=[]）"
            )

    # ③ 其他工具：默认放行
    # 未覆盖的工具类型默认放行，进入 LLM formatter
    # 扩展新查询类工具的空结果守卫时，在上面添加相应判断
    return GuardResult(allow=True)


# ==================== 工具函数 ====================

def is_guard_enabled(config: dict) -> bool:
    """读取护栏总开关（便于各挂载点统一判断）

    返回：
      - True: 护栏启用（默认）
      - False: 护栏关闭（等价现网行为，用于回滚）
    """
    return config.get("harness", {}).get("guard_enabled", True)


def log_guard_action(result: GuardResult, context: str = ""):
    """统一记录护栏动作（便于审计和调试）

    参数：
      - result: GuardResult 实例
      - context: 上下文信息（如 "check_plan" / "needs_confirmation"）
    """
    if result.action == "allow":
        logger.debug(f"[HARNESS-GUARD] {context}: allow")
    elif result.action == "deny":
        logger.warning(f"[HARNESS-GUARD] {context}: deny - {result.reason}")
    elif result.action == "ask":
        logger.info(f"[HARNESS-GUARD] {context}: ask - {result.reason}")


# ==================== H8：启动期自检 ====================

def self_check_result_coverage(registry: Any, config: dict):
    """启动期自检：检查注册的查询类工具是否被 check_result 覆盖

    目的：降低"新增工具漏挂核对"风险（对齐方向三 T7 的自检思路）

    检查逻辑：
      - 列出所有以 ai_/aggregate_/list_/query_ 开头的工具（查询类特征）
      - 检查 check_result 函数中是否覆盖该工具
      - 未覆盖的工具打 WARNING 日志

    注意：
      - 这是启发式检查，提醒开发者注意，不是强制拦截
      - 真实环境（MCP 连接）才有实际工具列表，测试环境跳过
    """
    if not is_guard_enabled(config):
        return  # 护栏关闭，跳过自检

    # check_result 已覆盖的工具（硬编码，与实现同步）
    covered_tools = {"ai_event_list", "aggregate_alarms"}

    # 查询类工具的命名模式（启发式）
    query_patterns = ["ai_", "aggregate_", "list_", "query_", "search_", "fetch_", "get_"]

    # 收集注册的查询类工具
    all_skills = registry.list_skills()
    if not all_skills:
        # 测试/评估环境，registry 为空，跳过自检
        logger.debug("[HARNESS-GUARD] check_result 覆盖自检：registry 为空，跳过")
        return

    query_tools = []
    for skill in all_skills:
        tool_id = skill.id
        # 检查是否匹配查询类命名模式
        if any(tool_id.startswith(p) for p in query_patterns):
            query_tools.append(tool_id)

    # 检查覆盖情况
    uncovered = [t for t in query_tools if t not in covered_tools]

    if uncovered:
        logger.warning(
            f"[HARNESS-GUARD] check_result 未覆盖以下查询类工具（可能需要空结果守卫）：{uncovered}\n"
            f"  已覆盖：{sorted(covered_tools)}\n"
            f"  如需扩展空结果守卫，请在 harness_guard.check_result 中添加相应判断"
        )
    else:
        logger.debug(
            f"[HARNESS-GUARD] check_result 覆盖检查通过，已覆盖查询类工具：{sorted(covered_tools)}"
        )
