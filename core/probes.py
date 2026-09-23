"""登录状态探测层。

四种探测源，按优先级组合：
    HttpProbe -> LogFileProbe(+QrFileProbe) -> AstrBotProbe
高优先级源能给出明确结论时采用；否则逐级降级；都不确定则 UNKNOWN（不误报）。
"""
from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Any, Protocol

from .autodetect import scan_endpoints
from .config import InstanceConfig
from .models import LoginState, ProbeResult
from .qr import extract_qr_url, qr_hash_of


def now_ts() -> int:
    return int(time.time())


class BaseProbe(Protocol):
    name: str

    async def probe(self, instance: InstanceConfig) -> ProbeResult: ...


def _tail_text(path: Path, max_bytes: int) -> str:
    try:
        size = path.stat().st_size
    except OSError:
        return ""
    try:
        with path.open("rb") as handle:
            if size > max_bytes:
                handle.seek(size - max_bytes)
            data = handle.read(max_bytes)
    except OSError:
        return ""
    return data.decode("utf-8", errors="ignore")


class AstrBotProbe:
    """通过 AstrBot 平台实例判断是否连上（跨主机也可用，但拿不到二维码）。"""

    name = "astrbot"

    def __init__(self, context: Any) -> None:
        self.context = context

    def _platform_status(self, instance: InstanceConfig) -> str:
        getter = getattr(self.context, "get_platform_inst", None)
        if not callable(getter):
            return ""
        platform = None
        try:
            platform = getter(instance.platform_id)
        except Exception:
            platform = None
        if platform is None:
            return ""
        try:
            meta = platform.meta()
        except Exception:
            return ""
        if str(getattr(meta, "name", "")) != "aiocqhttp":
            return ""
        status = getattr(platform, "status", None)
        return str(getattr(status, "value", status) or "")

    def _connected(self, instance: InstanceConfig) -> bool | None:
        getter = getattr(self.context, "get_platform_inst", None)
        if not callable(getter):
            return None
        platform = None
        try:
            platform = getter(instance.platform_id)
        except Exception:
            platform = None
        bot = getattr(platform, "bot", None) if platform is not None else None
        if bot is None:
            return None
        api_clients = getattr(bot, "_wsr_api_clients", None)
        event_clients = getattr(bot, "_wsr_event_clients", None)
        if isinstance(api_clients, dict) and isinstance(event_clients, set):
            return bool(api_clients) or bool(event_clients)
        return None

    async def probe(self, instance: InstanceConfig) -> ProbeResult:
        status = await asyncio.to_thread(self._platform_status, instance)
        if not status:
            return ProbeResult(
                instance_id=instance.instance_id,
                state=LoginState.UNKNOWN,
                source=self.name,
                detail="未找到 aiocqhttp 平台实例 " + instance.platform_id,
                ts=now_ts(),
            )
        if status == "error":
            return ProbeResult(
                instance_id=instance.instance_id,
                state=LoginState.OFFLINE,
                source=self.name,
                detail="平台状态为 error",
                ts=now_ts(),
            )
        connected = await asyncio.to_thread(self._connected, instance)
        if connected is False:
            return ProbeResult(
                instance_id=instance.instance_id,
                state=LoginState.OFFLINE,
                source=self.name,
                detail="反向 WebSocket 无客户端连接",
                ts=now_ts(),
            )
        if connected is True:
            return ProbeResult(
                instance_id=instance.instance_id,
                state=LoginState.ONLINE,
                source=self.name,
                detail="反向 WebSocket 已连接",
                ts=now_ts(),
            )
        return ProbeResult(
            instance_id=instance.instance_id,
            state=LoginState.UNKNOWN,
            source=self.name,
            detail="无法判断连接状态",
            ts=now_ts(),
        )


