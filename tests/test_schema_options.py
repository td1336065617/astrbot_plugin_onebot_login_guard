"""配置页下拉选项测试。"""
from __future__ import annotations

import json
from pathlib import Path

from core.schema_options import (
    inject_schema,
    platform_entries,
    session_entries,
    session_label,
)

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "_conf_schema.json"


class _Meta:
    def __init__(self, platform_id: str, name: str) -> None:
        self.id = platform_id
        self.name = name


class _Instance:
    def __init__(self, platform_id: str, name: str) -> None:
        self._meta = _Meta(platform_id, name)

    def meta(self):
        return self._meta


class _Manager:
    def __init__(self, instances) -> None:
        self.platform_insts = instances


class _Context:
    def __init__(self, instances) -> None:
        self.platform_manager = _Manager(instances)


def test_session_label():
    assert session_label("aiocqhttp:FriendMessage:123") == "aiocqhttp · 私聊 123"
    assert session_label("telegram:GroupMessage:-100") == "telegram · 群 -100"
    assert session_label("weird") == "weird"


def test_platform_entries_dedup_and_label():
    context = _Context(
        [
            _Instance("default", "aiocqhttp"),
            _Instance("default", "aiocqhttp"),
            _Instance("爱莉希雅2", ""),
        ]
    )
    entries = platform_entries(context)
    assert entries == [("default", "default（aiocqhttp）"), ("爱莉希雅2", "爱莉希雅2")]


def test_platform_entries_tolerates_broken_instance():
    class _Broken:
        def meta(self):
            raise RuntimeError("boom")

    context = _Context([_Broken(), _Instance("ok", "name")])
    assert platform_entries(context) == [("ok", "ok（name）")]


def test_session_entries_dedupe_order():
    entries = session_entries(["a:FriendMessage:1", "a:FriendMessage:1"], ["b:GroupMessage:2"])
    assert [item[0] for item in entries] == ["a:FriendMessage:1", "b:GroupMessage:2"]
    assert entries[0][1] == "a · 私聊 1"


def test_inject_schema_into_template_list():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["instances"]["type"] == "template_list"
    changed = inject_schema(
        schema,
        [("default", "default（aiocqhttp）")],
        [("aiocqhttp:FriendMessage:1", "aiocqhttp · 私聊 1")],
    )
    assert changed is True
    platform_field = schema["instances"]["templates"]["onebot"]["items"]["platform_id"]
    assert platform_field["options"] == ["default"]
    assert platform_field["labels"] == ["default（aiocqhttp）"]
    assert schema["notify_sessions"]["options"] == ["aiocqhttp:FriendMessage:1"]
    assert inject_schema(
        schema,
        [("default", "default（aiocqhttp）")],
        [("aiocqhttp:FriendMessage:1", "aiocqhttp · 私聊 1")],
    ) is False


def test_inject_schema_legacy_list_shape():
    schema = {
        "instances": {"type": "list", "items": {"items": {"platform_id": {"type": "string"}}}},
        "notify_sessions": {"type": "list"},
    }
    assert inject_schema(schema, [("p1", "p1")], []) is True
    assert schema["instances"]["items"]["items"]["platform_id"]["options"] == ["p1"]


def test_inject_schema_handles_garbage():
    assert inject_schema(None, [], []) is False
    assert inject_schema("nope", [], []) is False
    assert inject_schema({}, [], []) is False
