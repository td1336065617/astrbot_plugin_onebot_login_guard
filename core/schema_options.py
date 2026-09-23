"""把「平台实例」与「会话」做成配置页里的下拉框。

AstrBot 的 _conf_schema.json 是静态 JSON，编写期无法枚举运行时的平台实例与会话。
但仪表盘每次打开配置页都会重新读取内存里的 schema（plugin_md.config.schema），
因此插件可以在运行时把 options / labels 注入 schema，配置页随即渲染为下拉框：

    string + options            -> 单选下拉（v-select）
    list   + options            -> 可搜索多选（v-autocomplete multiple）
    list   + options + render_type=checkbox -> 复选框组

（渲染规则见 AstrBot 仪表盘 ConfigItemRenderer.vue。）
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

INSTANCES_FIELD = "instances"
SESSIONS_FIELD = "notify_sessions"
TEMPLATE_KEY = "onebot"

_MESSAGE_TYPE_LABELS = {
    "GroupMessage": "群",
    "FriendMessage": "私聊",
    "OtherMessage": "其他",
}


def platform_entries(context: Any) -> list[tuple[str, str]]:
    """枚举 AstrBot 已加载的平台实例，返回 [(实例 ID, 展示名), ...]。"""
    entries: list[tuple[str, str]] = []
    seen: set[str] = set()
    manager = getattr(context, "platform_manager", None)
    instances = getattr(manager, "platform_insts", None) or []
    for instance in instances:
        try:
            meta = instance.meta()
        except Exception:
            continue
        platform_id = str(getattr(meta, "id", "") or "").strip()
        if not platform_id or platform_id in seen:
            continue
        seen.add(platform_id)
        name = str(getattr(meta, "name", "") or "").strip()
        label = platform_id if not name or name == platform_id else f"{platform_id}（{name}）"
        entries.append((platform_id, label))
    return entries


def session_label(umo: str) -> str:
    """把 unified_msg_origin 变成好读的展示名。"""
    parts = str(umo or "").split(":")
    if len(parts) >= 3:
        platform_id, message_type = parts[0], parts[1]
        session_id = ":".join(parts[2:])
        kind = _MESSAGE_TYPE_LABELS.get(message_type, message_type)
        return f"{platform_id} · {kind} {session_id}"
    return str(umo or "")


def session_entries(*sources: Iterable[str]) -> list[tuple[str, str]]:
    """合并多个来源的 umo，去重并生成展示名。"""
    entries: list[tuple[str, str]] = []
    seen: set[str] = set()
    for source in sources:
        for item in source or []:
            umo = str(item or "").strip()
            if not umo or umo in seen:
                continue
            seen.add(umo)
            entries.append((umo, session_label(umo)))
    return entries


def _platform_field(schema: dict[str, Any]) -> dict[str, Any] | None:
    """定位 instances 里 platform_id 的 schema 节点。"""
    field = schema.get(INSTANCES_FIELD)
    if not isinstance(field, dict):
        return None
    templates = field.get("templates")
    if isinstance(templates, dict):
        for template in templates.values():
            items = template.get("items") if isinstance(template, dict) else None
            target = items.get("platform_id") if isinstance(items, dict) else None
            if isinstance(target, dict):
                return target
    # 兼容旧的 list + items 结构
    items = field.get("items")
    inner = items.get("items") if isinstance(items, dict) else None
    target = inner.get("platform_id") if isinstance(inner, dict) else None
    return target if isinstance(target, dict) else None


def _apply(field: dict[str, Any], entries: list[tuple[str, str]]) -> bool:
    options = [item[0] for item in entries]
    labels = [item[1] for item in entries]
    if field.get("options") == options and field.get("labels") == labels:
        return False
    field["options"] = options
    field["labels"] = labels
    return True


def inject_schema(
    schema: Any,
    platforms: list[tuple[str, str]],
    sessions: list[tuple[str, str]],
) -> bool:
    """把选项注入内存中的 schema；返回是否有变化。"""
    if not isinstance(schema, dict):
        return False
    changed = False
    platform_field = _platform_field(schema)
    if platform_field is not None:
        changed = _apply(platform_field, platforms) or changed
    sessions_field = schema.get(SESSIONS_FIELD)
    if isinstance(sessions_field, dict):
        changed = _apply(sessions_field, sessions) or changed
    return changed


async def collect_db_sessions(context: Any, limit: int = 200) -> list[str]:
    """尽力从 AstrBot 会话库收集 umo（不同版本字段名有差异，全部容错）。"""
    getter = getattr(context, "get_db", None)
    if not callable(getter):
        return []
    try:
        db = getter()
        conversations = await db.get_all_conversations(page=1, page_size=limit)
    except Exception:
        return []
    umos: list[str] = []
    for conversation in conversations or []:
        for attr in ("user_id", "platform_id", "unified_msg_origin"):
            value = getattr(conversation, attr, None)
            if isinstance(value, str) and value.count(":") >= 2:
                umos.append(value)
                break
    return umos
