"""OneBot 登录守护：NapCat / Lagrange 掉登录时自动把二维码推给你。"""
from __future__ import annotations

import sys
from pathlib import Path

_PLUGIN_ROOT = str(Path(__file__).resolve().parent)
if _PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, _PLUGIN_ROOT)

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools

from .core.config import parse_settings
from .core.guard import Guard
from .core.web_api import register_web_apis

PLUGIN_NAME = "astrbot_plugin_onebot_login_guard"


class OneBotLoginGuardPlugin(Star):
    """登录守护插件主体。"""

    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        self.config = config if isinstance(config, dict) else {}
        self.settings = parse_settings(self.config)
        self.data_dir = Path(StarTools.get_data_dir(PLUGIN_NAME))
        self.guard = Guard(self.settings, context, self.data_dir, logger=self.logger)
        try:
            register_web_apis(self)
        except Exception as exc:
            logger.error("OneBot 登录守护：注册 Web API 失败：%s", exc)

    async def initialize(self) -> None:
        await self.guard.start()
        self.logger.info(
            "OneBot 登录守护已启动：实例=%s 渠道=%s 轮询=%ss",
            [item.instance_id for item in self.settings.instances],
            [getattr(n, "channel_id", "unknown") for n in self.guard.notifiers],
            self.settings.poll_interval,
        )

    async def terminate(self) -> None:
        await self.guard.stop()
        self.logger.info("OneBot 登录守护已停止")

    # ---------------- 指令 ----------------
    @filter.command("登录守护状态")
    async def cmd_status(self, event: AstrMessageEvent):
        """查看 OneBot 登录守护状态。"""
        snapshot = self.guard.snapshot()
        lines = ["🔐 OneBot 登录守护"]
        for item in snapshot["instances"]:
            lines.append(
                "• {}：{}（{}）".format(
                    item["instance_id"],
                    item["state_label"],
                    item["detail"] or "—",
                )
            )
        lines.append("通知渠道：" + ("、".join(snapshot["channels"]) or "未配置"))
        lines.append("WebUI：插件管理 → OneBot 登录守护 → 状态页")
        yield event.plain_result("\n".join(lines))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("登录守护二维码")
    async def cmd_qr(self, event: AstrMessageEvent):
        """重发当前二维码（仅管理员）。"""
        sent = await self.guard.resend_qr()
        if not sent:
            yield event.plain_result("当前没有可发送的二维码。")
            return
        yield event.plain_result("已重新推送二维码到所有通知渠道。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("登录守护测试")
    async def cmd_test(self, event: AstrMessageEvent):
        """向所有渠道发送测试通知（仅管理员）。"""
        await self.guard.test_notify()
        channels = [
            getattr(item, "channel_id", "unknown") for item in self.guard.notifiers
        ]
        yield event.plain_result(
            "已发送测试通知，渠道：" + ("、".join(channels) or "未配置")
        )
