"""设计修正回归：过期证据不得用于判定当前状态。

线上事故：NapCat 上一次崩溃留下的死日志里有「请扫描下面的二维码」，
插件把这条历史记录当成当前状态，于是永远认为「需要登录」，
并一直推送那张早已过期的 qrcode.png。
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from test_guard import build_guard, pr

from core.config import InstanceConfig
from core.models import LoginState
from core.probes import LogFileProbe, ProbeManager

NEED_LOGIN_LINE = "09-22 18:59:08 [warn] 请扫描下面的二维码，然后在手Q上授权登录：\n"


def _age(path: Path, seconds: int) -> None:
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


def test_stale_log_is_ignored(tmp_path: Path):
    log = tmp_path / "napcat.log"
    log.write_text(NEED_LOGIN_LINE, encoding="utf-8")
    _age(log, 86400)
    instance = InstanceConfig(instance_id="i1", log_path=str(log))
    result = asyncio.run(LogFileProbe(fresh_seconds=900).probe(instance))
    assert result.state is LoginState.UNKNOWN
    assert result.stale is True
    assert "未更新" in result.detail


def test_fresh_log_is_used(tmp_path: Path):
    log = tmp_path / "napcat.log"
    log.write_text(NEED_LOGIN_LINE, encoding="utf-8")
    instance = InstanceConfig(instance_id="i1", log_path=str(log))
    result = asyncio.run(LogFileProbe(fresh_seconds=900).probe(instance))
    assert result.state is LoginState.NEED_LOGIN
    assert result.stale is False


def test_dead_log_plus_stale_qr_need_login_but_stale(tmp_path: Path):
    """死日志 + 过期二维码：死日志依旧不参与判定；

    A7 / BUG-041 起，二维码过期也算「在等扫码」的信号（好让守护主动重新出码），
    但必须带 stale=True，且下游不得把死码当有效码发出去。
    """
    log = tmp_path / "napcat.log"
    log.write_text(NEED_LOGIN_LINE, encoding="utf-8")
    _age(log, 86400)
    qr = tmp_path / "qrcode.png"
    qr.write_bytes(b"png")
    _age(qr, 3600)
    instance = InstanceConfig(
        instance_id="i1", platform_id="nope", log_path=str(log), qr_path=str(qr)
    )
    manager = ProbeManager(None, qr_fresh_seconds=300, log_fresh_seconds=900)
    assert asyncio.run(LogFileProbe(fresh_seconds=900).probe(instance)).state is LoginState.UNKNOWN
    result = asyncio.run(manager.probe(instance))
    assert result.state is LoginState.NEED_LOGIN
    assert result.stale is True
    assert result.source == "qrfile"


def test_stale_qr_is_not_attached_to_notification(tmp_path: Path):
    source = tmp_path / "qrcode.png"
    source.write_bytes(b"OLD")
    results = [pr(LoginState.NEED_LOGIN, qr_hash="h1", qr_path=str(source), stale=True)]
    guard, notifier = build_guard(tmp_path, results)
    asyncio.run(guard.tick())
    assert guard.statuses["i1"].qr_stale is True
    # 过期二维码不应作为图片发出
    assert notifier.qr_contents[-1] == b""


def test_resend_refuses_stale_qr(tmp_path: Path):
    source = tmp_path / "qrcode.png"
    source.write_bytes(b"OLD")
    results = [pr(LoginState.NEED_LOGIN, qr_hash="h1", qr_path=str(source), stale=True)]
    guard, notifier = build_guard(tmp_path, results)
    asyncio.run(guard.tick())
    notifier.sent.clear()
    assert asyncio.run(guard.resend_qr()) == []
    assert notifier.sent == []


def test_unknown_demotes_stale_need_login(tmp_path: Path):
    """连续拿不到有效证据后，错误的「需要登录」必须被降级，不能永远推旧码。"""
    results = [
        pr(LoginState.NEED_LOGIN, qr_hash="h1", qr_url="https://x/1"),
        pr(LoginState.UNKNOWN),
        pr(LoginState.UNKNOWN),
        pr(LoginState.UNKNOWN),
    ]
    guard, _notifier = build_guard(tmp_path, results, unknown_confirm_rounds=3)
    asyncio.run(guard.tick())
    assert guard.statuses["i1"].state is LoginState.NEED_LOGIN
    for _ in range(3):
        asyncio.run(guard.tick())
    status = guard.statuses["i1"]
    assert status.state is LoginState.UNKNOWN
    assert status.qr_path == ""
    assert status.qr_url == ""
