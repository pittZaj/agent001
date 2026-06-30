"""真实平台支持的 AI 算法告警类型 —— 权威静态字典。

来源：平台后端算法宏定义（KSAI_* #define），这是"平台支持哪些算法类型"的
唯一权威依据。

⚠️ 关键区分（修复"支持但无数据"被误判为"不支持"的问题）：
  - **支持的类型**：event_type 出现在本字典里（无论数据库当前有没有该类告警记录）。
  - **是否有数据**：由实际查询 ai_event_list 的 total 决定，与"是否支持"是两件事。

因此完整的判定链是：
  1. 用户问的类型能否语义匹配到本字典 → 决定"平台是否支持识别"
  2. 若支持，再查 ai_event_list → total>0 即有数据，total==0 即"支持但暂无记录"
  3. 若不支持 → 明确告知平台无此算法，并列出支持的类型

注意：event_name 取自后端注释，与 ai_event_list 实际返回的 event_name 可能有细微
差异（如"未戴安全帽告警" vs "未戴安全帽"），匹配以 event_type 编码为准。
"""
import re

# event_type 编码 → 中文显示名（平台支持的全部算法类型）
SUPPORTED_EVENT_TYPES: dict[str, str] = {
    # 人脸 / 识别类
    "ET01001": "人脸识别",
    # 人员行为 / 区域类
    "ET02001": "人员离岗",
    "ET02002": "区域入侵",
    "ET02003": "人员聚集",
    "ET02004": "人员徘徊滞留",
    "ET02005": "区域无人",
    "ET02006": "人员跌倒",
    "ET02007": "打瞌睡检测",
    "ET02008": "未授权进入",
    "ET02009": "打架",
    # 作业 / 着装 / 违规行为类
    "ET03001": "接打电话",
    "ET03002": "违规抽烟",
    "ET03003": "未穿工作服",
    "ET03004": "未戴口罩",
    "ET03005": "未戴护目镜",
    "ET03006": "未戴安全带",
    "ET03007": "未戴安全帽",
    "ET03008": "未戴绝缘手套",
    "ET03009": "使用手机",
    "ET03010": "双人在岗卸货",
    "ET03011": "人员攀爬",
    "ET03012": "登高作业无人扶梯",
    "ET03017": "穿工作服人员",
    # 消防 / 火灾类
    "ET05001": "明火",
    "ET05002": "烟雾",
    "ET05003": "灭火器",
}

# 反向索引：中文名 → 编码（便于按名称查编码；名称去掉"告警"后缀后比较）
NAME_TO_TYPE: dict[str, str] = {v: k for k, v in SUPPORTED_EVENT_TYPES.items()}

# 口语别名 → 编码（补充正式名称，长词优先匹配）
EVENT_TYPE_ALIASES: dict[str, str] = {
    "违规抽烟": "ET03002",
    "抽烟": "ET03002",
    "吸烟": "ET03002",
    "未戴安全帽": "ET03007",
    "安全帽": "ET03007",
    "使用手机": "ET03009",
    "玩手机": "ET03009",
    "手机": "ET03009",
    "接打电话": "ET03001",
    "打电话": "ET03001",
    "未戴口罩": "ET03004",
    "口罩": "ET03004",
    "明火": "ET05001",
    "烟雾": "ET05002",
    "灭火器": "ET05003",
}


def is_supported(event_type: str | None) -> bool:
    """该 event_type 编码是否为平台支持的算法类型。"""
    return bool(event_type) and event_type in SUPPORTED_EVENT_TYPES


def display_name(event_type: str | None, fallback: str | None = None) -> str:
    """编码 → 中文名（未命中时回退到 fallback 或编码本身）。"""
    if event_type and event_type in SUPPORTED_EVENT_TYPES:
        return SUPPORTED_EVENT_TYPES[event_type]
    return fallback or (event_type or "未知类型")


def normalize_event_type(raw: str | None) -> str | None:
    """将 Planner 可能输出的混写值规范为 ET 编码（如「未戴安全帽(ET03007)」→ ET03007）。"""
    if not raw:
        return None
    s = str(raw).strip()
    if m := re.search(r"(ET\d{5})", s, re.I):
        code = m.group(1).upper()
        if code in SUPPORTED_EVENT_TYPES:
            return code
    if s in SUPPORTED_EVENT_TYPES:
        return s
    if s in NAME_TO_TYPE:
        return NAME_TO_TYPE[s]
    for name, code in sorted(NAME_TO_TYPE.items(), key=lambda kv: len(kv[0]), reverse=True):
        if name in s or s in name:
            return code
    if s in EVENT_TYPE_ALIASES:
        return EVENT_TYPE_ALIASES[s]
    return None


def resolve_event_type_from_text(text: str) -> str | None:
    """从用户原话中解析 event_type 编码（长词优先，避免「手机」误匹配）。"""
    text = (text or "").strip()
    if not text:
        return None
    for alias, code in sorted(EVENT_TYPE_ALIASES.items(), key=lambda kv: len(kv[0]), reverse=True):
        if alias in text:
            return code
    for name, code in sorted(NAME_TO_TYPE.items(), key=lambda kv: len(kv[0]), reverse=True):
        if name in text:
            return code
    return None


def catalog_lines() -> str:
    """渲染成提示词用的「编码 = 名称」清单（按编码排序，稳定输出）。"""
    return "\n".join(
        f"- `{code}` = {name}"
        for code, name in sorted(SUPPORTED_EVENT_TYPES.items())
    )


def catalog_inline() -> str:
    """渲染成一行「名称(编码)、名称(编码)…」，用于 direct_response 文案。"""
    return "、".join(
        f"{name}({code})"
        for code, name in sorted(SUPPORTED_EVENT_TYPES.items())
    )
