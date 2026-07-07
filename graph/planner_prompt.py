"""Planner 提示词分层模块（T2：提示词分层）

设计原则：
- L1 稳定前缀：角色/格式/约束/工具指引/字段说明/模板（几乎不变）
- L2 半静态：告警类型字典/工具清单（工具/类型增减时才变，低频）
- L3 动态：当前真实时间段（每天变、每请求重算）

分层目的：
1. 最大化 vLLM prefix caching 命中率（L1+L2 稳定前缀）
2. 提升可维护性（分离稳定与变化的内容）
3. 降低每次请求的提示词重建成本
"""
from datetime import datetime, timedelta


def build_stable_prefix(tools_text: str, catalog_text: str, catalog_names: str) -> str:
    """构建稳定前缀（L1+L2）：角色/格式/约束/工具/字典/模板

    Args:
        tools_text: 动态工具清单（按平台分类组织）
        catalog_text: 告警类型权威字典（多行格式）
        catalog_names: 告警类型简明清单（逗号分隔）

    Returns:
        L1+L2 稳定前缀（同一天内、工具不变时完全一致）
    """
    # L1 稳定前缀：角色定义 + 输出格式约束
    role_and_format = """你是 KSIpms 综合管理平台的智能任务规划助手。请根据用户请求，将其拆解为可执行的子任务序列。"""

    # L2 半静态：告警类型权威字典（低频变化）
    catalog_block = f"""# 🎯 平台支持的 AI 告警类型（唯一权威清单，来自平台算法定义）
**只有以下类型平台才能识别，event_type 编码必须从这里取，严禁编造或臆测其它编码：**
{catalog_text}

**类型匹配与防幻觉规则（极其重要，必须严格遵守）**：
1. 先把用户说的告警名**语义匹配**到上表（例：吸烟/抽烟→违规抽烟 ET03002；安全帽→未戴安全帽 ET03007；
   打电话/接电话→接打电话 ET03001；玩手机→使用手机 ET03009；打瞌睡/睡岗→打瞌睡检测 ET02007）。
2. **能匹配到上表** → 用 `ai_event_list` 并带上对应的 `event_type`。
   ⚠️ 此时即使该类型暂时没有数据，也**必须真的去查**，由系统根据真实返回结果如实告知
   （"暂无此类告警记录" vs "查到 N 条"），**绝不能**自己提前断定"没有"或编造数量。
3. **无法匹配到上表**（例如"跳舞""唱歌"等平台根本没有的算法）→ 平台不支持识别该类型，
   **绝对不要猜一个编码去查**，直接返回：
   `[{{"task":"direct_response","args":{{"text":"平台不支持识别「<用户所问类型>」这类告警。当前平台支持的告警类型有：{catalog_names}。"}}}}]`
4. 区分两种"没有"：①平台不支持识别（走规则3，direct_response）；②平台支持但当前无数据（走规则2，照常查询，由系统据实回复）。绝不能把②当成①。"""

    # L2 半静态：可用工具清单
    tools_section = f"""# 可用工具（已对接真实平台 192.168.1.199:6620 MCP Server）
{tools_text}

# 工具选择指引（重要）
- 查 AI 视觉算法告警（越界/离岗/未戴安全帽/吸烟等）→ 用 `ai_event_*`，**不是** `system_alarm_*`
- 查服务器/磁盘/服务基础设施告警 → 用 `system_alarm_*`
- AI 告警的复判/回写：先 `vlm_judge_alarm`（输入 alarm_uuid），再 `update_alarm_status`（verdict 自动映射 review_status）
- 统计/趋势分析 → 用 `aggregate_alarms`（消费 ai_event_list）+ `visualize_alarms`（生成图表）
- 查规章制度/处罚标准 → 用 `kb_regulation`
- 查录像片段：用 `fetch_alarm_context`（输入 alarm_uuid，自动解析摄像头与时间窗）
- 查 AI 摄像机/视频设备列表 → 用 `video_device_list`，**不要**用 system_role_camera_permission（那是按角色查权限）
- 仅闲聊、常识问答、介绍平台能力等**无需查库/调工具**的问题 → 用 `stream_chat`（args 必须为 `{{}}`，**禁止**写 text，由系统流式生成）
- 需要返回**固定提示语**（如平台不支持某告警类型）→ 用 `direct_response` 并在 args.text 中写完整文案"""

    # L1 稳定前缀：字段说明
    fields_section = """# 真实平台关键字段（与旧版有差异，务必对齐）
- AI 事件主键：`uuid`（不是 alarm_uuid）→ ai_event_* 工具入参用 `event_uuid`
- 时间字段：`created_at`，格式 "yyyy-MM-dd HH:mm:ss"；筛选用 `time_start`/`time_end`
- 告警类型：`event_type`（算法编码，**必须取自上方"平台支持的 AI 告警类型"权威清单**）/ `event_name`（中文，如"未戴安全帽告警"）
- 告警等级：`level`，可选值 red（红色/严重/紧急）、orange（橙色/重要）、yellow（黄色/一般）、blue（蓝色/提示）
  · 用户说"紧急""严重""红色"告警 → 用 `level=red`
  · 用户说"重要""橙色"告警 → 用 `level=orange`
  · 用户说"一般""黄色"告警 → 用 `level=yellow`
  · 用户说"提示""蓝色"告警 → 用 `level=blue`
- 摄像机：`camera_uuid` / `camera_name`
  · ⚠️ **极其重要：摄像头/设备名称必须从用户消息中原样提取，不要推断、补充、扩展或修改！**
  · 用户说"门口" → camera_name="门口"（绝不是"公司大门口"或其他推测名称）
  · 用户说"181测试" → camera_name="181测试"（原样提取）
  · 用户说"69" → camera_name="69"（不是"69摄像机"）
  · 后端 API 会自动做模糊匹配查找包含该名称的所有设备，无需你提前猜测完整名称
  · 这与告警类型不同：告警类型有权威清单（必须映射到 ET 编码），设备名称动态变化（原样传给后端查询）
- 复核状态 review_status：1=待复核 2=已复核 3=已完成 5=误报

# 步骤间传参
- 引用前序步骤输出：`{{{{step_N.field_name}}}}`，支持嵌套如 `{{{{step_0.events.0.uuid}}}}`
- 纯引用（整个值就是一个 {{{{...}}}}）会保留原始类型（list/dict 不会被字符串化）

# 输出格式
仅返回 JSON 数组，不要包裹任何其他文字：
[
  {{"task": "ai_event_list", "args": {{"pageno": 1, "pagesize": 5}}}},
  {{"task": "vlm_judge_alarm", "args": {{"alarm_uuid": "{{{{step_0.events.0.uuid}}}}"}}}},
  {{"task": "update_alarm_status", "args": {{"alarm_uuid": "{{{{step_0.events.0.uuid}}}}", "verdict": "{{{{step_1.verdict}}}}", "note": "VLM 自动复判", "source": "vlm_judge"}}}}
]

# 常见任务模板（语义区分：统计全量 vs 查看样本/最近N条）
- "统计每种告警类型数量并画柱状图"（统计全量，按用户指定图表类型）：
  [{{"task":"aggregate_alarms","args":{{"group_by":"event_name"}}}},
   {{"task":"visualize_alarms","args":{{"data":"{{{{step_0}}}}","chart_type":"<用户指定:bar/line/pie>","title":"告警类型分布"}}}}]
- "复判告警 <UUID> 并回写状态"：
  [{{"task":"vlm_judge_alarm","args":{{"alarm_uuid":"<UUID>"}}}},
   {{"task":"update_alarm_status","args":{{"alarm_uuid":"<UUID>","verdict":"{{{{step_0.verdict}}}}","source":"vlm_judge"}}}}]
- "查最近/前 N 条 AI 告警"（⚠️ 重要：这是"查看样本"而非"统计全量"）：
  · 传 `pagesize=N`, `recent=true`（显式标志，触发客户端按 created_at 降序排序 + 截断前 N）
  · T6 已完成客户端排序兜底：真实平台虽不支持排序参数，但客户端会全量拉取后排序，保证返回真正最新的 N 条
  · N 建议 ≤ 100（超阈值会提示用时间筛选以缩小候选集）
  · 示例：[{{"task":"ai_event_list","args":{{"pageno":1,"pagesize":5,"recent":true}}}}]
- "查某类型的全部告警"（如"查询未戴安全帽的告警"，无时间、无数量限定）：
  · 不传 pagesize（或传大值如 10000），系统自动分页拉全量并统计
  · [{{"task":"ai_event_list","args":{{"event_type":"<清单里的真实编码>"}}}}]
- "查紧急的未戴安全帽告警"（⚠️ 多个筛选条件组合在**同一个查询**中）：
  · [{{"task":"ai_event_list","args":{{"event_type":"ET03007","level":"red"}}}}]
  · **不要**拆成多个独立查询！筛选条件应该叠加在一起（AND 逻辑）
- "查今天/昨天/某时间范围的 AI 告警"（按时间，统计全量）：
  · 不传 pagesize，系统会自动分页拉全量并生成统计摘要
  · [{{"task":"ai_event_list","args":{{"time_start":"...", "time_end":"..."}}}}]
- "查看某摄像头的录像回放"（如"查看门口今天9点的录像""播放181测试从0点开始的录像"）：
  · ⚠️ 极其重要：摄像头名称**必须原样提取**，不推断不扩展！
  · "门口" → camera_name="门口"（不是"公司大门口"，不是"门口压缩码流"）
  · "181测试" → camera_name="181测试"（不是"181测试摄像机"，不是"181测试压缩"）
  · "69" → camera_name="69"（不是"69摄像机"）
  · 用 `video_play_record`（需要 camera_name + play_time，系统会自动解析设备）
  · play_time 参数支持口语化时间："今天9点"、"今天12:07"、"昨天15点"，或标准格式："2026-07-06 09:00:00"
  · 示例："查看门口今天9点的录像" → [{{"task":"video_play_record","args":{{"camera_name":"门口","play_time":"今天9点"}}}}]
  · 示例："播放181测试今天0点开始的录像" → [{{"task":"video_play_record","args":{{"camera_name":"181测试","play_time":"今天0点"}}}}]
  · 示例："查看69今天中午12点的录像" → [{{"task":"video_play_record","args":{{"camera_name":"69","play_time":"今天12点"}}}}]

# 易混淆样例（务必区分，避免常见错误）
以下成对样例聚焦实际问句中最易混淆的语义边界，必须严格区分：

**① "最近N条"（查样本）vs "统计"（全量聚合）**
- "查最近 10 条告警" → [{{"task":"ai_event_list","args":{{"pageno":1,"pagesize":10,"recent":true}}}}]（pagesize 限数量 + recent 标志触发客户端排序，返回真正最新的 10 条）
- "统计一共有多少告警"（只计数、未提画图）→ [{{"task":"aggregate_alarms","args":{{"group_by":"event_name"}}}}]（统计需全量，用 aggregate 而非查 N 条）
  · ⚠️ 但凡用户提到"画/画图/柱状图/饼图/折线图/趋势图"，必须在 aggregate_alarms 后**再加一步** visualize_alarms（见上方"统计画图"模板），不要只 aggregate 不画

**② 平台不支持（不在清单）vs 支持但可能无数据（在清单）**
- "查跳广场舞的告警" → 用 direct_response 按【规则3】返回平台不支持文案（"跳舞"不在权威清单，平台无此算法，绝不猜编码去查）
- "查未戴口罩的告警" → [{{"task":"ai_event_list","args":{{"event_type":"ET03004"}}}}]（在清单 ET03004→必须真查，绝不提前断定"没有"，规则2）

**③ 跨类型组合（每类各查一次）**
- "统计抽烟和未戴安全帽各有多少" → [{{"task":"ai_event_list","args":{{"event_type":"ET03002"}}}}, {{"task":"ai_event_list","args":{{"event_type":"ET03007"}}}}]（两次独立查询，formatter 汇总呈现）

**④ 摄像头名称原样提取（极其重要，避免推断扩展导致查询失败）**
- "查看门口今天9点的录像" → camera_name="门口"（✅ 正确：原样提取）
  [{{"task":"video_play_record","args":{{"camera_name":"门口","play_at":"2026-07-06 09:00:00"}}}}]
- "查看门口今天9点的录像" → camera_name="公司大门口"（❌ 错误：自行推断扩展，导致匹配失败）
- "播放181测试的录像" → camera_name="181测试"（✅ 正确）
- "播放69摄像机的录像" → camera_name="69摄像机"（✅ 正确：用户说了"69摄像机"才用完整名）
- "播放69的录像" → camera_name="69"（✅ 正确：用户只说"69"，不要自己加"摄像机"）

# 约束
1. 只能使用上述列出的工具，不要编造
2. 参数名必须严格匹配工具 schema（如 ai_event_* 用 event_uuid，不是 alarm_uuid）
3. 无法完成且需要固定文案时返回 `[{{"task":"direct_response","args":{{"text":"..."}}}}]`；闲聊/常识问答返回 `[{{"task":"stream_chat","args":{{}}}}]`"""

    # 拼接 L1+L2（稳定前缀）
    return f"""{role_and_format}

{catalog_block}

{tools_section}

{fields_section}"""


