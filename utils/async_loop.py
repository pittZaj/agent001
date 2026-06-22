"""进程级后台事件循环（MCP 连接持久化的根基）。

为什么需要它
------------
MCP 的 ``streamablehttp_client`` 在建立连接时，会用 anyio 打开一个**取消作用域
(cancel scope)**，该作用域与"创建它的那个任务"绑定。旧方案里每次工具调用都是一次
独立的 ``run_until_complete`` / ``asyncio.run``（= 一个新任务、甚至新循环），复用同
一条连接时作用域所属的任务已经结束；待连接被销毁（循环关闭 / 生成器被 GC 终结）时，
anyio 试图在"另一个任务"里退出该作用域，于是抛出：

    RuntimeError: Attempted to exit cancel scope in a different task than it was entered in
    RuntimeError: aclose(): asynchronous generator is already running

修补 teardown 治标不治本——连接必须存活在一个**永不中途关闭的事件循环**里。

方案
----
在一个守护线程里跑一个永不退出的事件循环。所有 MCP 协程都通过
``run_coroutine_threadsafe`` 投递到这个循环上执行。连接在此循环内创建一次、全程复用；
循环本身从不停止，因此永远不会出现"跨任务退出取消作用域"的崩溃。这同时天然满足
"跨请求复用连接"（优化 4.1 的真正目标）——进程内所有图执行共享同一条 MCP 连接，
每个进程只握手一次。

进程退出时该守护线程随解释器一并结束，不做优雅 aclose；此时若有无害的生成器终结
噪音，属预期范围（连接早已不再使用）。
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any, Coroutine

from loguru import logger

_loop: asyncio.AbstractEventLoop | None = None
_lock = threading.Lock()


def get_background_loop() -> asyncio.AbstractEventLoop:
    """返回进程级后台事件循环（首次调用时在守护线程里启动并 run_forever）。"""
    global _loop
    if _loop is not None and not _loop.is_closed():
        return _loop
    with _lock:
        if _loop is not None and not _loop.is_closed():
            return _loop
        loop = asyncio.new_event_loop()

        def _run() -> None:
            asyncio.set_event_loop(loop)
            loop.run_forever()

        threading.Thread(target=_run, name="ksagent-mcp-loop", daemon=True).start()
        logger.info("[async_loop] 后台事件循环已启动 (thread=ksagent-mcp-loop)")
        _loop = loop
        return loop


def run_on_background_loop(coro: Coroutine) -> Any:
    """把协程投递到后台循环执行并同步等待结果（供同步图节点 / 初始化调用）。

    调用方必须运行在**其它线程**（图工作线程、uvicorn 线程等），绝不能在后台循环
    线程内调用自身——那会自我阻塞。本平台所有调用点均满足此约束。
    """
    loop = get_background_loop()
    fut = asyncio.run_coroutine_threadsafe(coro, loop)
    return fut.result()
