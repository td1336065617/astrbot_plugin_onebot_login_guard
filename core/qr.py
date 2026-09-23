"""二维码提取与处理。"""
from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

from .config import QR_URL_PATTERN

_QR_URL_RE = re.compile(QR_URL_PATTERN)
_FALLBACK_URL_RE = re.compile(r"https://txz\.qq\.com/\S+")


def extract_qr_url(text: str) -> str:
    """从协议端日志文本中提取二维码解码 URL。"""
    if not text:
        return ""
    match = _QR_URL_RE.search(text)
    if match:
        return match.group(1).strip()
    fallback = _FALLBACK_URL_RE.search(text)
    return fallback.group(0).strip() if fallback else ""


def read_qr_file(path: str | Path) -> tuple[str, str]:
    """读取二维码图片，返回 (base64, md5)；失败返回 ("", "")。"""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return "", ""
    if not data:
        return "", ""
    return base64.b64encode(data).decode("ascii"), hashlib.md5(data).hexdigest()


def qr_hash_of(path: str | Path, url: str = "") -> str:
    """二维码指纹：优先文件 md5，其次 URL 摘要。"""
    _, digest = read_qr_file(path)
    if digest:
        return digest
    if url:
        return hashlib.md5(url.encode("utf-8")).hexdigest()[:16]
    return ""


def copy_qr(src: str | Path, dst_dir: str | Path) -> str:
    """把二维码复制到插件数据目录，防止协议端清理后发不出去。"""
    source = Path(src)
    if not source.is_file():
        return ""
    try:
        target_dir = Path(dst_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "last_qrcode.png"
        target.write_bytes(source.read_bytes())
        return str(target)
    except OSError:
        return ""
