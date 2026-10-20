"""Server-Sent Events (SSE) pub/sub for live dashboard updates."""
import asyncio
import json
from typing import Set, Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class Event:
    """Event to broadcast to all connected SSE clients."""
    type: str  # "run_created", "run_updated", "step_updated", "finding_added", "gate_set", "reset"
    data: Dict[str, Any]
    timestamp: Optional[str] = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_sse(self) -> str:
        """Format as SSE event message."""
        payload = {
            "type": self.type,
            "data": self.data,
            "timestamp": self.timestamp
        }
        return f"data: {json.dumps(payload)}\n\n"


class EventBus:
    """In-memory pub/sub for SSE broadcasts."""

    def __init__(self):
        self.subscribers: Set[asyncio.Queue] = set()

    async def subscribe(self) -> asyncio.Queue:
        """Subscribe to events; returns an asyncio.Queue to read from."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self.subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        """Unsubscribe from events."""
        self.subscribers.discard(queue)

    async def publish(self, event: Event) -> None:
        """Publish an event to all active subscribers."""
        dead_queues = set()
        for queue in list(self.subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Subscriber lagged; drop oldest message and put newest
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except Exception:
                    dead_queues.add(queue)
            except Exception:
                dead_queues.add(queue)

        for dq in dead_queues:
            self.subscribers.discard(dq)


event_bus = EventBus()
