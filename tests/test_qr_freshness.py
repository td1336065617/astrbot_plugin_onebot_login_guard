"""二维码新鲜度判定测试。"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from core.config import InstanceConfig
from core.models import LoginState
from core.probes import LogFileProbe, ProbeManager, QrFileProbe

ONLINE_LOG = "09-23 12:00:00 [info] 登录成功\n"
NAPCAT_LOG = "09-23 12:00:00 [warn] 请扫描下面的二维码\n"


def _age(path: Path, seconds: int) -> None:
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


def test_fresh_qr_means_need_login(tmp_path: Path):
    qr = tmp_path / "qrcode.png"
    qr.write_bytes(b"png")
    instance = InstanceConfig(instance_id="i1", qr_path=str(qr))
    result = asyncio.run(QrFileProbe(fresh_seconds=300).probe(instance))
    assert result.state is LoginState.NEED_LOGIN
    assert "等待扫码" in result.detail


def test_stale_qr_claims_need_login_with_stale_flag(tmp_path: Path):
    """A7 / BUG-041：过期二维码同样是「在等扫码」的信号（好让守护主动重新出码），

    但必须带 stale=True —— 上层据此不发死码（见 test_stale_evidence 的两条用例）。
    """
    qr = tmp_path / "qrcode.png"
    qr.write_bytes(b"png")
    _age(qr, 3600)
    instance = InstanceConfig(instance_id="i1", qr_path=str(qr))
    result = asyncio.run(QrFileProbe(fresh_seconds=300).probe(instance))
    assert result.state is LoginState.NEED_LOGIN
    assert result.stale is True
    assert result.qr_path == str(qr)


def test_fresh_qr_overrides_online_log(tmp_path: Path):
    log = tmp_path / "napcat.log"
    log.write_text(ONLINE_LOG, encoding="utf-8")
    qr = tmp_path / "qrcode.png"
    qr.write_bytes(b"png")
    instance = InstanceConfig(
        instance_id="i1", platform_id="nope", log_path=str(log), qr_path=str(qr)
    )
    manager = ProbeManager(None, qr_fresh_seconds=300)
    result = asyncio.run(manager.probe(instance))
    assert result.state is LoginState.NEED_LOGIN
    assert result.source == "qrfile"


def test_stale_qr_falls_back_to_log(tmp_path: Path):
    log = tmp_path / "napcat.log"
    log.write_text(ONLINE_LOG, encoding="utf-8")
    qr = tmp_path / "qrcode.png"
    qr.write_bytes(b"png")
    _age(qr, 3600)
    instance = InstanceConfig(
        instance_id="i1", platform_id="nope", log_path=str(log), qr_path=str(qr)
    )
    manager = ProbeManager(None, qr_fresh_seconds=300)
    result = asyncio.run(manager.probe(instance))
    assert result.state is LoginState.ONLINE
    assert result.source == "logfile"


def test_log_only_still_detects_need_login(tmp_path: Path):
    log = tmp_path / "napcat.log"
    log.write_text(NAPCAT_LOG, encoding="utf-8")
    instance = InstanceConfig(instance_id="i1", platform_id="nope", log_path=str(log))
    manager = ProbeManager(None, qr_fresh_seconds=300)
    result = asyncio.run(manager.probe(instance))
    assert result.state is LoginState.NEED_LOGIN
    assert result.source == "logfile"
    assert asyncio.run(LogFileProbe().probe(instance)).state is LoginState.NEED_LOGIN
