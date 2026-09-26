"""协议端自报源 / 僵尸 WS / 过期二维码 的判定测试（BUG-041）。"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from test_guard import build_guard, pr

import core.probes as probes_mod
from core.config import InstanceConfig
from core.models import LoginState, ProbeResult
from core.probes import AstrBotProbe, ProbeManager, ProtocolStatusProbe, QrFileProbe


def _fake_webui(payload, *, raise_exc: Exception | None = None):
    class _FakeWebUI:
        def __init__(self, base_url: str, token: str, *, timeout: float = 8.0) -> None:
            self.base_url = str(base_url or "")
            self.token = str(token or "")
            self.timeout = timeout
            self.ready = bool(self.base_url and self.token)

        async def login_status(self):
            if raise_exc is not None:
                raise raise_exc
            return payload

    return _FakeWebUI


def _instance(**kwargs) -> InstanceConfig:
    base = {"instance_id": "i1", "http_url": "http://127.0.0.1:6099", "http_token": "tok"}
    base.update(kwargs)
    return InstanceConfig(**base)


def test_protocol_probe_online(monkeypatch):
    monkeypatch.setattr(probes_mod, "NapCatWebUI", _fake_webui({"isLogin": True}))
    result = asyncio.run(ProtocolStatusProbe().probe(_instance()))
    assert result.state is LoginState.ONLINE


def test_protocol_probe_waiting_qrcode(monkeypatch):
    monkeypatch.setattr(
        probes_mod,
        "NapCatWebUI",
        _fake_webui(
            {
                "isLogin": False,
                "isOffline": True,
                "loginPhase": "waiting_qrcode",
                "qrcodeurl": "https://txz.qq.com/p?k=abc&f=1600001615",
            }
        ),
    )
    result = asyncio.run(ProtocolStatusProbe().probe(_instance()))
    assert result.state is LoginState.NEED_LOGIN
    assert result.qr_url == "https://txz.qq.com/p?k=abc&f=1600001615"


def test_protocol_probe_offline_with_reason(monkeypatch):
    monkeypatch.setattr(
        probes_mod,
        "NapCatWebUI",
        _fake_webui(
            {
                "isLogin": False,
                "isOffline": True,
                "loginPhase": "waiting_qrcode_failed",
                "loginError": "当前账号(123)已登录,无法重复登录",
            }
        ),
    )
    result = asyncio.run(ProtocolStatusProbe().probe(_instance()))
    assert result.state is LoginState.OFFLINE
    assert "无法重复登录" in result.detail


def test_protocol_probe_error_is_unknown(monkeypatch):
    monkeypatch.setattr(
        probes_mod, "NapCatWebUI", _fake_webui({}, raise_exc=RuntimeError("boom"))
    )
    result = asyncio.run(ProtocolStatusProbe().probe(_instance()))
    assert result.state is LoginState.UNKNOWN


def test_protocol_probe_without_config_is_unknown():
    result = asyncio.run(ProtocolStatusProbe().probe(InstanceConfig(instance_id="i1")))
    assert result.state is LoginState.UNKNOWN


class _FakeProtocol:
    name = "napcat-webui"

    def __init__(self, state: LoginState) -> None:
        self._state = state

    async def probe(self, instance):
        return ProbeResult(
            instance_id=instance.instance_id,
            state=self._state,
            source=self.name,
            detail="协议端自报",
            ts=0,
        )


class _FakeAstrBot:
    name = "astrbot"

    def __init__(self, state: LoginState) -> None:
        self._state = state

    async def probe(self, instance):
        return ProbeResult(
            instance_id=instance.instance_id,
            state=self._state,
            source=self.name,
            detail="反向 WS",
            ts=0,
        )


def test_manager_prefers_protocol_over_astrbot():
    manager = ProbeManager(None)
    manager.protocol = _FakeProtocol(LoginState.ONLINE)
    manager.astrbot = _FakeAstrBot(LoginState.OFFLINE)
    result = asyncio.run(manager.probe(InstanceConfig(instance_id="i1")))
    assert result.state is LoginState.ONLINE
    assert result.source == "napcat-webui"


def test_qrfile_stale_becomes_need_login(tmp_path: Path):
    qr = tmp_path / "qrcode.png"
    qr.write_bytes(b"png-bytes")
    stale = time.time() - 3600
    os.utime(qr, (stale, stale))
    result = asyncio.run(QrFileProbe().probe(InstanceConfig(instance_id="i1", qr_path=str(qr))))
    assert result.state is LoginState.NEED_LOGIN
    assert result.stale is True


def test_manager_falls_back_to_stale_qr(tmp_path: Path):
    qr = tmp_path / "qrcode.png"
    qr.write_bytes(b"png-bytes")
    stale = time.time() - 3600
    os.utime(qr, (stale, stale))
    manager = ProbeManager(None)
    result = asyncio.run(
        manager.probe(InstanceConfig(instance_id="i1", platform_id="nope", qr_path=str(qr)))
    )
    assert result.state is LoginState.NEED_LOGIN
    assert result.stale is True
    assert result.source == "qrfile"


class _FakePlatform:
    class _Meta:
        name = "aiocqhttp"

    class _Status:
        value = "running"

    def __init__(self, *, action_raises: bool) -> None:
        self._action_raises = action_raises
        self.bot = self._Bot(action_raises)
        self.status = _FakePlatform._Status()

    class _Bot:
        def __init__(self, raises: bool) -> None:
            self._raises = raises
            self._wsr_api_clients = {"c": object()}
            self._wsr_event_clients = set()

        async def call_action(self, action: str, **params):
            if self._raises:
                raise RuntimeError("ws dead")
            return {"user_id": 1482759239}

    def meta(self):
        return _FakePlatform._Meta()


class _FakeContext:
    def __init__(self, platform) -> None:
        self._platform = platform

    def get_platform_inst(self, platform_id: str):
        return self._platform


def test_astrbot_probe_zombie_ws_is_offline():
    probe = AstrBotProbe(_FakeContext(_FakePlatform(action_raises=True)))
    result = asyncio.run(probe.probe(InstanceConfig(instance_id="i1", platform_id="p1")))
    assert result.state is LoginState.OFFLINE
    assert "僵尸" in result.detail


def test_stale_only_evidence_warns_without_image(tmp_path: Path):
    """A7：唯一证据是过期二维码时，要提醒（好触发重新出码），但不得附上死码。"""
    source = tmp_path / "qrcode.png"
    source.write_bytes(b"OLD")
    results = [pr(LoginState.NEED_LOGIN, qr_hash="h1", qr_path=str(source), stale=True)]
    guard, notifier = build_guard(tmp_path, results)
    asyncio.run(guard.tick())
    assert guard.statuses["i1"].state is LoginState.NEED_LOGIN
    assert guard.statuses["i1"].qr_stale is True
    assert notifier.qr_contents[-1] == b""
    assert "need_login" in notifier.sent


def test_astrbot_probe_live_ws_is_online():
    probe = AstrBotProbe(_FakeContext(_FakePlatform(action_raises=False)))
    result = asyncio.run(probe.probe(InstanceConfig(instance_id="i1", platform_id="p1")))
    assert result.state is LoginState.ONLINE
    assert "往返正常" in result.detail
