# 更新日志

本文件记录所有值得注意的变更，格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

图例：✨ 新增 · 🐛 修复 · ⚙️ 变更

---

## [0.1.0] - 2026-09-23

首个版本。

### ✨ 新增
- 四路探测：协议端日志、二维码文件、协议端 WebUI（可选）、AstrBot 平台连接状态。
- 状态机：ONLINE / OFFLINE / NEED_LOGIN / UNKNOWN，含掉线确认与去重冷却。
- 通知渠道：AstrBot 会话、企业微信、钉钉、飞书、Telegram、通用 Webhook、SMTP 邮件。
- 多协议端多账号：instances 列表，各自独立探测源、状态与冷却。
- WebUI 状态页 + REST 接口（status / probe / resend_qr / test_notify / events / qr / qr_data）。
- 指令：登录守护状态 / 登录守护二维码 / 登录守护测试。
- 事件落盘（events.jsonl）与二维码副本保存。
