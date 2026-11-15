"""Conservative source inspection: additions cannot hide edits to old objects.

Snapshot format (not a CRDT log): reject duplicates and unknown/tombstone
records. Compare payloads as well as record envelopes. Only DOCHEAD's three
observed volatile fields are ignored. Never write document source.
"""
from __future__ import annotations

import json

from .drawing_api import comparable_source


def records(source: str) -> dict:
    result = {}
    for line in comparable_source(source).splitlines():
        if not line.strip():
            continue
        outer, sep, inner = line.partition("||")
        if not sep:
            raise ValueError("文档源记录缺少分隔符")
        envelope = json.loads(outer)
        inner = inner.rstrip()
        value = json.loads(inner[:-1] if inner.endswith("|") else inner)
        if not isinstance(envelope, dict) or not isinstance(value, dict):
            raise ValueError("文档源记录必须为对象；不接受删除记录")
        kind = envelope.get("type")
        rid = "DOCHEAD" if kind == "DOCHEAD" else envelope.get("id")
        if not isinstance(kind, str) or not isinstance(rid, str) or not rid or rid in result:
            raise ValueError("文档源存在缺失或重复图元身份")
        result[rid] = (envelope, value)
    if not result or result.get("DOCHEAD", ({}, {}))[1].get("docType") != "SCH_PAGE":
        raise ValueError("必须是原理图完整源快照")
    return result


def verify_addition(before: str, after: str, primitive_id: str, kind: str) -> dict:
    old, new = records(before), records(after)
    if primitive_id in old or primitive_id not in new:
        raise ValueError("新增图元身份不存在或与已有对象冲突")
    for rid, record in old.items():
        if new.get(rid) != record:
            raise ValueError("发现方案外已有对象修改或删除：" + rid)
    added = set(new) - set(old)
    if new[primitive_id][0]["type"] != kind:
        raise ValueError("新增图元类型不一致")
    if len(added) > 300:
        raise ValueError("单次新增图元数量超过限制")
    attributes = {}
    for rid in added - {primitive_id}:
        envelope, value = new[rid]
        if envelope["type"] == "LINE":
            if value.get("lineGroup") != primitive_id:
                raise ValueError("新增线段未绑定到导线图元")
            continue
        if envelope["type"] != "ATTR" or value.get("parentId") != primitive_id:
            raise ValueError("发现方案外新增对象：" + rid)
        key = value.get("key")
        if not isinstance(key, str) or key in attributes:
            raise ValueError("新增对象属性身份重复或无效")
        attributes[key] = value
    return {"primitive": new[primitive_id][1], "attributes": attributes,
            "added_ids": sorted(added)}


def verify_wire_merge(before: str, after: str, primitive_id: str, expected_line, net: str) -> dict:
    old, new = records(before), records(after)
    if primitive_id not in old or primitive_id not in new or old[primitive_id][0].get("type") != "WIRE" or new[primitive_id][0].get("type") != "WIRE":
        raise ValueError("合并导线身份无效")
    if set(old) - set(new):
        raise ValueError("合并导线期间删除了已有图元")
    added, changed = set(new) - set(old), {rid for rid in old if old[rid] != new[rid]}
    lines, attributes = [], {}
    for rid in added:
        env, value = new[rid]
        if env.get("type") == "LINE" and value.get("lineGroup") == primitive_id:
            lines.append([value["startX"], -value["startY"], value["endX"], -value["endY"]])
        elif env.get("type") == "ATTR" and value.get("parentId") == primitive_id and value.get("key") in {"NET", "Relevance"}:
            key = value["key"]
            if key in attributes:
                raise ValueError("合并导线属性重复")
            attributes[key] = value
        else:
            raise ValueError("合并导线期间新增了方案外图元：" + rid)
    if not lines or wire_segments(lines) != wire_segments(expected_line):
        raise ValueError("合并导线新增线段与计划不一致")
    for rid in changed:
        old_env, old_value = old[rid]
        new_env, new_value = new[rid]
        if old_env != new_env or old_env.get("type") != "ATTR" or old_value.get("parentId") != primitive_id:
            raise ValueError("合并导线修改了非属性图元")
        if any(old_value.get(key) != new_value.get(key) for key in set(old_value) | set(new_value) if key != "zIndex"):
            raise ValueError("合并导线修改了网络或未授权属性")
    previous_nets = [value.get("value") for env, value in old.values()
                     if env.get("type") == "ATTR" and value.get("parentId") == primitive_id and value.get("key", "").upper() == "NET"]
    if net not in previous_nets or any(value.get("value") != net for key, value in attributes.items() if key == "NET"):
        raise ValueError("合并导线网络名不一致")
    return {"primitive": new[primitive_id][1], "attributes": attributes, "added_ids": sorted(added)}


def wire_segments(line) -> tuple:
    """Compare exact orthogonal coverage; allow reversal, splitting and merging."""
    if not isinstance(line, list) or not line:
        raise ValueError("导线路径为空")
    groups = [line] if not isinstance(line[0], list) else line
    if len(groups) > 100:
        raise ValueError("导线路径过大")
    intervals = {}
    for group in groups:
        if not isinstance(group, list) or len(group) < 4 or len(group) % 2 or len(group) > 400:
            raise ValueError("导线路径必须至少含两个完整坐标点")
        for x1, y1, x2, y2 in zip(group[0::2], group[1::2], group[2::2], group[3::2]):
            import math
            if any(type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 1000000
                   for v in (x1, y1, x2, y2)):
                raise ValueError("导线坐标必须有限且不能为布尔值")
            if (x1 == x2) == (y1 == y2):
                raise ValueError("只接受非零正交导线")
            key, a, b = (("V", x1), y1, y2) if x1 == x2 else (("H", y1), x1, x2)
            intervals.setdefault(key, []).append((min(a, b), max(a, b)))
    output = []
    for key, values in sorted(intervals.items()):
        merged = []
        for a, b in sorted(values):
            if merged and a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
            else:
                merged.append((a, b))
        output.extend((*key, a, b) for a, b in merged)
    return tuple(output)
