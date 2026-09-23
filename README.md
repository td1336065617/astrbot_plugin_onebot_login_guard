# OneBot 登录守护（astrbot_plugin_onebot_login_guard）

> NapCat / Lagrange / go-cqhttp 掉登录时，**自动把二维码推给你**。

AstrBot 插件。为非官方 QQ 接入（OneBot v11）提供「掉登录守护」：持续探测协议端登录状态，
一旦掉线或需要重新扫码，立刻把**二维码图片 + 解码链接**推送到 AstrBot 会话、企业微信 / 钉钉 /
飞书 / Telegram / 通用 Webhook 或邮件；扫码恢复后再通知一次，形成闭环。

---

## ✨ 特性

- **掉登录即通知**：日志/二维码文件/平台连接状态三路探测，发现需要扫码立刻推送。
- **二维码送达**：图片随通知发送，并附「二维码解码链接」作为兜底。
- **跨 AstrBot 自救**：通知可发到**另一个还活着的平台**（QQ 官方号 / Telegram / 企微），QQ 挂了也能收到。
- **多协议端多账号**：一个 AstrBot 可守护多个 OneBot 实例，各自独立状态与冷却。
- **多种通知渠道**：AstrBot 会话、企业微信、钉钉、飞书、Telegram、通用 Webhook、SMTP 邮件。
- **不误报**：探测不确定时判「未知」，连续 N 次确认才判掉线；同类通知有冷却窗口。
- **可观测**：WebUI 状态页（实例状态 / 二维码预览 / 事件表）+ 指令 + 事件落盘。

## ⚠️ 边界（重要）

**本插件不能自动扫码登录。** QQ 的安全设计决定了登录态失效后必须人工验证，
任何客户端（含 NapCat）都无法绕过。本插件做的是：把「没人知道掉线了」变成
「几秒内手机收到二维码」。

---

## 📦 安装

### 方式一：插件市场（推荐）
AstrBot WebUI → 插件 → 插件市场 → 搜索「OneBot 登录守护」→ 安装。

### 方式二：手动
把本仓库放到 AstrBot 的 `data/plugins/` 目录下，然后在 WebUI 插件页重载。

依赖：`aiohttp>=3.9.0`（邮件用标准库，无需额外依赖）。

---

## ⚙️ 配置（WebUI → 插件 → OneBot 登录守护）

### 1. 被守护实例（instances）

| 字段 | 说明 |
| --- | --- |
| `instance_id` | 自定义标识（唯一），用于通知与状态展示 |
| `platform_id` | AstrBot 里该 OneBot 平台实例的 ID（如 `default`） |
| `log_path` | 协议端日志路径（**与 AstrBot 同主机时填**，能拿到二维码） |
| `qr_path` | 协议端二维码图片路径（同上） |
| `http_url` / `http_token` | 协议端 WebUI 地址与 token（可选，跨主机时用） |
| `regex_need_login` / `regex_online` | 自定义正则（留空用内置，适配其它协议端） |

**推荐路径（默认值）**

- NapCat：日志 `/root/Napcat/logs/napcat.log`（或服务日志），二维码 `/root/Napcat/opt/QQ/resources/app/napcat/cache/qrcode.png`
- Lagrange：日志与二维码路径按你的部署填写
- go-cqhttp：日志 `logs/xx.log`
- **Windows 示例**：日志 `C:\NapCat\logs\napcat.log`，二维码 `C:\NapCat\opt\QQ\resources\app\napcat\cache\qrcode.png`（按你的实际安装位置调整）

### 2. 通知渠道

- **AstrBot 会话**（`notify_sessions`）：填 `unified_msg_origin` 列表，例如
  `telegram:FriendMessage:123456`、`aiocqhttp:GroupMessage:123456`。
  **强烈建议填一个非 OneBot 的会话**（QQ 官方号 / Telegram），实现跨通道自救。
- **Webhook**（`webhooks`）：类型 `wecom / dingtalk / feishu / telegram / generic`。
  - telegram：`token` 填 Bot Token，`url` 填 chat_id。
- **邮件**（`email`）：SMTP 服务器/端口/加密方式/账号/授权码/收件人，二维码作为附件。

### 3. 行为开关

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `poll_interval` | 30 | 轮询间隔（秒） |
| `notify_cooldown` | 600 | 同类通知冷却（秒） |
| `offline_confirm_rounds` | 2 | 连续几次判掉线才通知 |
| `notify_on_recover` | 开 | 恢复时通知 |
| `qr_refresh_notify` | 开 | 二维码刷新时重发 |
| `send_qr_image` / `send_qr_url` | 开 | 是否发图片 / 附解码链接 |

---

## 🧠 工作原理

```
每 poll_interval 秒
   ├─ LogFileProbe   读协议端日志尾部 → 需要登录 / 登录成功 + 二维码解码 URL
   ├─ QrFileProbe    读二维码图片 → 路径 + md5 指纹（判断是否刷新）
   ├─ HttpProbe      （可选）协议端 WebUI
   └─ AstrBotProbe   平台实例反向 WS 连接状态（跨主机兜底）
        ↓
   状态机：ONLINE / OFFLINE / NEED_LOGIN / UNKNOWN
        ↓  去重（instance:kind:qr_hash）+ 冷却
   通知：AstrBot 会话 / 企微 / 钉钉 / 飞书 / Telegram / 通用 Webhook / 邮件
```

---

## 💬 指令

| 指令 | 权限 | 说明 |
| --- | --- | --- |
| `登录守护状态` | 所有人 | 查看各实例状态与通知渠道 |
| `登录守护二维码` | 管理员 | 重发当前二维码 |
| `登录守护测试` | 管理员 | 向所有渠道发测试通知 |

## 🖥️ WebUI

插件页 →「状态」：实例状态卡片、二维码预览、事件表，以及「立即探测 / 重发二维码 / 测试通知」。

---

## ❓ FAQ

**Q：为什么拿不到二维码？**
A：二维码只能从**协议端日志或二维码文件**读取，因此要求插件与协议端**在同一主机**。
跨主机时只能判断「掉线」，拿不到二维码——请改用 `http_url`（协议端 WebUI）或在协议端所在主机部署本插件。

**Q：会不会自动扫码登录？**
A：不会，也做不到（QQ 安全限制）。插件负责「通知你」，「扫码」仍需人工。

**Q：二维码过期了怎么办？**
A：NapCat 会自动生成新二维码，本插件检测到指纹变化会**重发**（可关 `qr_refresh_notify`）。

**Q：通知发不出去？**
A：如果只配了 AstrBot 会话、而 AstrBot 也挂了，就发不出。建议同时配一个**外部 Webhook**（Telegram / 企微）或邮件。

**Q：会不会误报？**
A：不会。探测不确定判「未知」不通知；掉线需连续 `offline_confirm_rounds` 次确认；同类通知有冷却窗口。

---

## 📄 许可

MIT License，见 `LICENSE`。


## ⚙️ 配置页里的下拉选择

- **被守护实例**：点「添加条目」后，`platform_id` 是**下拉框**（列出 AstrBot 已加载的平台实例）。
- **通知会话**：是**可搜索的多选下拉**，选项来自
  1. 机器人**见过的会话**——让目标会话（如你的 Telegram 私聊）先给机器人发一条消息，它就会出现在下拉里；
  2. AstrBot 会话库里已有的记录。
- 选项由插件在运行时注入，新增平台/新会话后刷新配置页即可看到（无需重启）。
- 需要手填时，仍可直接编辑 `data/config/astrbot_plugin_onebot_login_guard_config.json`。
