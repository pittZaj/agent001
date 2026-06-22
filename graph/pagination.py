"""并发翻页公共模块（nodes.py 与 alarm_skills.py 共用）

设计原则（遵循 Karpathy Guidelines）：
1. 外科手术式修改：只动分页逻辑，不改调用方接口
2. 并发 + 限流：asyncio.gather + Semaphore 控制并发度
3. 容错处理：单页失败不影响其他页，记录警告并继续
4. 可观测：详细日志记录分页进度
"""
import asyncio
from typing import Callable, Any
from loguru import logger


async def fetch_all_events_concurrent(
    invoke_func: Callable,  # 调用函数（registry.invoke）
    tool_name: str,  # 工具名（"ai_event_list"）
    base_args: dict,  # 基础参数
    context: dict,  # 上下文
    first_result: dict,  # 第一页结果
    pagesize: int = 10000,  # 每页大小
    max_pages: int = 100,  # 最大页数
    max_concurrency: int = 5,  # 最大并发数
) -> dict:
    """并发拉取所有页（通用版本）

    Args:
        invoke_func: 异步调用函数，签名为 async (tool_name, args, context) -> dict
        tool_name: 工具名称
        base_args: 基础查询参数（不含 pageno/pagesize）
        context: 调用上下文
        first_result: 第一页结果（已经拉取）
        pagesize: 每页大小
        max_pages: 最大页数（防止无限循环）
        max_concurrency: 最大并发数（避免压垮平台）

    Returns:
        合并后的结果字典，包含所有页的 events
    """
    total = first_result.get('total', 0)
    current_events = first_result.get('events', [])

    # 如果第一页已是全部数据，直接返回
    if total <= len(current_events):
        return first_result

    # 计算需要拉取的总页数
    total_pages = min((total + pagesize - 1) // pagesize, max_pages)

    logger.info(
        f"[Pagination] 开始并发拉取 {total_pages} 页（total={total}, 每页 {pagesize} 条，并发度 {max_concurrency}）"
    )

    # 并发拉取所有页（限流并发度）
    semaphore = asyncio.Semaphore(max_concurrency)

    async def fetch_page(pageno: int) -> tuple[int, dict]:
        """拉取单页，返回 (pageno, result)"""
        async with semaphore:
            page_args = dict(base_args)
            page_args['pageno'] = pageno
            page_args['pagesize'] = pagesize

            try:
                result = await invoke_func(tool_name, page_args, context)
                return (pageno, result)
            except Exception as e:
                logger.warning(f"[Pagination] 第 {pageno} 页拉取异常: {e}")
                return (pageno, {"error": str(e), "events": []})

    # 并发拉取剩余页（page 2 到 total_pages，第一页已经拉取过了）
    if total_pages <= 1:
        # 只有一页，直接返回第一页结果
        return first_result

    tasks = [fetch_page(p) for p in range(2, total_pages + 1)]
    page_results = await asyncio.gather(*tasks)

    # 从第一页开始拼接
    all_events = list(current_events)
    fetched_pages = 1  # 第一页已经拉取

    for pageno, result in sorted(page_results, key=lambda x: x[0]):
        if result.get('error'):
            logger.warning(f"[Pagination] 第 {pageno} 页拉取失败: {result['error']}，跳过该页")
            continue

        page_events = result.get('events', [])
        if page_events:
            all_events.extend(page_events)
            fetched_pages += 1

    logger.info(f"[Pagination] 并发翻页完成，共 {len(all_events)}/{total} 条数据（成功 {fetched_pages}/{total_pages} 页）")

    final_result = dict(first_result)
    final_result['events'] = all_events
    final_result['_fetched_pages'] = fetched_pages
    final_result['_fetched_count'] = len(all_events)
    return final_result
