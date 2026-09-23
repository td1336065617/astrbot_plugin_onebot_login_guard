"""配置解析与校验。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: 内置「需要登录」正则（NapCat / Lagrange / go-cqhttp 常见文案）
DEFAULT_NEED_LOGIN_PATTERNS: tuple[str, ...] = (
    r"Login Error",
    r"请扫描下面的二维码",
    r"二维码已保存到",
    r"扫码登录",
)
#: 内置「登录成功」正则
DEFAULT_ONLINE_PATTERNS: tuple[str, ...] = (
    r"登录成功",
    r"\[Login\]\s*Success",
    r"快速登录成功",
)
#: 二维码解码 URL 正则
QR_URL_PATTERN = r"二维码解码URL[:：]\s*(\S+)"

SECRET_KEYS = ("token", "http_token", "password")


@dataclass
class InstanceConfig:
    """一个被守护的 OneBot 实例。"""

    instance_id: str = "default"
    platform_id: str = "default"
    log_path: str = ""
    qr_path: str = ""
    http_url: str = ""
    http_token: str = ""
    regex_need_login: str = ""
    regex_online: str = ""

    @property
    def need_login_patterns(self) -> tuple[str, ...]:
        if self.regex_need_login.strip():
            return (self.regex_need_login.strip(),)
        return DEFAULT_NEED_LOGIN_PATTERNS

    @property
    def online_patterns(self) -> tuple[str, ...]:
        if self.regex_online.strip():
            return (self.regex_online.strip(),)
        return DEFAULT_ONLINE_PATTERNS


@dataclass
class WebhookConfig:
    """一个外部 Webhook 渠道。"""

    type: str = "generic"
    url: str = ""
    token: str = ""
    at_mobiles: list[str] = field(default_factory=list)

    @property
    def channel_id(self) -> str:
        return "webhook:" + (self.type or "generic")


@dataclass
class EmailConfig:
    """邮件通知配置。"""

    enable: bool = False
    smtp_host: str = ""
    smtp_port: int = 465
    security: str = "ssl"
    username: str = ""
    password: str = ""
    from_addr: str = ""
    to_addrs: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return bool(self.enable and self.smtp_host and self.to_addrs)

    @property
    def sender(self) -> str:
        return self.from_addr or self.username


@dataclass
class GuardSettings:
    """插件总配置。"""

    guard_enabled: bool = True
    poll_interval: int = 30
    notify_cooldown: int = 600
    offline_confirm_rounds: int = 2
    notify_on_recover: bool = True
    qr_refresh_notify: bool = True
    send_qr_image: bool = True
    send_qr_url: bool = True
    auto_detect: bool = True
    qr_fresh_seconds: int = 300
    max_events: int = 200
    log_tail_bytes: int = 65536
    instances: list[InstanceConfig] = field(default_factory=list)
    notify_sessions: list[str] = field(default_factory=list)
    webhooks: list[WebhookConfig] = field(default_factory=list)
    email: EmailConfig = field(default_factory=EmailConfig)

    def instance(self, instance_id: str) -> InstanceConfig | None:
        for item in self.instances:
            if item.instance_id == instance_id:
                return item
        return None


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "是", "开"}
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _as_int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, number))


def _as_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for item in value:
        text = _as_str(item)
        if text and text not in out:
            out.append(text)
    return out


def _parse_instance(raw: Any) -> InstanceConfig | None:
    if not isinstance(raw, dict):
        return None
    instance = InstanceConfig(
        instance_id=_as_str(raw.get("instance_id")) or "default",
        platform_id=_as_str(raw.get("platform_id")) or "default",
        log_path=_as_str(raw.get("log_path")),
        qr_path=_as_str(raw.get("qr_path")),
        http_url=_as_str(raw.get("http_url")),
        http_token=_as_str(raw.get("http_token")),
        regex_need_login=_as_str(raw.get("regex_need_login")),
        regex_online=_as_str(raw.get("regex_online")),
    )
    return instance


def _parse_webhook(raw: Any) -> WebhookConfig | None:
    if not isinstance(raw, dict):
        return None
    url = _as_str(raw.get("url"))
    kind = _as_str(raw.get("type")) or "generic"
    if not url:
        return None
    return WebhookConfig(
        type=kind,
        url=url,
        token=_as_str(raw.get("token")),
        at_mobiles=_as_str_list(raw.get("at_mobiles")),
    )


def _parse_email(raw: Any) -> EmailConfig:
    if not isinstance(raw, dict):
        return EmailConfig()
    security = _as_str(raw.get("security")) or "ssl"
    if security not in {"ssl", "starttls", "none"}:
        security = "ssl"
    return EmailConfig(
        enable=_as_bool(raw.get("enable"), False),
        smtp_host=_as_str(raw.get("smtp_host")),
        smtp_port=_as_int(raw.get("smtp_port"), 465, 1, 65535),
        security=security,
        username=_as_str(raw.get("username")),
        password=_as_str(raw.get("password")),
        from_addr=_as_str(raw.get("from_addr")),
        to_addrs=_as_str_list(raw.get("to_addrs")),
    )


def parse_settings(raw: dict | None) -> GuardSettings:
    """把 AstrBot 传入的配置字典解析为强类型设置。"""
    raw = raw if isinstance(raw, dict) else {}
    instances: list[InstanceConfig] = []
    seen: set[str] = set()
    for item in raw.get("instances") or []:
        instance = _parse_instance(item)
        if instance is None:
            continue
        if instance.instance_id in seen:
            continue
        seen.add(instance.instance_id)
        instances.append(instance)
    if not instances:
        instances = [InstanceConfig()]

    webhooks: list[WebhookConfig] = []
    for item in raw.get("webhooks") or []:
        hook = _parse_webhook(item)
        if hook is not None:
            webhooks.append(hook)

    return GuardSettings(
        guard_enabled=_as_bool(raw.get("guard_enabled"), True),
        poll_interval=_as_int(raw.get("poll_interval"), 30, 10, 600),
        notify_cooldown=_as_int(raw.get("notify_cooldown"), 600, 0, 86400),
        offline_confirm_rounds=_as_int(raw.get("offline_confirm_rounds"), 2, 1, 10),
        notify_on_recover=_as_bool(raw.get("notify_on_recover"), True),
        qr_refresh_notify=_as_bool(raw.get("qr_refresh_notify"), True),
        send_qr_image=_as_bool(raw.get("send_qr_image"), True),
        send_qr_url=_as_bool(raw.get("send_qr_url"), True),
        auto_detect=_as_bool(raw.get("auto_detect"), True),
        qr_fresh_seconds=_as_int(raw.get("qr_fresh_seconds"), 300, 30, 3600),
        max_events=_as_int(raw.get("max_events"), 200, 10, 5000),
        log_tail_bytes=_as_int(raw.get("log_tail_bytes"), 65536, 4096, 1048576),
        instances=instances,
        notify_sessions=_as_str_list(raw.get("notify_sessions")),
        webhooks=webhooks,
        email=_parse_email(raw.get("email")),
    )


def mask_secrets(payload: dict[str, Any]) -> dict[str, Any]:
    """把配置里的密钥字段脱敏（用于返回前端）。"""
    out: dict[str, Any] = {}
    for key, value in (payload or {}).items():
        if key in SECRET_KEYS and value:
            out[key] = "***"
        elif isinstance(value, dict):
            out[key] = mask_secrets(value)
        elif isinstance(value, list):
            out[key] = [
                mask_secrets(item) if isinstance(item, dict) else item for item in value
            ]
        else:
            out[key] = value
    return out