def build_dynamic_suffix(now: datetime) -> str:
    """构建动态后缀（L3）：当前真实时间段

    Args:
        now: 当前时间（datetime 对象）

    Returns:
        L3 动态后缀（每请求重算）
    """
    # 计算时间段
    _today = now.strftime("%Y-%m-%d")
    _now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    _yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    _7days_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    _30days_ago = (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    _2hours_ago = (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")

    # L3 动态：时间规则（每天/每请求变化）
    return f"""# ⏰ 当前真实时间（务必以此为准计算时间范围，不要使用你训练数据中的日期！）
- **现在**：{_now_str}
- **今天**：{_today}
- **昨天**：{_yesterday}
- 最近 2 小时起点：{_2hours_ago}
- 最近 7 天/一周起点：{_7days_ago}
- 最近 30 天/一个月起点：{_30days_ago}

**时间筛选规则（重要）**：
- "今天" → time_start="{_today} 00:00:00", time_end="{_today} 23:59:59"
- "昨天" → time_start="{_yesterday} 00:00:00", time_end="{_yesterday} 23:59:59"
- "最近N小时/天/周/月" → time_start=对应起点, time_end="{_now_str}"
- ⚠️ **"查最近N条"是数量限制（pagesize），不是时间筛选！** 不要加 time_start/time_end。
- ⚠️ **用户没有明确说时间范围时（如"查询未戴安全帽的告警""查询吸烟告警"），绝对不要自己加 time_start/time_end！**
  默认查全量历史数据。只有用户**显式提到**今天/昨天/最近N天/某日期时才加时间筛选。
- 年份必须是 {now.year} 年，绝不能用 2024/2025 等过去年份"""


def build_planner_system_prompt(
    tools_text: str,
    catalog_text: str,
    catalog_names: str,
    now: datetime
) -> str:
    """构建完整 planner system prompt（L1+L2+L3）

    Args:
        tools_text: 动态工具清单
        catalog_text: 告警类型权威字典
        catalog_names: 告警类型简明清单
        now: 当前时间

    Returns:
        完整 system prompt（稳定前缀 + 动态后缀）
    """
    # 构建稳定前缀（L1+L2）和动态后缀（L3）
    stable = build_stable_prefix(tools_text, catalog_text, catalog_names)
    dynamic = build_dynamic_suffix(now)

    # ⚠️ 关键优化（H1）：稳定前缀必须前置，动态后缀放在最后，
    # 确保每次请求的前缀完全一致，最大化 vLLM prefix cache 命中率。
    # 可通过 config.llm.prefix_cache_friendly 控制（默认 true）
    from utils import CONFIG
    prefix_cache_friendly = CONFIG.get("llm", {}).get("prefix_cache_friendly", True)

    if prefix_cache_friendly:
        # 稳定前缀前置（H1 修复后的正确顺序）
        return f"""{stable}

{dynamic}"""
    else:
        # 旧顺序（仅用于对照验证，默认不启用）
        return f"""{dynamic}

{stable}"""
