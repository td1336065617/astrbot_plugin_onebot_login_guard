"""通知层测试。"""
from __future__ import annotations

from core.config import EmailConfig, WebhookConfig
from core.models import GuardEvent, LoginState
from core.notifiers import EmailNotifier, WebhookNotifier, render_text


def make_event(**kwargs) -> GuardEvent:
    params = {
        "kind": "need_login",
        "instance_id": "i1",
        "state": LoginState.NEED_LOGIN,
        "qr_url": "https://txz.qq.com/p?k=abc",
        "ts": 1758500000,
    }
    params.update(kwargs)
    return GuardEvent(**params)


def test_render_text():
    text = render_text(make_event(), qr_url="https://txz.qq.com/p?k=abc")
    assert "OneBot 登录守护" in text
    assert "i1" in text
    assert "需要重新登录" in text
    assert "https://txz.qq.com/p?k=abc" in text


def test_webhook_payload_wecom_text():
    notifier = WebhookNotifier(WebhookConfig(type="wecom", url="https://x/a"))
    url, payload = notifier._payload(make_event(), "hello")
    assert url == "https://x/a"
    assert payload["msgtype"] == "text"
    assert payload["text"]["content"] == "hello"


def test_webhook_payload_dingtalk():
    notifier = WebhookNotifier(
        WebhookConfig(type="dingtalk", url="https://x/b", at_mobiles=["13800000000"])
    )
    _, payload = notifier._payload(make_event(), "hello")
    assert payload["msgtype"] == "markdown"
    assert payload["at"]["atMobiles"] == ["13800000000"]


def test_webhook_payload_feishu():
    notifier = WebhookNotifier(WebhookConfig(type="feishu", url="https://x/c"))
    _, payload = notifier._payload(make_event(), "hello")
    assert payload["msg_type"] == "text"
    assert payload["content"]["text"] == "hello"


def test_webhook_payload_generic():
    notifier = WebhookNotifier(WebhookConfig(type="generic", url="https://x/d"))
    _, payload = notifier._payload(make_event(), "hello")
    assert payload["event"] == "need_login"
    assert payload["instance_id"] == "i1"
    assert payload["state"] == "need_login"


def test_email_message_build(tmp_path):
    qr = tmp_path / "q.png"
    qr.write_bytes(b"png-bytes")
    cfg = EmailConfig(
        enable=True,
        smtp_host="smtp.example.com",
        username="u@example.com",
        to_addrs=["a@example.com"],
    )
    notifier = EmailNotifier(cfg)
    message = notifier._build(make_event(qr_path=str(qr)), "hello")
    assert message["Subject"].startswith("OneBot 登录守护")
    assert message["To"] == "a@example.com"
    assert message.is_multipart()


def test_render_text_need_login_with_stale_qr():
    text = render_text(make_event(qr_stale=True), qr_url="")
    assert "过期" in text
    assert "重新发起登录" in text


def test_render_text_qr_expired():
    text = render_text(make_event(kind="qr_expired", qr_stale=True), qr_url="")
    assert "二维码已经过期" in text
    assert "重新发起登录" in text


def test_webhook_payload_telegram_uses_url_as_chat_id():
    """BUG-047：README 约定 url=chat_id、token=Bot Token，此前 chat_id 会被清空导致发不出去。"""
    notifier = WebhookNotifier(
        WebhookConfig(type="telegram", url="-1001234567890", token="123:ABC")
    )
    url, payload = notifier._payload(make_event(), "hello")
    assert url == "https://api.telegram.org/bot123:ABC/sendMessage"
    assert payload["chat_id"] == "-1001234567890"
    assert payload["text"] == "hello"


def test_webhook_payload_telegram_accepts_full_endpoint():
    """兼容写法：url 直接给完整 API 地址时，token 当 chat_id。"""
    notifier = WebhookNotifier(
        WebhookConfig(type="telegram", url="https://api.telegram.org/bot1:x/sendMessage", token="42")
    )
    url, payload = notifier._payload(make_event(), "hi")
    assert url == "https://api.telegram.org/bot1:x/sendMessage"
    assert payload["chat_id"] == "42"
