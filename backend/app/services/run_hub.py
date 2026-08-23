"""Per-run live SSE event hub.

每个进行中的 run 在 hub 里有一个 RunChannel，承担三件事：

1. ``publish()`` 持久化事件到 ``RunEvent`` 后广播给当前订阅者。
2. 多订阅者广播：每个 SSE 订阅者有自己的 ``asyncio.Queue``，hub publish 时
   逐个 put_nowait；满了就丢掉最老的，保证 publish 不阻塞 orchestrator。
3. 暴露 ``cancel_event`` 和 waiting 状态，供 orchestrator 协调取消与中段交互。

历史 replay 的唯一来源是 ``RunEvent`` 数据库；RunChannel 只负责当前进程实时 tail。
run 被删除时调 ``RunHub.discard`` 清理。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable

from app.db import SessionLocal
from app.runtime.base import DecisionResult
from app.services.run_events import append_run_event
from app.utils import dumps

logger = logging.getLogger(__name__)

# 单订阅者最多积压的事件数量；满了用 drop-oldest 策略，避免 publish 阻塞。
SUBSCRIBER_QUEUE_SIZE = 512
# SSE 心跳间隔（秒）；空闲超过这个时长就发一次 ``:keep-alive`` 注释帧。
KEEPALIVE_SECONDS = 15.0


@dataclass
class StoredEvent:
    """当前进程实时 tail 中的一条事件。"""

    id: int
    event: str
    data: dict[str, Any]

    def to_sse_frame(self) -> str:
        # data 必须单行；多行 data 由前端拼，但当前协议里所有 data 都是 JSON 单对象。
        payload = dumps(self.data)
        return f"id: {self.id}\nevent: {self.event}\ndata: {payload}\n\n"


@dataclass
class RunSubscription:
    """已注册的 live tail 订阅。"""

    queue: asyncio.Queue[StoredEvent | None]
    already_closed: bool


class RunChannel:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._subscribers: set[asyncio.Queue[StoredEvent | None]] = set()
        self._lock = asyncio.Lock()
        self.closed = False
        # orchestrator 通过这个 event 接收 cancel 信号；订阅者也能 await 它。
        self.cancel_event = asyncio.Event()
        # 并行节点可能同时进入 decision_request；当前前端只支持一个等待面板，
        # 因此每个 run channel 同一时间只发布一个 waiting request。
        self.waiting_lock = asyncio.Lock()
        self.waiting_node_id: str | None = None
        self.waiting_request_id: str | None = None
        self._waiting_future: asyncio.Future[DecisionResult] | None = None
        self._waiting_ack_future: asyncio.Future[bool] | None = None

    def begin_waiting(self, node_id: str, request_id: str) -> asyncio.Future[DecisionResult]:
        if self._waiting_future is not None and not self._waiting_future.done():
            raise RuntimeError("run 已有待回答的问题")
        self.waiting_node_id = node_id
        self.waiting_request_id = request_id
        self._waiting_future = asyncio.get_running_loop().create_future()
        self._waiting_ack_future = asyncio.get_running_loop().create_future()
        return self._waiting_future

    def submit_resume(
        self,
        node_id: str,
        request_id: str,
        result: DecisionResult,
    ) -> asyncio.Future[bool] | None:
        future = self._waiting_future
        if (
            future is None
            or future.done()
            or self.waiting_node_id != node_id
            or self.waiting_request_id != request_id
        ):
            return None
        future.set_result(result)
        return self._waiting_ack_future

    def acknowledge_resume(self, node_id: str, request_id: str) -> None:
        if self.waiting_node_id != node_id or self.waiting_request_id != request_id:
            return
        future = self._waiting_ack_future
        if future is not None and not future.done():
            future.set_result(True)

    def clear_waiting(self, node_id: str, request_id: str) -> None:
        if self.waiting_node_id != node_id or self.waiting_request_id != request_id:
            return
        ack_future = self._waiting_ack_future
        if ack_future is not None and not ack_future.done():
            ack_future.set_result(False)
        self.waiting_node_id = None
        self.waiting_request_id = None
        self._waiting_future = None
        self._waiting_ack_future = None

    def abort_waiting(self, error: str) -> None:
        future = self._waiting_future
        if future is not None and not future.done():
            future.set_result(DecisionResult(ok=False, error=error))

    async def publish(self, event: str, data: dict[str, Any]) -> StoredEvent:
        """记录一条新事件，广播给所有当前订阅者。

        publish 自身不阻塞 orchestrator：订阅者队列满时 drop-oldest，确保
        ``put_nowait`` 始终成功。
        """

        async with SessionLocal() as db:
            row = await append_run_event(db, self.run_id, event, data)

        async with self._lock:
            stored = StoredEvent(id=row.id, event=event, data=data)
            subscribers = list(self._subscribers)
        for queue in subscribers:
            _put_drop_oldest(queue, stored)
        return stored

    async def close(self) -> None:
        """run 进入终态后调用：标记 closed 并通知所有订阅者退出。"""

        async with self._lock:
            if self.closed:
                return
            self.closed = True
            subscribers = list(self._subscribers)
        for queue in subscribers:
            _put_drop_oldest(queue, None)

    async def subscribe(self) -> RunSubscription:
        """注册一个实时订阅队列。

        调用方负责先注册订阅，再从 DB replay 历史，最后消费该订阅队列中
        ``id > cursor`` 的实时事件，避免 replay 期间发布的新事件丢失。
        """

        async with self._lock:
            queue: asyncio.Queue[StoredEvent | None] = asyncio.Queue(SUBSCRIBER_QUEUE_SIZE)
            self._subscribers.add(queue)
            return RunSubscription(queue=queue, already_closed=self.closed)

    async def unsubscribe(self, subscription: RunSubscription) -> None:
        async with self._lock:
            self._subscribers.discard(subscription.queue)

    async def iter_live(
        self,
        subscription: RunSubscription,
        after_id: int | None,
        transform: Callable[[StoredEvent], StoredEvent | None] | None = None,
    ) -> AsyncIterator[bytes]:
        """转发订阅队列中的实时事件，不做任何历史 replay。"""

        if subscription.already_closed:
            return
        while True:
            try:
                next_item = await asyncio.wait_for(subscription.queue.get(), timeout=KEEPALIVE_SECONDS)
            except asyncio.TimeoutError:
                yield b": keep-alive\n\n"
                continue
            if next_item is None:
                return
            if after_id is not None and next_item.id <= after_id:
                continue
            transformed = transform(next_item) if transform else next_item
            if transformed is not None:
                yield transformed.to_sse_frame().encode("utf-8")


def _put_drop_oldest(queue: asyncio.Queue[StoredEvent | None], item: StoredEvent | None) -> None:
    """publish 永不阻塞：满时丢掉队头一条最老事件再放新事件。"""

    while True:
        try:
            queue.put_nowait(item)
            return
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                # 不应该发生（满 → empty 矛盾），但避免死循环。
                logger.warning("subscriber queue contention on run hub")
                return


@dataclass
class RunHub:
    """全进程共享的 run -> RunChannel 注册表。"""

    _channels: dict[str, RunChannel] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def create(self, run_id: str) -> RunChannel:
        async with self._lock:
            if run_id in self._channels:
                raise RuntimeError(f"run {run_id} already in hub")
            channel = RunChannel(run_id)
            self._channels[run_id] = channel
            return channel

    def get(self, run_id: str) -> RunChannel | None:
        return self._channels.get(run_id)

    async def discard(self, run_id: str) -> None:
        async with self._lock:
            channel = self._channels.pop(run_id, None)
        if channel is not None:
            await channel.close()


# 进程级单例：API 路由 / orchestrator 都从这里拿。
_hub = RunHub()


def get_run_hub() -> RunHub:
    return _hub
