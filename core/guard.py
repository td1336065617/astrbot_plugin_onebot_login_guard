"""登录守护：状态机 + 调度 + 去重 + 通知分发。"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from .config import GuardSettings, InstanceConfig
from .models import GuardEvent, InstanceStatus, LoginState, NotificationRecord, ProbeResult
from .napcat_api import NapCatWebUI, NapCatWebUIError
from .notifiers import build_notifiers, render_text
from .probes import ProbeManager
from .qr import copy_qr
from .store import EventStore


class Guard:
    """核心状态机。"""

    def __init__(
        self,
        settings: GuardSettings,
        context: Any,
        data_dir: Path,
        *,
        logger: Any = None,
    ) -> None:
        self.settings = settings
        self.context = context
        self.data_dir = Path(data_dir)
        self.logger = logger
        self.probe_manager = ProbeManager(
            context,
            log_tail_bytes=settings.log_tail_bytes,
            qr_fresh_seconds=settings.qr_fresh_seconds,
            log_fresh_seconds=settings.log_fresh_seconds,
        )
        self.notifiers = build_notifiers(settings, context, logger=logger)
        self.store = EventStore(
            self.data_dir, max_events=settings.max_events, logger=logger
        )
        self.statuses: dict[str, InstanceStatus] = {
            item.instance_id: InstanceStatus(item.instance_id)
            for item in settings.instances
        }
        self._last_notify: dict[str, float] = {}
        self._last_qr_refresh: dict[str, float] = {}
        # 发送失败的通知要重试：插件可能在平台适配器加载完成前就探测到了事件
        self._pending_events: dict[str, GuardEvent] = {}
        self._pending_at: dict[str, float] = {}
        self._pending_tries: dict[str, int] = {}
        self._retry_interval = 60.0
        self._retry_limit = 10
        self._task: asyncio.Task | None = None
        self._running = False

    # ---------------- 生命周期 ----------------
    async def start(self) -> None:
        if self._task is not None or not self.settings.guard_enabled:
            return
        self._running = True
        self._task = asyncio.create_task(self.run(), name="onebot-login-guard")

    async def stop(self) -> None:
        self._running = False
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def run(self) -> None:
        while self._running:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                if self.logger is not None:
                    self.logger.exception("登录守护：轮询异常")
            await asyncio.sleep(self.settings.poll_interval)

    # ---------------- 轮询 ----------------
    async def tick(self, *, force: bool = False) -> None:
        for instance in self.settings.instances:
            try:
                await self._tick_instance(instance, force=force)
            except Exception:
                if self.logger is not None:
                    self.logger.exception(
                        "登录守护：实例 %s 探测失败", instance.instance_id
                    )

    async def _tick_instance(self, instance: InstanceConfig, *, force: bool) -> None:
        status = self.statuses.setdefault(
            instance.instance_id, InstanceStatus(instance.instance_id)
        )
        result = await self.probe_manager.probe(instance)
        if result.state is LoginState.NEED_LOGIN and (result.stale or not result.qr_path):
            # 协议端掉登录后不会一直刷新二维码（实测会停），过期就主动让它重新出一张
            refreshed = await self._refresh_qrcode(instance)
            if refreshed is not None:
                result = refreshed
        status.last_probe_ts = result.ts
        status.source = result.source
        status.detail = result.detail
        if result.state == LoginState.UNKNOWN:
            status.unknown_streak += 1
            if (
                status.state is not LoginState.UNKNOWN
                and status.unknown_streak >= self.settings.unknown_confirm_rounds
            ):
                # 连续多轮拿不到有效证据时不能再保留旧状态，否则一个错误的
                # 「需要登录」会永远推那张早已过期的二维码。
                status.state = LoginState.UNKNOWN
                status.last_change_ts = result.ts
                status.qr_path = ""
                status.qr_url = ""
                status.qr_hash = ""
                status.qr_stale = False
                if self.logger is not None:
                    self.logger.info(
                        "登录守护：实例 %s 连续 %d 轮无法判定，状态置为未知",
                        instance.instance_id,
                        status.unknown_streak,
                    )
            return
        status.unknown_streak = 0

        prev = status.state  # 已确认状态
        prev_qr_stale = status.qr_stale
        new = result.state

        status.qr_stale = result.stale
        qr_path = result.qr_path
        if qr_path:
            # 记录协议端的原始路径，WebUI 可直接读实时文件
            status.qr_source_path = qr_path
        if qr_path and not result.stale:
            # 只有新鲜的二维码才另存快照用于发送（防止文件被清理）
            copied = copy_qr(qr_path, self.data_dir / "qr")
            if copied:
                qr_path = copied
        else:
            # 过期二维码不发出去——推一张死码没有意义
            qr_path = ""
        qr_url = result.qr_url if self.settings.send_qr_url else ""
        qr_hash = result.qr_hash

        events: list[GuardEvent] = []
        confirmed = status.state
        if new == LoginState.OFFLINE:
            status.offline_streak += 1
            if confirmed != LoginState.OFFLINE:
                # 状态立即反映事实（状态页与判定保持一致），只有通知需要抖动确认
                status.state = LoginState.OFFLINE
                status.last_change_ts = result.ts
                status.offline_notified = False
            if (
                not status.offline_notified
                and status.offline_streak >= self.settings.offline_confirm_rounds
            ):
                status.offline_notified = True
                events.append(
                    GuardEvent("offline", instance.instance_id, new, ts=result.ts)
                )
        else:
            status.offline_streak = 0
            status.offline_notified = False
            qr_stale = bool(result.stale)
            if new == LoginState.NEED_LOGIN:
                if prev != LoginState.NEED_LOGIN:
                    events.append(
                        GuardEvent(
                            "need_login",
                            instance.instance_id,
                            new,
                            qr_path=qr_path,
                            qr_url=qr_url,
                            qr_hash=qr_hash,
                            qr_stale=qr_stale,
                            ts=result.ts,
                        )
                    )
                elif (
                    self.settings.qr_refresh_notify
                    and qr_hash
                    and qr_hash != status.qr_hash
                ):
                    events.append(
                        GuardEvent(
                            "qr_refreshed",
                            instance.instance_id,
                            new,
                            qr_path=qr_path,
                            qr_url=qr_url,
                            qr_hash=qr_hash,
                            qr_stale=qr_stale,
                            ts=result.ts,
                        )
                    )
                elif qr_stale and not prev_qr_stale:
                    # 二维码从「新鲜」变「过期」：协议端不再刷新，得提醒用户手动触发
                    events.append(
                        GuardEvent(
                            "qr_expired",
                            instance.instance_id,
                            new,
                            qr_path="",
                            qr_url="",
                            qr_hash=qr_hash,
                            qr_stale=True,
                            ts=result.ts,
                        )
                    )
            elif (
                new == LoginState.ONLINE
                and prev in (LoginState.NEED_LOGIN, LoginState.OFFLINE)
                and self.settings.notify_on_recover
            ):
                events.append(
                    GuardEvent("recovered", instance.instance_id, new, ts=result.ts)
                )

        if new != LoginState.OFFLINE:
            if new != confirmed:
                status.last_change_ts = result.ts
            status.state = new
            if new == LoginState.ONLINE:
                status.qr_path = ""
                status.qr_url = ""
                status.qr_hash = ""
            else:
                status.qr_path = qr_path or status.qr_path
                status.qr_url = qr_url or status.qr_url
                status.qr_hash = qr_hash or status.qr_hash

        for event in events:
            if self._should_notify(
                event.instance_id, event.kind, event.qr_hash, force=force
            ):
                await self._dispatch(event, instance)

        await self._retry_pending(instance)

    async def _retry_pending(self, instance: InstanceConfig) -> None:
        """重试此前发送失败的通知（例如平台适配器还没加载完）。"""
        event = self._pending_events.get(instance.instance_id)
        if event is None:
            return
        now = time.time()
        if now - self._pending_at.get(instance.instance_id, 0.0) < self._retry_interval:
            return
        tries = self._pending_tries.get(instance.instance_id, 0) + 1
        if tries > self._retry_limit:
            if self.logger is not None:
                self.logger.warning(
                    "登录守护：通知重试 %d 次仍失败，放弃本轮（%s）",
                    self._retry_limit,
                    event.kind,
                )
            self._pending_events.pop(instance.instance_id, None)
            self._pending_at.pop(instance.instance_id, None)
            self._pending_tries.pop(instance.instance_id, None)
            return
        self._pending_tries[instance.instance_id] = tries
        self._pending_at[instance.instance_id] = now
        if self.logger is not None:
            self.logger.info(
                "登录守护：重试此前失败的通知（第 %d 次，%s）", tries, event.kind
            )
        await self._dispatch(event, instance)

    # ---------------- 主动刷新二维码 ----------------
    async def _refresh_qrcode(
        self, instance: InstanceConfig, *, force: bool = False
    ) -> ProbeResult | None:
        """请求协议端 WebUI 重新生成二维码，成功则返回带新码的探测结果。"""
        if not (instance.http_url and instance.http_token):
            return None
        if not force and not self.settings.auto_refresh_qr:
            return None
        now = time.time()
        if (
            not force
            and now - self._last_qr_refresh.get(instance.instance_id, 0.0)
            < self.settings.qr_refresh_cooldown
        ):
            return None
        self._last_qr_refresh[instance.instance_id] = now
        client = NapCatWebUI(instance.http_url, instance.http_token)
        try:
            qr_url = await client.refresh_qrcode()
        except NapCatWebUIError as exc:
            if self.logger is not None:
                self.logger.warning(
                    "登录守护：请求协议端刷新二维码失败（%s）：%s",
                    instance.instance_id,
                    exc,
                )
            return None
        if self.logger is not None:
            self.logger.info(
                "登录守护：已请求协议端重新生成二维码（实例 %s）", instance.instance_id
            )
        # 等协议端把图片写盘，再读一次
        for _ in range(6):
            await asyncio.sleep(0.5)
            fresh = await self.probe_manager.qrfile.probe(instance)
            if fresh.state is LoginState.NEED_LOGIN:
                return ProbeResult(
                    instance_id=instance.instance_id,
                    state=LoginState.NEED_LOGIN,
                    source="napcat-webui",
                    detail="已请求协议端重新生成二维码",
                    qr_path=fresh.qr_path,
                    qr_url=qr_url or fresh.qr_url,
                    qr_hash=fresh.qr_hash,
                    stale=False,
                    ts=int(time.time()),
                )
        if self.logger is not None:
            self.logger.warning(
                "登录守护：协议端已重新生成二维码，但未读到新的图片文件（检查 qr_path）"
            )
        return None

    async def force_refresh_qr(self, instance_id: str | None = None) -> dict[str, Any]:
        """手动要码：每次都向协议端申请一张**全新**的二维码，再推送。

        为什么不能只重发缓存里那张：扫码被驳回后旧码其实已经失效，但协议端
        写下的文件 mtime 可能还很新，光看「新鲜度」分辨不出来。
        """
        refreshed = False
        for instance in self.settings.instances:
            if instance_id and instance.instance_id != instance_id:
                continue
            if await self._refresh_qrcode(instance, force=True) is not None:
                refreshed = True
        await self.tick()
        sent = await self.resend_qr(instance_id)
        return {"sent": sent, "refreshed": refreshed}

    # ---------------- 去重 ----------------
    def _should_notify(
        self, instance_id: str, kind: str, qr_hash: str, *, force: bool = False
    ) -> bool:
        if force or self.settings.notify_cooldown <= 0:
            return True
        if kind == "qr_refreshed":
            # 二维码刷新是高频事件（协议端每两分钟出一张），按实例节流，
            # 否则会变成每两分钟推一张码的骚扰。
            key = instance_id + ":" + kind
            cooldown = self.settings.qr_refresh_notify_interval
        else:
            key = instance_id + ":" + kind + ":" + (qr_hash or "")
            cooldown = self.settings.notify_cooldown
        now = time.time()
        last = self._last_notify.get(key, 0.0)
        if now - last < cooldown:
            return False
        self._last_notify[key] = now
        return True

    # ---------------- 通知 ----------------
    async def _dispatch(self, event: GuardEvent, instance: InstanceConfig) -> None:
        text = render_text(
            event,
            qr_url=event.qr_url if self.settings.send_qr_url else "",
            instance_label=instance.instance_id,
        )
        records: list[dict[str, Any]] = []
        if self.notifiers:
            results = await asyncio.gather(
                *(self._safe_send(notifier, event, text) for notifier in self.notifiers),
                return_exceptions=True,
            )
            for notifier, outcome in zip(self.notifiers, results, strict=False):
                ok = bool(outcome) if not isinstance(outcome, Exception) else False
                records.append(
                    NotificationRecord(
                        event_kind=event.kind,
                        instance_id=event.instance_id,
                        channel=getattr(notifier, "channel_id", "unknown"),
                        ok=ok,
                        error="" if ok else "send failed",
                        ts=event.ts,
                    ).to_dict()
                )
        self.store.append(event.to_dict(), records)
        delivered = any(item["ok"] for item in records)
        if records and not delivered:
            # 全部渠道都失败：留着重试，否则这一次告警就永远丢了
            self._pending_events[event.instance_id] = event
            self._pending_at.setdefault(event.instance_id, time.time())
        elif delivered:
            self._pending_events.pop(event.instance_id, None)
            self._pending_at.pop(event.instance_id, None)
            self._pending_tries.pop(event.instance_id, None)
        if self.logger is not None:
            self.logger.info(
                "登录守护：事件 %s（%s）已分发，渠道结果=%s",
                event.kind,
                event.instance_id,
                [item["ok"] for item in records] or "无渠道",
            )

    async def _safe_send(self, notifier: Any, event: GuardEvent, text: str) -> bool:
        try:
            return bool(await notifier.send(event, text))
        except Exception as exc:
            if self.logger is not None:
                self.logger.warning(
                    "登录守护：渠道 %s 发送异常：%s",
                    getattr(notifier, "channel_id", "unknown"),
                    exc,
                )
            return False

    # ---------------- 手动操作 ----------------
    async def probe_now(self, instance_id: str | None = None) -> list[dict[str, Any]]:
        await self.tick(force=False)
        items = []
        for key, status in self.statuses.items():
            if instance_id and key != instance_id:
                continue
            items.append(status.to_dict())
        return items

    async def resend_qr(self, instance_id: str | None = None) -> list[dict[str, Any]]:
        """重新推送二维码。

        探测时会把二维码复制一份做快照（防止协议端清理文件），这份快照可能已经
        过期，所以这里先重新探测一次，拿到协议端**当前**的二维码再发送；
        并且只在确实处于「需要登录」时才发，避免推一张已经没用的旧码。
        """
        await self.tick()
        sent: list[dict[str, Any]] = []
        for instance in self.settings.instances:
            if instance_id and instance.instance_id != instance_id:
                continue
            status = self.statuses.get(instance.instance_id)
            if status is None or status.state is not LoginState.NEED_LOGIN:
                continue
            if status.qr_stale:
                # 手上的二维码已经过期，等协议端刷新后会自动推送
                continue
            if not (status.qr_path or status.qr_url):
                continue
            event = GuardEvent(
                "need_login",
                instance.instance_id,
                LoginState.NEED_LOGIN,
                qr_path=status.qr_path,
                qr_url=status.qr_url if self.settings.send_qr_url else "",
                qr_hash=status.qr_hash,
                ts=int(time.time()),
            )
            await self._dispatch(event, instance)
            sent.append(status.to_dict())
        return sent

    async def test_notify(self) -> list[dict[str, Any]]:
        instance = self.settings.instances[0]
        status = self.statuses.get(instance.instance_id)
        event = GuardEvent(
            "test",
            instance.instance_id,
            LoginState.UNKNOWN,
            qr_path=status.qr_path if status else "",
            qr_url=(status.qr_url if status else "") if self.settings.send_qr_url else "",
            ts=int(time.time()),
        )
        await self._dispatch(event, instance)
        return self.store.events(1)

    def snapshot(self) -> dict[str, Any]:
        return {
            "instances": [item.to_dict() for item in self.statuses.values()],
            "channels": [getattr(n, "channel_id", "unknown") for n in self.notifiers],
            "events": self.store.events(50),
            "poll_interval": self.settings.poll_interval,
            "guard_enabled": self.settings.guard_enabled,
        }
