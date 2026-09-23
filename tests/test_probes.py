"""探测层测试。"""
from __future__ import annotations

import asyncio
from pathlib import Path

from core.config import InstanceConfig
from core.models import LoginState
from core.probes import LogFileProbe, ProbeManager, QrFileProbe

NAPCAT_LOG = """
09-22 22:44:58 [warn] 请扫描下面的二维码，然后在手Q上授权登录：
09-22 22:44:58 [warn] 二维码已保存到 /tmp/qrcode.png
09-22 22:44:58 [warn] 二维码解码URL: https://txz.qq.com/p?k=abc
"""

ONLINE_LOG = """
09-22 22:44:58 [warn] 请扫描下面的二维码，然后在手Q上授权登录：
09-22 22:45:30 [info] 登录成功
"""


def test_classify_need_login():
    instance = InstanceConfig(instance_id="i1")
    state, _ = LogFileProbe._classify(instance, NAPCAT_LOG)
    assert state is LoginState.NEED_LOGIN


def test_classify_online_wins_when_later():
    instance = InstanceConfig(instance_id="i1")
    state, _ = LogFileProbe._classify(instance, ONLINE_LOG)
    assert state is LoginState.ONLINE


def test_classify_no_info():
    instance = InstanceConfig(instance_id="i1")
    state, _ = LogFileProbe._classify(instance, "随便一条日志")
    assert state is None


def test_logfile_probe(tmp_path: Path):
    log = tmp_path / "napcat.log"
    log.write_text(NAPCAT_LOG, encoding="utf-8")
    instance = InstanceConfig(instance_id="i1", log_path=str(log))
    result = asyncio.run(LogFileProbe().probe(instance))
    assert result.state is LoginState.NEED_LOGIN
    assert result.qr_url == "https://txz.qq.com/p?k=abc"


def test_logfile_probe_missing_path():
    instance = InstanceConfig(instance_id="i1")
    result = asyncio.run(LogFileProbe().probe(instance))
    assert result.state is LoginState.UNKNOWN


def test_qrfile_probe(tmp_path: Path):
    qr = tmp_path / "qrcode.png"
    qr.write_bytes(b"png-bytes")
    instance = InstanceConfig(instance_id="i1", qr_path=str(qr))
    result = asyncio.run(QrFileProbe().probe(instance))
    assert result.qr_path == str(qr)
    assert result.qr_hash


def test_probe_manager_prefers_logfile(tmp_path: Path):
    log = tmp_path / "napcat.log"
    log.write_text(NAPCAT_LOG, encoding="utf-8")
    qr = tmp_path / "qrcode.png"
    qr.write_bytes(b"png-bytes")
    instance = InstanceConfig(
        instance_id="i1", platform_id="nope", log_path=str(log), qr_path=str(qr)
    )
    manager = ProbeManager(None)
    result = asyncio.run(manager.probe(instance))
    assert result.state is LoginState.NEED_LOGIN
    assert result.source == "logfile"
    assert result.qr_path == str(qr)
    assert result.qr_hash
