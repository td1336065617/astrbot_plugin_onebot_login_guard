"""协议端路径自动识别测试。"""
from __future__ import annotations

import json
import os
from pathlib import Path

from core.autodetect import (
    _candidate_roots,
    _read_webui,
    discover,
    scan_endpoints,
)


def _make_proc(tmp_path: Path, pid: int, cmdline: str, exe: Path, cwd: Path) -> Path:
    proc = tmp_path / "proc"
    entry = proc / str(pid)
    entry.mkdir(parents=True, exist_ok=True)
    (entry / "cmdline").write_bytes(cmdline.encode("utf-8") + b"\x00--no-sandbox\x00")
    os.symlink(exe, entry / "exe")
    os.symlink(cwd, entry / "cwd")
    return proc


def _make_napcat(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "Napcat"
    qq_dir = root / "opt" / "QQ"
    qq_dir.mkdir(parents=True, exist_ok=True)
    exe = qq_dir / "qq"
    exe.write_bytes(b"")
    qr_dir = qq_dir / "resources" / "app" / "napcat" / "cache"
    qr_dir.mkdir(parents=True, exist_ok=True)
    (qr_dir / "qrcode.png").write_bytes(b"png")
    return root, exe


def test_candidate_roots_walks_up_from_exe_and_cwd():
    roots = _candidate_roots("/a/b/c/qq", "/a/b/c")
    assert "/a/b/c" in roots
    assert "/a/b" in roots
    assert "/a" in roots


def test_scan_endpoints_matches_napcat(tmp_path):
    _root, exe = _make_napcat(tmp_path)
    proc = _make_proc(tmp_path, 1234, "/root/Napcat/opt/QQ/qq", exe, exe.parent)
    endpoints = scan_endpoints(str(proc))
    assert len(endpoints) == 1
    assert endpoints[0].kind == "napcat"
    assert endpoints[0].pid == 1234
    assert str(exe.parent) in endpoints[0].roots


def test_scan_endpoints_ignores_unrelated_process(tmp_path):
    exe = tmp_path / "bash"
    exe.write_bytes(b"")
    proc = _make_proc(tmp_path, 4321, "/usr/bin/bash -l", exe, tmp_path)
    assert scan_endpoints(str(proc)) == []


def test_scan_endpoints_missing_proc_root():
    assert scan_endpoints("/definitely/not/here") == []


def test_discover_finds_qr_via_process(tmp_path):
    _root, exe = _make_napcat(tmp_path)
    proc = _make_proc(tmp_path, 1234, "/root/Napcat/opt/QQ/qq", exe, exe.parent)
    result = discover(proc_root=str(proc))
    assert result.qr_path.endswith("resources/app/napcat/cache/qrcode.png")
    assert result.found is True
    assert any("二维码" in note for note in result.notes)


def test_discover_reads_webui_config(tmp_path):
    _root, exe = _make_napcat(tmp_path)
    cfg_dir = exe.parent / "resources" / "app" / "napcat" / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "webui.json").write_text(
        json.dumps({"host": "0.0.0.0", "port": 6099, "token": "secret"}), encoding="utf-8"
    )
    proc = _make_proc(tmp_path, 1234, "/root/Napcat/opt/QQ/qq", exe, exe.parent)
    result = discover(proc_root=str(proc))
    assert result.http_url == "http://127.0.0.1:6099"
    assert result.http_token == "secret"


def test_read_webui_without_config(tmp_path):
    assert _read_webui([str(tmp_path)]) == ("", "")


def test_discover_without_process_uses_fallback_roots(tmp_path):
    root, _exe = _make_napcat(tmp_path)
    result = discover(proc_root=str(tmp_path / "nope"), extra_roots=[str(root)])
    assert result.qr_path.endswith("qrcode.png")
    assert any("未发现协议端进程" in note for note in result.notes)


def test_discover_reports_missing_paths(tmp_path):
    result = discover(proc_root=str(tmp_path / "nope"), extra_roots=[str(tmp_path)])
    assert result.qr_path == ""
    assert result.log_path == ""
    assert any("未找到二维码" in note for note in result.notes)