class LogFileProbe:
    """读取协议端日志尾部，按正则判断需要登录 / 已登录，并提取二维码 URL。"""

    name = "logfile"

    def __init__(self, max_bytes: int = 65536, fresh_seconds: int = 900) -> None:
        self.max_bytes = max_bytes
        self.fresh_seconds = max(60, int(fresh_seconds))

    @staticmethod
    def _age_seconds(path: Path) -> int | None:
        try:
            return int(time.time() - path.stat().st_mtime)
        except OSError:
            return None

    @staticmethod
    def _classify(instance: InstanceConfig, text: str) -> tuple[LoginState | None, str]:
        if not text:
            return None, ""
        last_need = -1
        last_online = -1
        for pattern in instance.need_login_patterns:
            try:
                matches = list(re.finditer(pattern, text))
            except re.error:
                continue
            if matches:
                last_need = max(last_need, matches[-1].end())
        for pattern in instance.online_patterns:
            try:
                matches = list(re.finditer(pattern, text))
            except re.error:
                continue
            if matches:
                last_online = max(last_online, matches[-1].end())
        if last_need < 0 and last_online < 0:
            return None, ""
        if last_need > last_online:
            return LoginState.NEED_LOGIN, "日志显示需要重新登录"
        return LoginState.ONLINE, "日志显示登录成功"

    async def probe(self, instance: InstanceConfig) -> ProbeResult:
        path = Path(instance.log_path) if instance.log_path else None
        if path is None or not instance.log_path:
            return ProbeResult(
                instance_id=instance.instance_id,
                state=LoginState.UNKNOWN,
                source=self.name,
                detail="未配置日志路径",
                ts=now_ts(),
            )
        # 协议端在运行时会持续写日志；长时间没更新的日志说明它已经“死”了
        # （例如上一次崩溃留下的文件），此时里面的「请扫描二维码」是历史记录，
        # 绝不能拿来当作当前状态，否则会一直误报“需要登录”。
        age = await asyncio.to_thread(self._age_seconds, path)
        if age is not None and age > self.fresh_seconds:
            return ProbeResult(
                instance_id=instance.instance_id,
                state=LoginState.UNKNOWN,
                source=self.name,
                detail=f"日志已 {age} 秒未更新，视为无效（可能不是当前运行实例的日志）",
                stale=True,
                ts=now_ts(),
            )
        text = await asyncio.to_thread(_tail_text, path, self.max_bytes)
        if not text:
            return ProbeResult(
                instance_id=instance.instance_id,
                state=LoginState.UNKNOWN,
                source=self.name,
                detail="日志不可读：" + instance.log_path,
                ts=now_ts(),
            )
        state, detail = self._classify(instance, text)
        return ProbeResult(
            instance_id=instance.instance_id,
            state=state or LoginState.UNKNOWN,
            source=self.name,
            detail=detail or "日志无相关记录",
            qr_url=extract_qr_url(text),
            ts=now_ts(),
        )


class QrFileProbe:
    """读取二维码图片。

    协议端登录成功后**不会删除**二维码文件（实测 NapCat），因此用文件 mtime
    判断「当前是否在等待扫码」：新鲜 -> NEED_LOGIN，过期 -> 不表态。
    """

    name = "qrfile"

    def __init__(self, fresh_seconds: int = 300) -> None:
        self.fresh_seconds = max(30, int(fresh_seconds))

    @staticmethod
    def _age_seconds(path: Path) -> int | None:
        try:
            return int(time.time() - path.stat().st_mtime)
        except OSError:
            return None

    async def probe(self, instance: InstanceConfig) -> ProbeResult:
        result = ProbeResult(
            instance_id=instance.instance_id,
            state=LoginState.UNKNOWN,
            source=self.name,
            ts=now_ts(),
        )
        if not instance.qr_path:
            return result
        path = Path(instance.qr_path)
        try:
            if not path.is_file():
                return result
        except OSError:
            return result
        result.qr_path = str(path)
        result.qr_hash = await asyncio.to_thread(qr_hash_of, path, "")
        age = await asyncio.to_thread(self._age_seconds, path)
        if age is not None and age <= self.fresh_seconds:
            result.state = LoginState.NEED_LOGIN
            result.detail = f"二维码文件 {age} 秒前更新，疑似等待扫码"
        else:
            # 文件还在，但已经过期：说明协议端此刻并没有在等你扫码
            result.stale = True
            result.detail = (
                f"二维码文件已 {age} 秒未更新，早已过期" if age is not None else "无法读取二维码文件时间"
            )
        return result


class HttpProbe:
    """可选：向协议端 WebUI 拉取状态/二维码（跨主机场景，best-effort）。"""

    name = "http"

    NEED_LOGIN_KEYWORDS = ("二维码", "扫码", "未登录", "need login", "qrcode")
    ONLINE_KEYWORDS = ("已登录", "在线", "online", "logged")

    async def probe(self, instance: InstanceConfig) -> ProbeResult:
        if not instance.http_url:
            return ProbeResult(
                instance_id=instance.instance_id,
                state=LoginState.UNKNOWN,
                source=self.name,
                detail="未配置 WebUI 地址",
                ts=now_ts(),
            )
        try:
            import aiohttp
        except ImportError:
            return ProbeResult(
                instance_id=instance.instance_id,
                state=LoginState.UNKNOWN,
                source=self.name,
                detail="未安装 aiohttp",
                ts=now_ts(),
            )
        headers = {}
        if instance.http_token:
            headers["Authorization"] = "Bearer " + instance.http_token
        try:
            timeout = aiohttp.ClientTimeout(total=8)
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(instance.http_url, headers=headers) as resp,
            ):
                text = await resp.text()
        except Exception as exc:
            return ProbeResult(
                instance_id=instance.instance_id,
                state=LoginState.UNKNOWN,
                source=self.name,
                detail="请求失败：" + type(exc).__name__,
                ts=now_ts(),
            )
        lowered = text.lower()
        if any(keyword in text or keyword in lowered for keyword in self.NEED_LOGIN_KEYWORDS):
            state = LoginState.NEED_LOGIN
            detail = "WebUI 显示需要登录"
        elif any(keyword in text or keyword in lowered for keyword in self.ONLINE_KEYWORDS):
            state = LoginState.ONLINE
            detail = "WebUI 显示已登录"
        else:
            state = LoginState.UNKNOWN
            detail = "WebUI 响应无法判定"
        return ProbeResult(
            instance_id=instance.instance_id,
            state=state,
            source=self.name,
            detail=detail,
            qr_url=extract_qr_url(text),
            ts=now_ts(),
        )


