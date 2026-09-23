"""状态机测试。"""
from __future__ import annotations

import asyncio
import time

from core.config import GuardSettings, InstanceConfig
from core.guard import Guard
from core.models import LoginState, ProbeResult


def pr(
    state: LoginState,
    *,
    qr_hash: str = "",
    qr_url: str = "",
    qr_path: str = "",
    stale: bool = False,
) -> ProbeResult:
    return ProbeResult(
        instance_id="i1",
        state=state,
        source="fake",
        detail=state.value,
        qr_path=qr_path,
        qr_url=qr_url,
        qr_hash=qr_hash,
        stale=stale,
        ts=int(time.time()),
    )


class FakeProbeManager:
    def __init__(self, results):
        self.results = list(results)
        self.index = 0

    async def probe(self, instance):
        if self.index < len(self.results):
            item = self.results[self.index]
            self.index += 1
            return item
        return self.results[-1]


class FakeNotifier:
    channel_id = "fake"

    def __init__(self):
        self.sent = []
        self.qr_contents = []

    async def send(self, event, text):
        self.sent.append(event.kind)
        self.qr_contents.append(_read_bytes(event.qr_path))
        return True


def _read_bytes(path):
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except (OSError, TypeError):
        return b""


def build_guard(tmp_path, results, **kwargs):
    settings = GuardSettings(
        instances=[InstanceConfig(instance_id="i1")],
        notify_cooldown=kwargs.pop("notify_cooldown", 0),
        **kwargs,
    )
    guard = Guard(settings, None, tmp_path)
    notifier = FakeNotifier()
    guard.notifiers = [notifier]
    guard.probe_manager = FakeProbeManager(results)
    return guard, notifier


def test_full_lifecycle(tmp_path):
    results = [
        pr(LoginState.ONLINE),
        pr(LoginState.NEED_LOGIN, qr_hash="h1", qr_url="https://x/1"),
        pr(LoginState.NEED_LOGIN, qr_hash="h1", qr_url="https://x/1"),
        pr(LoginState.NEED_LOGIN, qr_hash="h2", qr_url="https://x/2"),
        pr(LoginState.ONLINE),
    ]
    guard, notifier = build_guard(tmp_path, results)
    for _ in range(5):
        asyncio.run(guard.tick())
    assert notifier.sent == ["need_login", "qr_refreshed", "recovered"]
    assert guard.statuses["i1"].state is LoginState.ONLINE


def test_offline_confirm_rounds(tmp_path):
    results = [pr(LoginState.ONLINE), pr(LoginState.OFFLINE), pr(LoginState.OFFLINE)]
    guard, notifier = build_guard(tmp_path, results, offline_confirm_rounds=2)
    for _ in range(3):
        asyncio.run(guard.tick())
    assert notifier.sent == ["offline"]


def test_cooldown_blocks_repeat(tmp_path):
    results = [
        pr(LoginState.NEED_LOGIN, qr_hash="h1"),
        pr(LoginState.ONLINE),
        pr(LoginState.NEED_LOGIN, qr_hash="h1"),
    ]
    guard, notifier = build_guard(tmp_path, results, notify_cooldown=3600)
    for _ in range(3):
        asyncio.run(guard.tick())
    # 冷却只应拦住重复的 need_login，recovered 是另一类事件不受影响
    assert notifier.sent.count("need_login") == 1
    assert notifier.sent == ["need_login", "recovered"]


def test_unknown_does_not_flap(tmp_path):
    results = [pr(LoginState.ONLINE), pr(LoginState.UNKNOWN), pr(LoginState.UNKNOWN)]
    guard, notifier = build_guard(tmp_path, results)
    for _ in range(3):
        asyncio.run(guard.tick())
    assert guard.statuses["i1"].state is LoginState.ONLINE
    assert notifier.sent == []


def test_resend_qr_and_test_notify(tmp_path):
    results = [pr(LoginState.NEED_LOGIN, qr_hash="h1", qr_url="https://x/1")]
    guard, notifier = build_guard(tmp_path, results)
    asyncio.run(guard.tick())
    notifier.sent.clear()
    sent = asyncio.run(guard.resend_qr())
    assert sent and notifier.sent == ["need_login"]
    asyncio.run(guard.test_notify())
    assert "test" in notifier.sent

def test_resend_qr_sends_fresh_file(tmp_path):
    """协议端刷新二维码后，重发指令必须发最新的那一张。"""
    source = tmp_path / "qrcode.png"
    source.write_bytes(b"OLD")
    results = [pr(LoginState.NEED_LOGIN, qr_hash="h1", qr_path=str(source))]
    guard, notifier = build_guard(tmp_path, results)
    asyncio.run(guard.tick())
    assert notifier.qr_contents[-1] == b"OLD"

    # 协议端重新生成了二维码
    source.write_bytes(b"NEW")
    notifier.qr_contents.clear()
    sent = asyncio.run(guard.resend_qr())
    assert sent
    assert notifier.qr_contents[-1] == b"NEW"


def test_resend_qr_refuses_when_online(tmp_path):
    """已经恢复登录时不应该再推旧二维码。"""
    source = tmp_path / "qrcode.png"
    source.write_bytes(b"OLD")
    results = [
        pr(LoginState.NEED_LOGIN, qr_hash="h1", qr_path=str(source)),
        pr(LoginState.ONLINE),
    ]
    guard, _notifier = build_guard(tmp_path, results)
    asyncio.run(guard.tick())
    sent = asyncio.run(guard.resend_qr())
    assert sent == []

