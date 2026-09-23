"""通知失败重试测试。

插件可能在平台适配器加载完成之前就探测到事件，此时发送必然失败；
若这次告警直接丢掉，用户就永远收不到（need_login 只在状态跳变时发一次）。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from test_guard import build_guard, pr

from core.models import LoginState


class FlakyNotifier:
    channel_id = "flaky"

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.attempts = 0

    async def send(self, event, text) -> bool:
        self.attempts += 1
        return self.attempts > self.failures


def test_failed_notification_is_retried(tmp_path: Path):
    results = [pr(LoginState.NEED_LOGIN, qr_hash="h1", qr_url="https://x/1")]
    guard, _notifier = build_guard(tmp_path, results)
    flaky = FlakyNotifier(failures=1)
    guard.notifiers = [flaky]
    guard._retry_interval = 3600.0  # 同一轮内不重试

    asyncio.run(guard.tick())
    assert flaky.attempts == 1
    assert guard._pending_events  # 已挂起，等待重试

    guard._retry_interval = 0.0  # 下一轮允许重试
    asyncio.run(guard.tick())
    assert flaky.attempts == 2
    assert not guard._pending_events  # 成功后清空

    asyncio.run(guard.tick())
    assert flaky.attempts == 2  # 成功后不再重试


def test_retry_gives_up_after_limit(tmp_path: Path):
    results = [pr(LoginState.NEED_LOGIN, qr_hash="h1")]
    guard, _notifier = build_guard(tmp_path, results)
    flaky = FlakyNotifier(failures=99)
    guard.notifiers = [flaky]
    guard._retry_interval = 0.0
    guard._retry_limit = 2

    asyncio.run(guard.tick())
    asyncio.run(guard.tick())
    asyncio.run(guard.tick())
    asyncio.run(guard.tick())
    assert flaky.attempts == 3  # 首次 + 2 次重试
