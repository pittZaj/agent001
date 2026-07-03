"""prefix cache 命中率探针：验证 H1/H3 的提速收益是否兑现。

用法：
    python -m agent.eval.probe_prefix_cache

设计：
    对同一个真实规模的 planner system_prompt，连续发两次请求到 8004，
    每次带不同的 user 尾部问句。测量两次请求的首 token 时延对比：
    - 若 vLLM 已开启 prefix caching 且稳定前缀前置（H1+H3）→ 第二次 TTFT 应显著下降
    - 否则两次 TTFT 接近（每次都全量 prefill）

前置条件：
    - 8004 可达（http://127.0.0.1:8004/v1）
    - Skill Registry 能初始化（需 MCP 可达，或至少能加载本地 Skills）

输出：
    打印两次请求的 TTFT 对比、prompt tokens 数量、是否命中前缀缓存的推断。
"""
import sys
import time
from datetime import datetime
from pathlib import Path

# 添加项目根到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger
from langchain_core.messages import SystemMessage, HumanMessage

from utils import CONFIG
from utils.llm_pool import get_llm
from graph.planner_prompt import build_planner_system_prompt
from skills import get_skill_registry
from skills.event_types import catalog_lines, catalog_inline


def build_realistic_planner_prompt() -> str:
    """构造一个真实规模的 planner system_prompt（与线上完全一致）。

    复用 planner_node 内的真实逻辑：获取 Skill Registry 的工具列表、
    权威告警字典，传给 build_planner_system_prompt。
    """
    logger.info("[Probe] 正在构造真实规模 planner system_prompt...")

    # 获取真实工具列表（需初始化 registry）
    registry = get_skill_registry()
    available_skills = registry.list_skills()

    # 复用 planner_node 的 _format_skills_grouped
    from graph.nodes import _format_skills_grouped
    tools_text = _format_skills_grouped(available_skills) or "暂无可用工具"

    # 权威告警字典（与 planner_node 完全一致）
    catalog_text = catalog_lines()
    catalog_names = catalog_inline()

    # 构建完整 prompt（这会命中 H1 修复前/后的顺序差异）
    system_prompt = build_planner_system_prompt(
        tools_text=tools_text,
        catalog_text=catalog_text,
        catalog_names=catalog_names,
        now=datetime.now()
    )

    logger.info(f"[Probe] system_prompt 长度: {len(system_prompt)} 字符 (~{len(system_prompt)//2} tokens)")
    return system_prompt


def measure_single_request(llm, system_prompt: str, user_query: str, round_num: int) -> dict:
    """发起一次 LLM 请求，测量首 token 时延（TTFT）和总耗时。

    Args:
        llm: LangChain ChatOpenAI 实例
        system_prompt: planner 的 system prompt
        user_query: 用户问句（不同轮次用不同问句，避免结果缓存）
        round_num: 第几轮（用于日志）

    Returns:
        {
            "ttft": 首 token 时延（秒），
            "total_time": 总耗时（秒），
            "success": 是否成功
        }
    """
    logger.info(f"[Probe] === 第 {round_num} 轮请求 ===")
    logger.info(f"[Probe] user_query: {user_query}")

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_query),
    ]

    t0_start = time.perf_counter()
    t_first_token = None

    try:
        # 使用流式接口测量 TTFT（首 token 到达时刻）
        response_text = ""
        for chunk in llm.stream(messages):
            if t_first_token is None:
                t_first_token = time.perf_counter()
            response_text += chunk.content

        t_end = time.perf_counter()

        # 计算时延
        ttft = t_first_token - t0_start if t_first_token else None
        total_time = t_end - t0_start

        logger.info(f"[Probe] 第 {round_num} 轮完成: TTFT={ttft:.3f}s, 总耗时={total_time:.3f}s")
        logger.debug(f"[Probe] 响应长度: {len(response_text)} 字符")

        return {
            "ttft": ttft,
            "total_time": total_time,
            "success": True,
            "response_length": len(response_text),
        }

    except Exception as e:
        logger.error(f"[Probe] 第 {round_num} 轮请求失败: {e}")
        return {
            "ttft": None,
            "total_time": None,
            "success": False,
            "error": str(e),
        }


