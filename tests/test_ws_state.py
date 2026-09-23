"""反向 WS 断开时的状态判定测试。

NapCat 掉登录后不会连反向 WS，但它仍在运行；而进程挂掉则是另一种情况。
两者都必须能区分出来，否则要么误报「进程没了」，要么在真掉线时闭嘴。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from test_guard import build_guard, pr

from core.config import InstanceConfig
from core.models import LoginState, ProbeResult
from core.probes import ProbeManager


class _FakeAstrBotProbe:
    name = "astrbot"

    def __init__(self, state: LoginState) -> None:
        self._state = state

    async def probe(self, instance):
        return ProbeResult(
            instance_id=instance.instance_id,
            state=self._state,
            source=self.name,
            detail="反向 WebSocket 无客户端连接",
            ts=0,
        )


class _FakeProcessProbe:
    name = "process"

    def __init__(self, alive: bool) -> None:
        self._alive = alive

    async def alive(self) -> bool:
        return self._alive


def _manager(astrbot_state: LoginState, process_alive: bool) -> ProbeManager:
    manager = ProbeManager(None)
    manager.astrbot = _FakeAstrBotProbe(astrbot_state)
    manager.process = _FakeProcessProbe(process_alive)
    return manager


def test_ws_down_with_process_alive_means_need_login():
    manager = _manager(LoginState.OFFLINE, True)
    result = asyncio.run(manager.probe(InstanceConfig(instance_id="i1")))
    assert result.state is LoginState.NEED_LOGIN
    assert result.source == "process"
    assert "等待扫码" in result.detail


def test_ws_down_without_process_means_offline():
    manager = _manager(LoginState.OFFLINE, False)
    result = asyncio.run(manager.probe(InstanceConfig(instance_id="i1")))
    assert result.state is LoginState.OFFLINE
    assert result.source == "astrbot"


def test_ws_connected_wins_over_stale_qr(tmp_path: Path):
    qr = tmp_path / "qrcode.png"
    qr.write_bytes(b"OLD")
    manager = _manager(LoginState.ONLINE, True)
    instance = InstanceConfig(instance_id="i1", qr_path=str(qr))
    result = asyncio.run(manager.probe(instance))
    assert result.state is LoginState.ONLINE


def test_qr_expired_notified_once(tmp_path: Path):
    source = tmp_path / "qrcode.png"
    source.write_bytes(b"OLD")
    results = [
        pr(LoginState.NEED_LOGIN, qr_hash="h1", qr_path=str(source), stale=False),
        pr(LoginState.NEED_LOGIN, qr_hash="h1", qr_path=str(source), stale=True),
        pr(LoginState.NEED_LOGIN, qr_hash="h1", qr_path=str(source), stale=True),
    ]
    guard, notifier = build_guard(tmp_path, results, notify_cooldown=0)
    for _ in range(3):
        asyncio.run(guard.tick())
    assert notifier.sent == ["need_login", "qr_expired"]


def test_offline_state_reflects_immediately(tmp_path: Path):
    """状态要立刻反映事实，只有通知需要抖动确认。"""
    results = [pr(LoginState.OFFLINE), pr(LoginState.OFFLINE)]
    guard, notifier = build_guard(tmp_path, results, offline_confirm_rounds=2)
    asyncio.run(guard.tick())
    assert guard.statuses["i1"].state is LoginState.OFFLINE
    assert notifier.sent == []
    asyncio.run(guard.tick())
    assert notifier.sent == ["offline"]