class ProcessProbe:
    """协议端进程是否还在运行。

    用于区分两种「反向 WS 没连上」：
    - 进程在跑  -> 它是在等扫码登录（NapCat 掉登录后不会连 WS）
    - 进程不在  -> 协议端挂了/没启动
    """

    name = "process"

    def __init__(self, ttl_seconds: int = 10) -> None:
        self.ttl_seconds = max(1, int(ttl_seconds))
        self._cached_at = 0.0
        self._cached = False

    async def alive(self) -> bool:
        now = time.time()
        if now - self._cached_at < self.ttl_seconds:
            return self._cached
        endpoints = await asyncio.to_thread(scan_endpoints)
        self._cached = bool(endpoints)
        self._cached_at = now
        return self._cached


class ProbeManager:
    """按实例组合探测源，输出统一的 ProbeResult。"""

    def __init__(
        self,
        context: Any,
        *,
        log_tail_bytes: int = 65536,
        qr_fresh_seconds: int = 300,
        log_fresh_seconds: int = 900,
    ) -> None:
        self.astrbot = AstrBotProbe(context)
        self.logfile = LogFileProbe(
            max_bytes=log_tail_bytes, fresh_seconds=log_fresh_seconds
        )
        self.qrfile = QrFileProbe(fresh_seconds=qr_fresh_seconds)
        self.http = HttpProbe()
        self.process = ProcessProbe()

    async def probe(self, instance: InstanceConfig) -> ProbeResult:
        log_result = await self.logfile.probe(instance)
        http_result = await self.http.probe(instance)
        astrbot_result = await self.astrbot.probe(instance)
        qr_result = await self.qrfile.probe(instance)

        state = LoginState.UNKNOWN
        source = ""
        detail = ""
        if astrbot_result.state is LoginState.ONLINE:
            # 反向 WS 连上了 -> 一定已登录成功（协议端登录后才会连 WS）
            state = LoginState.ONLINE
            source = astrbot_result.source
            detail = astrbot_result.detail
        elif qr_result.state is LoginState.NEED_LOGIN:
            # 二维码刚刚刷新 -> 正在等扫码
            state = LoginState.NEED_LOGIN
            source = qr_result.source
            detail = qr_result.detail
        elif astrbot_result.state is LoginState.OFFLINE:
            # 反向 WS 没连上：进程还在 = 它在等扫码；进程没了 = 协议端挂了
            if await self.process.alive():
                state = LoginState.NEED_LOGIN
                source = self.process.name
                detail = "协议端进程在运行，但反向 WebSocket 未连接——通常表示正在等待扫码登录"
            else:
                state = LoginState.OFFLINE
                source = astrbot_result.source
                detail = astrbot_result.detail
        elif log_result.state is not LoginState.UNKNOWN:
            state = log_result.state
            source = log_result.source
            detail = log_result.detail
        elif http_result.state is not LoginState.UNKNOWN:
            state = http_result.state
            source = http_result.source
            detail = http_result.detail

        if state is LoginState.UNKNOWN:
            details = [
                item.detail
                for item in (log_result, http_result, astrbot_result)
                if item.detail
            ]
            return ProbeResult(
                instance_id=instance.instance_id,
                state=LoginState.UNKNOWN,
                source="none",
                detail="；".join(details[:3]) or "所有探测源均无结论",
                ts=now_ts(),
            )

        qr_path = qr_result.qr_path
        qr_url = log_result.qr_url or http_result.qr_url
        qr_hash = qr_result.qr_hash or qr_hash_of("", qr_url)
        stale = qr_result.stale
        if state is LoginState.NEED_LOGIN:
            if not qr_path and not qr_url:
                detail = (detail + "；未找到二维码，请检查 qr_path / log_path 配置").strip("；")
            elif stale and qr_path:
                detail = (
                    detail + "；现有二维码文件已过期，协议端已不再自动刷新"
                ).strip("；")
        return ProbeResult(
            instance_id=instance.instance_id,
            state=state,
            source=source,
            detail=detail,
            qr_path=qr_path,
            qr_url=qr_url,
            qr_hash=qr_hash,
            stale=stale,
            ts=now_ts(),
        )
