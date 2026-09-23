# 更新日志

本文件记录所有值得注意的变更，格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

图例：✨ 新增 · 🐛 修复 · ⚙️ 变更

---

## [0.1.2] - 2026-09-23

> ✨ 协议端路径支持自动识别；没有日志文件也能判断「需要登录」。

### ✨ 新增
- 新增 core/autodetect.py：扫描本机协议端进程（/proc 的 exe 与 cwd）与常见安装目录，自动识别 qr_path / log_path，并从 NapCat 的 webui.json 读取 http_url / http_token。
- 新增指令「登录守护识别」、接口 GET /discover 与 POST /apply_discovery；状态页新增「自动识别协议端」按钮。
- 新增配置项 auto_detect（默认开）与 qr_fresh_seconds（默认 300）。
- QrFileProbe 支持「文件新鲜度」判定：实测协议端登录成功后**不会删除**二维码文件，因此按 mtime 判断是否正在等待扫码 —— 只配 qr_path 也能发现掉登录。

### ⚙️ 变更
- 探测优先级：新鲜的二维码文件 > 协议端日志 > WebUI > 平台连接状态。
- 启动时若日志/二维码/WebUI 路径为空，自动识别并回填（只填空字段，不覆盖手填内容）。

---

## [0.1.1] - 2026-09-23

> ✨ 配置页的「实例」与「通知会话」改为下拉选择，不再手输。

### ✨ 新增
- instances 改用 template_list：每个实例渲染为子表单，platform_id 变成**下拉框**（选项 = AstrBot 已加载的平台实例，运行时注入）。
- notify_sessions 变成**可搜索多选下拉**：选项 = 机器人见过的会话 + AstrBot 会话库里的记录。
- 新增「会话观测」：任何平台的消息到达时记录其 unified_msg_origin，作为通知会话候选（让目标会话先给机器人发一条消息即可出现在下拉里）。
- 新增 core/schema_options.py：平台实例枚举、会话枚举、schema 选项注入。

### ⚙️ 变更
- 旧配置里的实例条目会在启动时自动补上 template_list 需要的 __template_key（幂等，只写一次）。

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
