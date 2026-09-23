"""OneBot 登录守护：NapCat / Lagrange 掉登录时自动把二维码推给你。"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

_PLUGIN_ROOT = str(Path(__file__).resolve().parent)
if _PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, _PLUGIN_ROOT)

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools

from .core.autodetect import discover
from .core.config import parse_settings
from .core.guard import Guard
from .core.schema_options import (
    TEMPLATE_KEY,
    collect_db_sessions,
    inject_schema,
    platform_entries,
    session_entries,
)
from .core.web_api import register_web_apis

PLUGIN_NAME = "astrbot_plugin_onebot_login_guard"


class OneBotLoginGuardPlugin(Star):
    """登录守护插件主体。"""

    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        # 保留 AstrBot 的配置对象本身：仪表盘每次打开配置页都会读取它的 schema，
        # 在运行时注入 options 即可把「实例 / 会话」渲染成下拉框。
        self._config_obj = config if isinstance(config, dict) else {}
        self.config = self._config_obj
        self.settings = parse_settings(self.config)
        self.data_dir = Path(StarTools.get_data_dir(PLUGIN_NAME))
        self.guard = Guard(self.settings, context, self.data_dir, logger=self.logger)
        self._observed_umos: list[str] = []
        self._db_umos: list[str] = []
        self._last_db_refresh = 0.0
        try:
            register_web_apis(self)
        except Exception as exc:
            logger.error("OneBot 登录守护：注册 Web API 失败：%s", exc)

    # ---------------- 配置页下拉选项 ----------------
    def _normalize_instances(self) -> None:
        """为旧配置补上 template_list 需要的 __template_key。"""
        raw = self._config_obj.get("instances")
        if not isinstance(raw, list):
            return
        changed = False
        for item in raw:
            if isinstance(item, dict) and not item.get("__template_key"):
                item["__template_key"] = TEMPLATE_KEY
                changed = True
        if changed:
            try:
                self._config_obj.save_config()
                self.logger.info("OneBot 登录守护：已为现有实例补全模板标识")
            except Exception as exc:
                self.logger.warning("OneBot 登录守护：写入配置失败：%s", exc)

    async def _refresh_schema_options(self, *, force_db: bool = False) -> None:
        """把平台实例与会话注入配置 schema，供配置页渲染下拉框。"""
        schema = getattr(self._config_obj, "schema", None)
        if not isinstance(schema, dict):
            return
        now = time.time()
        if force_db or now - self._last_db_refresh >= 60:
            self._last_db_refresh = now
            self._db_umos = await collect_db_sessions(self.context)
        platforms = platform_entries(self.context)
        sessions = session_entries(self._observed_umos, self._db_umos)
        if inject_schema(schema, platforms, sessions):
            self.logger.debug(
                "OneBot 登录守护：配置下拉已更新（平台 %d 个，会话 %d 个）",
                len(platforms),
                len(sessions),
            )

    @filter.on_platform_loaded()
    async def _on_platform_loaded(self, *args, **kwargs) -> None:
        """平台加载完成后刷新下拉选项。"""
        await self._refresh_schema_options(force_db=True)

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def _observe_session(self, event: AstrMessageEvent):
        """记录机器人见过的会话，作为「通知会话」下拉的候选项。"""
        umo = str(getattr(event, "unified_msg_origin", "") or "").strip()
        if not umo or umo in self._observed_umos:
            return
        self._observed_umos.append(umo)
        del self._observed_umos[:-200]
        await self._refresh_schema_options()

    async def apply_auto_detect(self, *, force: bool = False) -> dict:
        """自动识别协议端路径并回填配置（默认只填空字段）。"""
        discovery = await asyncio.to_thread(discover)
        data = discovery.to_dict()
        raw = self._config_obj.get("instances")
        changed = False
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                if discovery.qr_path and (force or not str(item.get("qr_path") or "").strip()):
                    item["qr_path"] = discovery.qr_path
                    changed = True
                if discovery.log_path and (force or not str(item.get("log_path") or "").strip()):
                    item["log_path"] = discovery.log_path
                    changed = True
                if discovery.http_url and (force or not str(item.get("http_url") or "").strip()):
                    item["http_url"] = discovery.http_url
                    if discovery.http_token:
                        item["http_token"] = discovery.http_token
                    changed = True
        if changed:
            try:
                self._config_obj.save_config()
            except Exception as exc:
                self.logger.warning("OneBot 登录守护：写回自动识别结果失败：%s", exc)
            fresh = parse_settings(self._config_obj)
            by_id = {entry.instance_id: entry for entry in fresh.instances}
            for entry in self.settings.instances:
                source = by_id.get(entry.instance_id)
                if source is None:
                    continue
                entry.log_path = source.log_path
                entry.qr_path = source.qr_path
                entry.http_url = source.http_url
                entry.http_token = source.http_token
            self.logger.info(
                "OneBot 登录守护：自动识别到 qr_path=%s log_path=%s",
                discovery.qr_path or "（无）",
                discovery.log_path or "（无）",
            )
        return data

    async def initialize(self) -> None:
        self._normalize_instances()
        if self.settings.auto_detect:
            await self.apply_auto_detect()
        await self._refresh_schema_options(force_db=True)
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
            yield event.plain_result(
                "当前不需要扫码（未处于「需要登录」状态），没有可推送的二维码。"
            )
            return
        yield event.plain_result(
            "已按协议端最新状态重新探测并推送二维码到所有通知渠道。"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("登录守护识别")
    async def cmd_discover(self, event: AstrMessageEvent):
        """自动识别协议端路径并回填（仅管理员）。"""
        data = await self.apply_auto_detect()
        lines = ["🔍 协议端路径自动识别"]
        if data.get("endpoints"):
            for item in data["endpoints"]:
                lines.append("• 进程：{} (pid {})".format(item.get("kind"), item.get("pid")))
        else:
            lines.append("• 未发现协议端进程")
        lines.append("• 二维码：{}".format(data.get("qr_path") or "未找到"))
        lines.append("• 日志：{}".format(data.get("log_path") or "未找到"))
        lines.append("• WebUI：{}".format(data.get("http_url") or "未找到"))
        for note in data.get("notes") or []:
            lines.append("· " + str(note))
        yield event.plain_result(chr(10).join(lines))

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
