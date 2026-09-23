"""配置解析测试。"""
from __future__ import annotations

from core.config import EmailConfig, mask_secrets, parse_settings


def test_defaults_when_empty():
    settings = parse_settings(None)
    assert settings.guard_enabled is True
    assert settings.poll_interval == 30
    assert settings.offline_confirm_rounds == 2
    assert len(settings.instances) == 1
    assert settings.instances[0].instance_id == "default"


def test_parse_instances_and_dedupe():
    raw = {
        "instances": [
            {"instance_id": "a", "platform_id": "p1", "log_path": "/tmp/a.log"},
            {"instance_id": "a", "platform_id": "p2"},
            {"instance_id": "b", "platform_id": "p3", "qr_path": "/tmp/q.png"},
        ]
    }
    settings = parse_settings(raw)
    assert [item.instance_id for item in settings.instances] == ["a", "b"]
    assert settings.instances[0].platform_id == "p1"
    assert settings.instances[1].qr_path == "/tmp/q.png"


def test_clamp_poll_interval():
    assert parse_settings({"poll_interval": 1}).poll_interval == 10
    assert parse_settings({"poll_interval": 99999}).poll_interval == 600
    assert parse_settings({"poll_interval": "abc"}).poll_interval == 30


def test_parse_webhooks_and_email():
    raw = {
        "webhooks": [
            {"type": "wecom", "url": "https://example.com/a"},
            {"type": "generic"},
        ],
        "email": {
            "enable": True,
            "smtp_host": "smtp.example.com",
            "to_addrs": ["a@example.com", "a@example.com", ""],
            "security": "starttls",
        },
        "notify_sessions": ["umo-1", "umo-1", "umo-2"],
    }
    settings = parse_settings(raw)
    assert len(settings.webhooks) == 1
    assert settings.webhooks[0].channel_id == "webhook:wecom"
    assert settings.email.ready is True
    assert settings.email.security == "starttls"
    assert settings.email.to_addrs == ["a@example.com"]
    assert settings.notify_sessions == ["umo-1", "umo-2"]


def test_email_sender_fallback():
    cfg = EmailConfig(username="u@x.com", from_addr="")
    assert cfg.sender == "u@x.com"
    cfg2 = EmailConfig(username="u@x.com", from_addr="n@x.com")
    assert cfg2.sender == "n@x.com"


def test_mask_secrets():
    raw = {
        "instances": [{"instance_id": "a", "http_token": "secret"}],
        "email": {"password": "pwd", "smtp_host": "h"},
        "token": "t",
    }
    masked = mask_secrets(raw)
    assert masked["token"] == "***"
    assert masked["email"]["password"] == "***"
    assert masked["email"]["smtp_host"] == "h"
    assert masked["instances"][0]["http_token"] == "***"