def run_probe():
    """主入口：连发两次请求，对比 TTFT 判断前缀缓存是否生效。"""
    logger.info("=" * 60)
    logger.info("Prefix Cache 命中率探针 (H0)")
    logger.info("=" * 60)

    # 检查 8004 配置
    llm_config = CONFIG.get("llm", {})
    llm_url = llm_config.get("base_url", "")
    logger.info(f"LLM 服务: {llm_url}")

    if "8004" not in llm_url:
        logger.warning(f"⚠️ 当前 LLM 端点不是 8004: {llm_url}，结果可能不准确")

    # 1. 构造真实规模的 planner system_prompt（需初始化 registry）
    try:
        system_prompt = build_realistic_planner_prompt()
    except Exception as e:
        logger.error(f"❌ 构造 planner prompt 失败: {e}")
        logger.info("提示：若 MCP 未启动，至少能加载本地 Skills（direct_response 等）")
        sys.exit(1)

    # 2. 获取 planner LLM 实例（复用 llm_pool，与线上一致）
    llm = get_llm(role="planner", temperature=0.1)

    # 3. 连续两次请求（不同尾部问句，避免结果缓存）
    queries = [
        "查询未戴安全帽的告警",  # 第一次：冷启动
        "统计每种告警类型数量",  # 第二次：若前缀缓存生效，稳定前缀部分应命中
    ]

    results = []
    for i, query in enumerate(queries, start=1):
        result = measure_single_request(llm, system_prompt, query, round_num=i)
        results.append(result)
        if not result["success"]:
            logger.error(f"❌ 第 {i} 轮请求失败，中止探针")
            sys.exit(1)
        # 两次请求间隔 1s（避免过快，给 vLLM 缓存管理留时间）
        if i < len(queries):
            time.sleep(1)

    # 4. 分析结果
    logger.info("")
    logger.info("=" * 60)
    logger.info("📊 结果分析")
    logger.info("=" * 60)

    ttft_1 = results[0]["ttft"]
    ttft_2 = results[1]["ttft"]

    logger.info(f"第 1 次请求 TTFT: {ttft_1:.3f}s")
    logger.info(f"第 2 次请求 TTFT: {ttft_2:.3f}s")

    if ttft_1 and ttft_2:
        speedup = (ttft_1 - ttft_2) / ttft_1 * 100
        logger.info(f"TTFT 变化: {speedup:+.1f}% ({ttft_2:.3f}s vs {ttft_1:.3f}s)")
        logger.info("")

        # 判断前缀缓存是否生效
        if speedup > 20:
            logger.info("✅ 推断：前缀缓存**可能已生效**（第二次 TTFT 显著下降 >20%）")
            logger.info("   → H1（稳定前缀前置）+ H3（vLLM 开启缓存）可能已落地")
        elif speedup > 5:
            logger.info("⚠️ 推断：前缀缓存**部分生效**（第二次 TTFT 略降 5-20%）")
            logger.info("   → 可能 H1 已修复但 H3 未开启，或缓存命中率不高")
        else:
            logger.info("❌ 推断：前缀缓存**未生效**（两次 TTFT 接近，差异 <5%）")
            logger.info("   → 稳定前缀可能仍在变化区（H1 未修复），或 vLLM 未开 --enable-prefix-caching（H3）")
    else:
        logger.warning("⚠️ 无法计算 TTFT 变化（某次请求失败）")

    logger.info("")
    logger.info("=" * 60)
    logger.info("说明：")
    logger.info("- 本探针通过对比首 token 时延（TTFT）推断前缀缓存是否生效")
    logger.info("- 若 H1+H3 均已落地，稳定前缀命中缓存，第二次 prefill 应显著加速")
    logger.info("- 阈值 >20% 为强信号，5-20% 为弱信号，<5% 视为无变化")
    logger.info("- 真实收益需结合业务场景多次测量取平均，本探针仅作快速验证")
    logger.info("=" * 60)


def main():
    """命令行入口"""
    try:
        run_probe()
    except KeyboardInterrupt:
        logger.info("\n探针被用户中断")
        sys.exit(130)
    except Exception as e:
        logger.exception(f"探针执行异常: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
