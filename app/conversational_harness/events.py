from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class HarnessEvent:
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.perf_counter)

    def to_json(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "ts": self.ts,
            "payload": self.payload,
        }


class EventSink:
    async def emit(self, event_type: str, **payload: Any) -> None:
        raise NotImplementedError

