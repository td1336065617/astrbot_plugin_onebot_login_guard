"""数据模型（纯数据，不依赖 AstrBot）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class LoginState(str, Enum):
    """OneBot 协议端登录状态。"""

    ONLINE = "online"
    OFFLINE = "offline"
    NEED_LOGIN = "need_login"
    UNKNOWN = "unknown"


STATE_LABELS: dict[LoginState, str] = {
    LoginState.ONLINE: "在线",
    LoginState.OFFLINE: "掉线",
    LoginState.NEED_LOGIN: "需要重新登录",
    LoginState.UNKNOWN: "未知",
}

EVENT_LABELS: dict[str, str] = {
    "need_login": "需要重新登录",
    "offline": "掉线",
    "recovered": "已恢复",
    "qr_refreshed": "二维码已刷新",
    "qr_expired": "二维码已过期",
    "test": "测试通知",
}


@dataclass
class ProbeResult:
    """一次探测的结果。"""

    instance_id: str
    state: LoginState
    source: str = ""
    detail: str = ""
    qr_path: str = ""
    qr_url: str = ""
    qr_hash: str = ""
    stale: bool = False
    """证据是否已过期（过期证据不得用于判定当前状态）。"""

    ts: int = 0

    @property
    def has_qr(self) -> bool:
        return bool(self.qr_path or self.qr_url)

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "state": self.state.value,
            "state_label": STATE_LABELS.get(self.state, self.state.value),
            "source": self.source,
            "detail": self.detail,
            "qr_path": self.qr_path,
            "qr_url": self.qr_url,
            "qr_hash": self.qr_hash,
            "stale": self.stale,
            "ts": self.ts,
        }


@dataclass
class GuardEvent:
    """一次需要通知的事件。"""

    kind: str
    instance_id: str
    state: LoginState
    qr_path: str = ""
    qr_url: str = ""
    qr_hash: str = ""
    qr_stale: bool = False
    """二维码已过期：仍要告警，但不要再把这张死码当图片发出去。"""

    ts: int = 0

    @property
    def label(self) -> str:
        return EVENT_LABELS.get(self.kind, self.kind)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "kind_label": self.label,
            "instance_id": self.instance_id,
            "state": self.state.value,
            "qr_path": self.qr_path,
            "qr_url": self.qr_url,
            "qr_hash": self.qr_hash,
            "qr_stale": self.qr_stale,
            "ts": self.ts,
        }


@dataclass
class NotificationRecord:
    """一次通知投递结果。"""

    event_kind: str
    instance_id: str
    channel: str
    ok: bool
    error: str = ""
    ts: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_kind": self.event_kind,
            "instance_id": self.instance_id,
            "channel": self.channel,
            "ok": self.ok,
            "error": self.error,
            "ts": self.ts,
        }


@dataclass
class InstanceStatus:
    """某个被守护实例的运行时状态（供 WebUI 展示）。"""

    instance_id: str
    state: LoginState = LoginState.UNKNOWN
    source: str = ""
    detail: str = ""
    qr_path: str = ""
    qr_source_path: str = ""
    qr_url: str = ""
    qr_hash: str = ""
    last_probe_ts: int = 0
    last_change_ts: int = 0
    offline_streak: int = 0
    unknown_streak: int = 0
    qr_stale: bool = False
    offline_notified: bool = False
    events: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "state": self.state.value,
            "state_label": STATE_LABELS.get(self.state, self.state.value),
            "source": self.source,
            "detail": self.detail,
            "qr_path": self.qr_path,
            "qr_source_path": self.qr_source_path,
            "qr_url": self.qr_url,
            "qr_hash": self.qr_hash,
            "has_qr": bool(self.qr_path or self.qr_url),
            "last_probe_ts": self.last_probe_ts,
            "last_change_ts": self.last_change_ts,
            "offline_streak": self.offline_streak,
            "unknown_streak": self.unknown_streak,
            "qr_stale": self.qr_stale,
        }
