"""NapCat WebUI 刷新二维码测试（用假的 HTTP 层，不依赖网络）。"""
from __future__ import annotations

import asyncio
import hashlib
import json

from core import napcat_api
from core.napcat_api import NapCatWebUI, NapCatWebUIError

TOKEN = "45b9360f6ea3"


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def text(self) -> str:
        return json.dumps(self._payload)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _FakeSession:
    def __init__(self, recorder, payloads) -> None:
        self._recorder = recorder
        self._payloads = payloads

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def request(self, method, url, headers=None, json=None):
        self._recorder.append((method, url, headers or {}, json or {}))
        payload = self._payloads.pop(0)
        return _FakeResponse(payload)


def _install(monkeypatch_payloads, recorder):
    class _FakeAiohttp:
        class ClientTimeout:
            def __init__(self, total=None):
                self.total = total

        class ClientSession:
            def __init__(self, timeout=None):
                self._session = _FakeSession(recorder, monkeypatch_payloads)

            async def __aenter__(self):
                return self._session

            async def __aexit__(self, *args):
                return False

    napcat_api.aiohttp = _FakeAiohttp


def test_password_hash_matches_napcat_contract():
    client = NapCatWebUI("http://127.0.0.1:6099", TOKEN)
    expected = hashlib.sha256((TOKEN + ".napcat").encode("utf-8")).hexdigest()
    assert client._password_hash() == expected


def test_refresh_qrcode_returns_url():
    recorder = []
    _install(
        [
            {"code": 0, "data": {"Credential": "jwt-1"}},
            {"code": 0, "data": {"qrcodeurl": "https://txz.qq.com/p?k=NEW"}},
        ],
        recorder,
    )
    client = NapCatWebUI("http://127.0.0.1:6099/", TOKEN)
    url = asyncio.run(client.refresh_qrcode())
    assert url == "https://txz.qq.com/p?k=NEW"
    assert recorder[0][1].endswith("/api/auth/login")
    assert recorder[0][3]["hash"] == hashlib.sha256((TOKEN + ".napcat").encode()).hexdigest()
    assert recorder[1][1].endswith("/api/QQLogin/RefreshQRcode")
    assert recorder[1][2]["Authorization"] == "Bearer jwt-1"


def test_refresh_reauthenticates_on_unauthorized():
    recorder = []
    _install(
        [
            {"code": 0, "data": {"Credential": "jwt-1"}},
            {"code": -1, "message": "Unauthorized"},
            {"code": 0, "data": {"Credential": "jwt-2"}},
            {"code": 0, "data": {"qrcodeurl": "https://txz.qq.com/p?k=OK"}},
        ],
        recorder,
    )
    client = NapCatWebUI("http://127.0.0.1:6099", TOKEN)
    assert asyncio.run(client.refresh_qrcode()) == "https://txz.qq.com/p?k=OK"
    assert recorder[-1][2]["Authorization"] == "Bearer jwt-2"


def test_login_failure_raises():
    recorder = []
    _install([{"code": -1, "message": "token is invalid"}], recorder)
    client = NapCatWebUI("http://127.0.0.1:6099", TOKEN)
    try:
        asyncio.run(client.credential())
    except NapCatWebUIError as exc:
        assert "token is invalid" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("应当抛出 NapCatWebUIError")


def test_not_ready_without_token():
    assert NapCatWebUI("http://127.0.0.1:6099", "").ready is False
    assert NapCatWebUI("", "t").ready is False
    assert NapCatWebUI("http://x", "t").ready is True
