"""通知层：把事件投递到 AstrBot 会话 / Webhook / 邮件。"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import smtplib
import time
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Protocol

from .config import EmailConfig, GuardSettings, WebhookConfig
from .models import GuardEvent


def now_ts() -> int:
    return int(time.time())


def render_text(event: GuardEvent, *, qr_url: str = "", instance_label: str = "") -> str:
    """构造通知文本。"""

    name = instance_label or event.instance_id
    lines = [
        "🔐 OneBot 登录守护",
        "实例：" + name,
        "状态：" + event.label,
        "时间：" + time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(event.ts or now_ts())),
    ]
    if event.kind == "need_login":
        if event.qr_stale:
            lines.append("⚠️ 协议端在等待扫码，但它手上那张二维码已经过期（协议端不再自动刷新）。")
            lines.append("请到协议端 WebUI 重新发起登录，或重启协议端服务；新二维码生成后会立刻推送。")
        else:
            lines.append("请在二维码过期前用手机 QQ 扫描下方二维码完成登录。")
    elif event.kind == "qr_expired":
        lines.append("⚠️ 二维码已经过期，且协议端不再自动刷新。")
        lines.append("请到协议端 WebUI 重新发起登录，或重启协议端服务；新二维码生成后会立刻推送。")
    elif event.kind == "offline":
        lines.append("协议端反向 WebSocket 已断开，请检查 NapCat/Lagrange 进程与网络。")
    elif event.kind == "recovered":
        lines.append("登录已恢复，机器人重新上线。")
    elif event.kind == "qr_refreshed":
        lines.append("二维码已刷新，请扫描最新二维码。")
    if qr_url:
        lines.append("二维码解码链接：" + qr_url)
    return "\n".join(lines)


def _qr_base64(path: str) -> str:
    try:
        return base64.b64encode(Path(path).read_bytes()).decode("ascii")
    except OSError:
        return ""


def _qr_md5(path: str) -> str:
    try:
        return hashlib.md5(Path(path).read_bytes()).hexdigest()
    except OSError:
        return ""


class BaseNotifier(Protocol):
    channel_id: str

    async def send(self, event: GuardEvent, text: str) -> bool: ...


class AstrBotSessionNotifier:
    """把通知发到 AstrBot 会话（umo），可跨平台自救。"""

    def __init__(
        self,
        context: Any,
        sessions: list[str],
        *,
        send_image: bool = True,
        logger: Any = None,
    ) -> None:
        self.context = context
        self.sessions = [item for item in sessions if item]
        self.send_image = send_image
        self.logger = logger

    @property
    def channel_id(self) -> str:
        return "astrbot:" + ",".join(self.sessions) if self.sessions else "astrbot"

    async def send(self, event: GuardEvent, text: str) -> bool:
        if not self.sessions:
            return False
        from astrbot.api.event import MessageChain
        from astrbot.api.message_components import Image, Plain

        ok = False
        for session in self.sessions:
            chain = [Plain(text)]
            if self.send_image and event.qr_path and Path(event.qr_path).is_file():
                chain.append(Image.fromFileSystem(str(event.qr_path)))
            try:
                sent = await self.context.send_message(session, MessageChain(chain))
                ok = bool(sent) or ok
            except Exception as exc:
                if self.logger is not None:
                    self.logger.warning(
                        "登录守护：向会话 %s 发送失败：%s", session, exc
                    )
        return ok


class WebhookNotifier:
    """外部 Webhook：wecom / dingtalk / feishu / telegram / generic。"""

    def __init__(
        self,
        config: WebhookConfig,
        *,
        send_image: bool = True,
        logger: Any = None,
    ) -> None:
        self.config = config
        self.send_image = send_image
        self.logger = logger

    @property
    def channel_id(self) -> str:
        return self.config.channel_id

    def _payload(self, event: GuardEvent, text: str) -> tuple[str, dict[str, Any] | str]:
        kind = self.config.type
        qr_path = event.qr_path if self.send_image else ""
        if kind == "wecom":
            if qr_path and Path(qr_path).is_file():
                body = _qr_base64(qr_path)
                if body:
                    return self.config.url, {
                        "msgtype": "image",
                        "image": {"base64": body, "md5": _qr_md5(qr_path)},
                    }
            return self.config.url, {"msgtype": "text", "text": {"content": text}}
        if kind == "dingtalk":
            return self.config.url, {
                "msgtype": "markdown",
                "markdown": {"title": "OneBot 登录守护", "text": text},
                "at": {"atMobiles": list(self.config.at_mobiles or [])},
            }
        if kind == "feishu":
            return self.config.url, {"msg_type": "text", "content": {"text": text}}
        if kind == "telegram":
            # README 的约定：url 填 chat_id、token 填 Bot Token（BUG-047 修复前 chat_id 会被清空）
            chat_id = self.config.url
            if chat_id.startswith("http"):
                # 兼容另一种写法：url 直接给完整 API 地址，此时 token 当 chat_id 用
                endpoint = chat_id
                chat_id = self.config.token
            elif self.config.token:
                endpoint = "https://api.telegram.org/bot" + self.config.token + "/sendMessage"
            else:
                endpoint = ""
            return endpoint, {"chat_id": chat_id, "text": text}
        return self.config.url, {
            "event": event.kind,
            "instance_id": event.instance_id,
            "state": event.state.value,
            "qr_url": event.qr_url,
            "text": text,
        }

    async def send(self, event: GuardEvent, text: str) -> bool:
        if not self.config.url:
            return False
        try:
            import aiohttp
        except ImportError:
            return False
        url, payload = self._payload(event, text)
        if not url:
            return False
        timeout = aiohttp.ClientTimeout(total=15)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                if isinstance(payload, str):
                    async with session.post(url, data=payload) as resp:
                        return 200 <= resp.status < 300
                async with session.post(url, json=payload) as resp:
                    return 200 <= resp.status < 300
        except Exception as exc:
            if self.logger is not None:
                self.logger.warning(
                    "登录守护：Webhook(%s) 发送失败：%s", self.config.type, exc
                )
            return False


class EmailNotifier:
    """SMTP 邮件通知（SSL / STARTTLS），二维码作为附件。"""

    def __init__(self, config: EmailConfig, *, logger: Any = None) -> None:
        self.config = config
        self.logger = logger

    @property
    def channel_id(self) -> str:
        return "email"

    def _build(self, event: GuardEvent, text: str) -> EmailMessage:
        message = EmailMessage()
        message["Subject"] = "OneBot 登录守护：" + event.label
        message["From"] = self.config.sender
        message["To"] = ", ".join(self.config.to_addrs)
        message.set_content(text)
        if event.qr_path and Path(event.qr_path).is_file():
            try:
                data = Path(event.qr_path).read_bytes()
                message.add_attachment(
                    data, maintype="image", subtype="png", filename="qrcode.png"
                )
            except OSError:
                pass
        return message

    def _send_sync(self, message: EmailMessage) -> bool:
        cfg = self.config
        if cfg.security == "ssl":
            server = smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, timeout=20)
        else:
            server = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=20)
        try:
            if cfg.security == "starttls":
                server.starttls()
            if cfg.username:
                server.login(cfg.username, cfg.password)
            server.send_message(message)
            return True
        finally:
            try:
                server.quit()
            except Exception:
                pass

    async def send(self, event: GuardEvent, text: str) -> bool:
        if not self.config.ready:
            return False
        message = self._build(event, text)
        try:
            return await asyncio.to_thread(self._send_sync, message)
        except Exception as exc:
            if self.logger is not None:
                self.logger.warning("登录守护：邮件发送失败：%s", exc)
            return False


def build_notifiers(settings: GuardSettings, context: Any, logger: Any = None) -> list[Any]:
    """根据配置构建全部通知渠道。"""
    notifiers: list[Any] = []
    if settings.notify_sessions:
        notifiers.append(
            AstrBotSessionNotifier(
                context,
                settings.notify_sessions,
                send_image=settings.send_qr_image,
                logger=logger,
            )
        )
    for hook in settings.webhooks:
        notifiers.append(
            WebhookNotifier(hook, send_image=settings.send_qr_image, logger=logger)
        )
    if settings.email.ready:
        notifiers.append(EmailNotifier(settings.email, logger=logger))
    return notifiers
