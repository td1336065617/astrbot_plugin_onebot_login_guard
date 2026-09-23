"""事件历史持久化（JSONL + 内存环形缓冲）。"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any


class EventStore:
    """保存最近的守护事件与通知结果。"""

    def __init__(self, data_dir: Path, *, max_events: int = 200, logger: Any = None) -> None:
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "events.jsonl"
        self.max_events = max(10, int(max_events))
        self.logger = logger
        self._lock = threading.Lock()
        self._events: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            if not self.path.is_file():
                return
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        for line in lines[-self.max_events:]:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict):
                self._events.append(item)

    def append(self, event: dict[str, Any], records: list[dict[str, Any]]) -> None:
        entry = {"event": event, "notifications": records}
        with self._lock:
            self._events.append(entry)
            if len(self._events) > self.max_events:
                self._events = self._events[-self.max_events:]
            self._persist()

    def _persist(self) -> None:
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            with self.path.open("w", encoding="utf-8") as handle:
                for item in self._events:
                    handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        except OSError as exc:
            if self.logger is not None:
                self.logger.warning("登录守护：事件落盘失败：%s", exc)

    def events(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._events)
        items.reverse()
        return items[: max(1, int(limit))]

    def clear(self) -> None:
        with self._lock:
            self._events = []
            self._persist()
