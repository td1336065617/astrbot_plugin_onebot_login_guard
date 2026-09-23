"""NapCat WebUI API 客户端：请求协议端重新生成登录二维码。

实测（NapCat 4.x）：
    POST /api/auth/login          body {"hash": SHA256(token + ".napcat")}
                                  -> {"code":0,"data":{"Credential":"<JWT>"}}
    POST /api/QQLogin/RefreshQRcode   -> {"code":0,"data":{"qrcodeurl":"https://txz.qq.com/p?k=..."}}
    POST /api/QQLogin/CheckLoginStatus -> {"code":0,"data":{"isLogin":false,"loginPhase":"waiting_qrcode",...}}

协议端掉登录后只会生成一两次二维码就不再刷新（实测 4 小时无动作），
所以「过期时主动让它重新出一张」是唯一能拿到有效码的办法。
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

try:  # aiohttp 由插件依赖提供；导入失败时给出明确错误
    import aiohttp
except ImportError:  # pragma: no cover
    aiohttp = None  # type: ignore[assignment]

AUTH_PATH = "/api/auth/login"
REFRESH_PATH = "/api/QQLogin/RefreshQRcode"
STATUS_PATH = "/api/QQLogin/CheckLoginStatus"

#: NapCat 约定的口令盐
TOKEN_SALT = ".napcat"


class NapCatWebUIError(RuntimeError):
    """调用协议端 WebUI 失败。"""


class NapCatWebUI:
    """NapCat WebUI 的最小客户端。"""

    def __init__(self, base_url: str, token: str, *, timeout: float = 10.0) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        self.token = str(token or "").strip()
        self.timeout = timeout
        self._credential = ""
        self._credential_at = 0.0

    @property
    def ready(self) -> bool:
        return bool(self.base_url and self.token)

    def _password_hash(self) -> str:
        raw = (self.token + TOKEN_SALT).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        credential: str = "",
    ) -> dict[str, Any]:
        if aiohttp is None:  # pragma: no cover - 依赖缺失
            raise NapCatWebUIError("未安装 aiohttp")
        headers = {"Content-Type": "application/json"}
        if credential:
            headers["Authorization"] = "Bearer " + credential
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        url = self.base_url + path
        try:
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.request(method, url, headers=headers, json=body or {}) as response,
            ):
                text = await response.text()
        except Exception as exc:
            raise NapCatWebUIError(f"请求 {url} 失败：{exc}") from exc
        try:
            payload = json.loads(text)
        except ValueError as exc:
            raise NapCatWebUIError(f"{url} 返回的不是 JSON：{text[:120]}") from exc
        if not isinstance(payload, dict):
            raise NapCatWebUIError(f"{url} 返回格式异常")
        return payload

    async def credential(self, *, force: bool = False) -> str:
        """取 JWT（缓存 10 分钟）。"""
        now = time.time()
        if not force and self._credential and now - self._credential_at < 600:
            return self._credential
        payload = await self._request(
            "POST", AUTH_PATH, body={"hash": self._password_hash()}
        )
        data = payload.get("data")
        token = ""
        if isinstance(data, dict):
            token = str(data.get("Credential") or data.get("credential") or "")
        elif isinstance(data, str):
            token = data
        if not token:
            raise NapCatWebUIError(
                "登录协议端 WebUI 失败：" + str(payload.get("message") or payload)[:120]
            )
        self._credential = token
        self._credential_at = now
        return token

    async def _authed(self, path: str, *, body: dict[str, Any] | None = None) -> dict[str, Any]:
        token = await self.credential()
        payload = await self._request("POST", path, body=body, credential=token)
        if payload.get("code") == 0:
            return payload
        # 凭证过期就重登一次
        token = await self.credential(force=True)
        payload = await self._request("POST", path, body=body, credential=token)
        if payload.get("code") != 0:
            raise NapCatWebUIError(str(payload.get("message") or payload)[:120])
        return payload

    async def refresh_qrcode(self) -> str:
        """请求协议端重新生成二维码，返回解码 URL。"""
        payload = await self._authed(REFRESH_PATH)
        data = payload.get("data")
        if isinstance(data, dict):
            return str(data.get("qrcodeurl") or "")
        return ""

    async def login_status(self) -> dict[str, Any]:
        payload = await self._authed(STATUS_PATH)
        data = payload.get("data")
        return data if isinstance(data, dict) else {}
