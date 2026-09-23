"""二维码处理测试。"""
from __future__ import annotations

from pathlib import Path

from core.qr import copy_qr, extract_qr_url, qr_hash_of, read_qr_file

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000a49444154789c6360000002000100ffff03000006000557bfabd4"
    "0000000049454e44ae426082"
)


def test_extract_qr_url_napcat():
    text = "09-22 22:44:58 [warn] 二维码解码URL: https://txz.qq.com/p?k=abc&f=1\n"
    assert extract_qr_url(text) == "https://txz.qq.com/p?k=abc&f=1"


def test_extract_qr_url_fallback():
    text = "请访问 https://txz.qq.com/p?k=xyz 完成登录"
    assert extract_qr_url(text) == "https://txz.qq.com/p?k=xyz"


def test_extract_qr_url_empty():
    assert extract_qr_url("") == ""
    assert extract_qr_url("nothing here") == ""


def test_read_qr_file(tmp_path: Path):
    target = tmp_path / "q.png"
    target.write_bytes(PNG)
    body, digest = read_qr_file(target)
    assert body and digest
    assert qr_hash_of(target) == digest


def test_read_qr_file_missing(tmp_path: Path):
    assert read_qr_file(tmp_path / "nope.png") == ("", "")


def test_copy_qr(tmp_path: Path):
    src = tmp_path / "src.png"
    src.write_bytes(PNG)
    copied = copy_qr(src, tmp_path / "data")
    assert copied and Path(copied).is_file()
    assert copy_qr(tmp_path / "nope.png", tmp_path / "data") == ""
