"""WebUI 后端 API（REST）。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from astrbot.api.web import error_response, file_response, json_response, request

PLUGIN_NAME = "astrbot_plugin_onebot_login_guard"


def register_web_apis(plugin: Any) -> None:
    """把状态/探测/重发/测试等接口注册到 AstrBot 控制台。"""
    context = plugin.context

    async def status_handler():
        return json_response({"status": "success", "data": plugin.guard.snapshot()})

    async def probe_handler():
        items = await plugin.guard.probe_now()
        return json_response({"status": "success", "data": {"instances": items}})

    async def resend_handler():
        payload = await request.json(default=None)
        payload = payload if isinstance(payload, dict) else {}
        instance_id = str(payload.get("instance_id") or "").strip() or None
        sent = await plugin.guard.resend_qr(instance_id)
        if not sent:
            return error_response("当前没有可发送的二维码")
        return json_response({"status": "success", "data": {"sent": sent}})

    async def test_handler():
        await plugin.guard.test_notify()
        channels = [
            getattr(item, "channel_id", "unknown") for item in plugin.guard.notifiers
        ]
        return json_response(
            {"status": "success", "data": {"channels": channels}}
        )

    async def events_handler():
        try:
            limit = int(request.query.get("limit", 50))
        except (TypeError, ValueError):
            limit = 50
        return json_response(
            {"status": "success", "data": {"events": plugin.guard.store.events(limit)}}
        )

    async def qr_handler():
        for item in plugin.guard.statuses.values():
            path = getattr(item, "qr_path", "")
            if path and Path(path).is_file():
                return file_response(str(path))
        return error_response("当前没有二维码")

    async def qr_data_handler():
        from .qr import read_qr_file

        for item in plugin.guard.statuses.values():
            path = getattr(item, "qr_path", "")
            if path and Path(path).is_file():
                body, _ = read_qr_file(path)
                if body:
                    return json_response(
                        {
                            "status": "success",
                            "data": {
                                "instance_id": item.instance_id,
                                "data_url": "data:image/png;base64," + body,
                            },
                        }
                    )
        return error_response("当前没有二维码")

    routes = (
        ("/status", status_handler, ["GET"], "登录守护状态"),
        ("/probe", probe_handler, ["POST"], "立即探测一次"),
        ("/resend_qr", resend_handler, ["POST"], "重发二维码"),
        ("/test_notify", test_handler, ["POST"], "发送测试通知"),
        ("/events", events_handler, ["GET"], "最近事件"),
        ("/qr", qr_handler, ["GET"], "当前二维码图片"),
        ("/qr_data", qr_data_handler, ["GET"], "当前二维码（data URL）"),
    )
    for suffix, handler, methods, desc in routes:
        context.register_web_api(
            f"/{PLUGIN_NAME}{suffix}", handler, methods, desc
        )
