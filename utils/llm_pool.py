"""LLM 客户端单例池（按 role + temperature 缓存）

设计原则：
- 避免 planner/formatter/vlm_chat 每次重复实例化 ChatOpenAI
- 按 (role, temperature) 缓存，确保不同用途的客户端隔离
- ChatOpenAI 本身是线程安全的（内部用连接池），单例化安全
"""
from langchain_openai import ChatOpenAI
from utils import CONFIG

# 客户端缓存池
_llm_cache: dict[tuple[str, float], ChatOpenAI] = {}


def get_llm(role: str = "default", temperature: float = 0.3) -> ChatOpenAI:
    """获取 LLM 客户端单例

    Args:
        role: 角色标识（planner/formatter/vlm，用于区分不同温度）
        temperature: 采样温度

    Returns:
        缓存的 ChatOpenAI 实例
    """
    cache_key = (role, temperature)

    if cache_key not in _llm_cache:
        llm_config = CONFIG["llm"]
        _llm_cache[cache_key] = ChatOpenAI(
            base_url=llm_config["base_url"],
            api_key=llm_config["api_key"],
            model=llm_config["model"],
            temperature=temperature,
        )

    return _llm_cache[cache_key]
