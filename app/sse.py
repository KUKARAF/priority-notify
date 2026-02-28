import asyncio
import json
from collections import defaultdict

import structlog

log = structlog.get_logger()


class SSEBroker:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, str]]]] = defaultdict(set)

    def subscribe(self, user_id: str) -> asyncio.Queue[dict[str, str]]:
        queue: asyncio.Queue[dict[str, str]] = asyncio.Queue()
        self._subscribers[user_id].add(queue)
        log.info("sse_subscribe", user_id=user_id, count=len(self._subscribers[user_id]))
        return queue

    def unsubscribe(self, user_id: str, queue: asyncio.Queue[dict[str, str]]) -> None:
        self._subscribers[user_id].discard(queue)
        if not self._subscribers[user_id]:
            del self._subscribers[user_id]
        log.info("sse_unsubscribe", user_id=user_id)

    async def publish(self, user_id: str, event_type: str, data: dict) -> None:  # type: ignore[type-arg]
        event = {"event": event_type, "data": json.dumps(data)}
        for queue in self._subscribers.get(user_id, set()):
            await queue.put(event)


broker = SSEBroker()
