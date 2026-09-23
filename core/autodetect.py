"""自动识别协议端的二维码 / 日志 / WebUI 路径。

思路：协议端进程就在本机时，从 /proc 读出它的可执行文件与工作目录，
按各协议端已知的目录结构探测文件；找不到再退回常见安装根目录。

实测要点：
- NapCat 登录成功后 **不会删除** cache/qrcode.png，因此只能靠文件 mtime
  判断「当前是否在等待扫码」（见 QrFileProbe）。
- NapCat 默认 fileLog=false，多数部署没有日志文件；此时仅靠 qr_path 也能工作。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

#: 进程命令行关键字 → 协议端标识（按顺序匹配）
PROCESS_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("napcat", ("napcat", "opt/qq/qq")),
    ("lagrange", ("lagrange",)),
    ("gocqhttp", ("go-cqhttp", "gocqhttp")),
)

#: 相对「可执行文件目录 / 工作目录 / 安装根目录」的二维码候选路径
QR_CANDIDATES: dict[str, tuple[str, ...]] = {
    "napcat": (
        "resources/app/napcat/cache/qrcode.png",
        "cache/qrcode.png",
        "opt/QQ/resources/app/napcat/cache/qrcode.png",
    ),
    "lagrange": ("qrcode.png", "qr.png", "data/qrcode.png"),
    "gocqhttp": ("qrcode.png", "qr.png"),
}

#: 日志候选路径
LOG_CANDIDATES: dict[str, tuple[str, ...]] = {
    "napcat": ("logs/napcat.log", "logs/*.log", "napcat.log"),
    "lagrange": ("logs/*.log", "logs/latest.log", "log.txt"),
    "gocqhttp": ("logs/*.log", "log.txt"),
}

#: NapCat WebUI 配置相对路径
WEBUI_CANDIDATES = (
    "resources/app/napcat/config/webui.json",
    "opt/QQ/resources/app/napcat/config/webui.json",
)

#: 进程不可见时的兜底搜索根目录
COMMON_ROOTS: tuple[str, ...] = (
    "/root/Napcat",
    "/root/napcat",
    "/opt/NapCat",
    "/opt/napcat",
    "/root/Lagrange",
    "/root/lagrange",
    "/opt/Lagrange",
    "/root/go-cqhttp",
    "/root/gocqhttp",
    "/opt/go-cqhttp",
)


@dataclass
class Endpoint:
    """一个被识别出来的协议端进程。"""

    kind: str
    pid: int
    exe: str = ""
    cwd: str = ""
    roots: list[str] = field(default_factory=list)


@dataclass
class Discovery:
    """一次自动识别的结果。"""

    endpoints: list[Endpoint] = field(default_factory=list)
    qr_path: str = ""
    log_path: str = ""
    http_url: str = ""
    http_token: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return bool(self.qr_path or self.log_path)

    def to_dict(self) -> dict:
        return {
            "endpoints": [
                {"kind": item.kind, "pid": item.pid, "exe": item.exe, "cwd": item.cwd}
                for item in self.endpoints
            ],
            "qr_path": self.qr_path,
            "log_path": self.log_path,
            "http_url": self.http_url,
            "http_token": bool(self.http_token),
            "notes": self.notes,
        }


def _read_text(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _match_kind(cmdline: str) -> str:
    lowered = cmdline.lower()
    for kind, keywords in PROCESS_HINTS:
        for keyword in keywords:
            if keyword in lowered:
                return kind
    return ""


def _is_dir(path: Path) -> bool:
    """is_dir 在权限不足时会抛 PermissionError，这里统一吞掉。"""
    try:
        return path.is_dir()
    except OSError:
        return False


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def _candidate_roots(exe: str, cwd: str) -> list[str]:
    roots: list[str] = []
    for value in (cwd, exe):
        if not value:
            continue
        path = Path(value)
        # 可执行文件本身要退到目录
        if path.suffix or not _is_dir(path):
            path = path.parent
        for _ in range(4):
            text = str(path)
            if text and text not in roots and text != "/":
                roots.append(text)
            if path.parent == path:
                break
            path = path.parent
    return roots


def scan_endpoints(proc_root: str = "/proc") -> list[Endpoint]:
    """扫描本机进程，返回识别到的协议端（非 Linux 返回空列表）。"""
    base = Path(proc_root)
    if not base.is_dir():
        return []
    endpoints: list[Endpoint] = []
    seen: set[tuple[str, str]] = set()
    try:
        entries = list(base.iterdir())
    except OSError:
        return []
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        cmdline = raw.replace(b"\x00", b" ").decode("utf-8", errors="ignore").strip()
        if not cmdline or "--type=" in cmdline:
            # 空命令行 / Electron 子进程（--type=zygote 等）不是协议端主进程
            continue
        kind = _match_kind(cmdline)
        if not kind:
            continue
        try:
            exe = str((entry / "exe").resolve())
        except OSError:
            exe = ""
        try:
            cwd = str((entry / "cwd").resolve())
        except OSError:
            cwd = ""
        if not exe and not cwd:
            # 可执行文件与工作目录都读不到（权限/内核线程），无法定位目录
            continue
        key = (kind, exe or cmdline[:60])
        if key in seen:
            continue
        seen.add(key)
        endpoints.append(
            Endpoint(
                kind=kind,
                pid=int(entry.name),
                exe=exe,
                cwd=cwd,
                roots=_candidate_roots(exe, cwd),
            )
        )
    return endpoints


def _iter_matches(root: str, pattern: str):
    base = Path(root)
    if not _is_dir(base):
        return
    if any(ch in pattern for ch in "*?["):
        try:
            yield from base.glob(pattern)
        except OSError:
            return
        return
    candidate = base / pattern
    if _is_file(candidate):
        yield candidate


def _pick_newest(paths: list[Path]) -> Path | None:
    best: Path | None = None
    best_mtime = -1.0
    for path in paths:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime > best_mtime:
            best, best_mtime = path, mtime
    return best


def _search(kind: str, roots: list[str], patterns: tuple[str, ...]) -> Path | None:
    found: list[Path] = []
    for root in roots:
        for pattern in patterns:
            found.extend(_iter_matches(root, pattern))
    return _pick_newest(found)


def _read_webui(roots: list[str]) -> tuple[str, str]:
    """从 NapCat 的 webui.json 读出 WebUI 地址与 token。"""
    import json

    for root in roots:
        for pattern in WEBUI_CANDIDATES:
            candidate = Path(root) / pattern
            if not _is_file(candidate):
                continue
            try:
                data = json.loads(candidate.read_text(encoding="utf-8", errors="ignore"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            host = str(data.get("host") or "127.0.0.1").strip()
            port = data.get("port")
            token = str(data.get("token") or "").strip()
            if host in {"0.0.0.0", "::"}:
                host = "127.0.0.1"
            if port:
                return "http://" + host + ":" + str(port), token
    return "", ""


def discover(*, proc_root: str = "/proc", extra_roots: list[str] | None = None) -> Discovery:
    """自动识别协议端路径。"""
    result = Discovery()
    endpoints = scan_endpoints(proc_root)
    result.endpoints = endpoints

    roots: list[str] = []
    kinds: list[str] = []
    for endpoint in endpoints:
        if endpoint.kind not in kinds:
            kinds.append(endpoint.kind)
        for root in endpoint.roots:
            if root not in roots:
                roots.append(root)

    for fallback in list(extra_roots or []) + list(COMMON_ROOTS):
        if fallback not in roots:
            roots.append(fallback)

    for kind in kinds or ["napcat", "lagrange", "gocqhttp"]:
        if not result.qr_path:
            qr = _search(kind, roots, QR_CANDIDATES.get(kind, ()))
            if qr is not None:
                result.qr_path = str(qr)
        if not result.log_path:
            log = _search(kind, roots, LOG_CANDIDATES.get(kind, ()))
            if log is not None:
                result.log_path = str(log)

    if "napcat" in kinds or not kinds:
        url, token = _read_webui(roots)
        if url:
            result.http_url = url
            result.http_token = token

    if not endpoints:
        result.notes.append("未发现协议端进程（非 Linux 或进程不可见），已按常见安装目录搜索。")
    if not result.qr_path:
        result.notes.append("未找到二维码图片，请在协议端配置里确认路径后手填。")
    if not result.log_path:
        result.notes.append("未找到日志文件（NapCat 默认关闭文件日志）；仅靠二维码文件也能判断是否需要登录。")
    if result.qr_path:
        try:
            age = int(time.time() - Path(result.qr_path).stat().st_mtime)
            result.notes.append(f"二维码文件最后更新于 {age} 秒前。")
        except OSError:
            pass
    return result
