"""Optional multi-board instance and explicit pin-map checks; no CAD writes."""
from __future__ import annotations

import re


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _objects(value, label):
    if not isinstance(value, list) or any(not isinstance(x, dict) or not _text(x.get("id")) for x in value):
        raise ValueError(label + " 必须为具有明确id的对象数组")
    if len({x["id"] for x in value}) != len(value):
        raise ValueError(label + " 身份不得重复")
    return {x["id"]: x for x in value}


def validate_product(product):
    if not isinstance(product, dict) or type(product.get("schema_version")) is not int or product["schema_version"] != 1 or not _text(product.get("id")):
        raise ValueError("product_context 需要 schema_version=1 和明确id")
    boards = _objects(product.get("boards"), "boards")
    instances = _objects(product.get("instances"), "instances")
    connections = _objects(product.get("connections"), "connections")
    connectors = {}
    for bid, board in boards.items():
        for key in ("project_uuid", "revision", "snapshot_sha256"):
            if not _text(board.get(key)):
                raise ValueError("板卡缺少 " + key)
        if not re.fullmatch(r"[0-9a-f]{64}", board["snapshot_sha256"]):
            raise ValueError("板卡快照需要小写SHA256")
        connectors[bid] = _objects(board.get("connectors"), "connectors")
        for connector in connectors[bid].values():
            pins = connector.get("pins")
            if not isinstance(pins, list) or not pins or any(not _text(pin) for pin in pins) or len(pins) != len(set(pins)):
                raise ValueError("连接器必须声明不重复的明确引脚编号")
    for instance in instances.values():
        if instance.get("board_id") not in boards:
            raise ValueError("板卡实例引用了不存在的board_id")
    pairs = set()
    for connection in connections.values():
        endpoints = []
        for side in ("a", "b"):
            endpoint = connection.get(side)
            if not isinstance(endpoint, dict) or set(endpoint) != {"instance", "connector", "pin"} or any(not _text(x) for x in endpoint.values()):
                raise ValueError("连接端点必须显式提供 instance、connector、pin")
            instance = instances.get(endpoint["instance"])
            if instance is None:
                raise ValueError("连接端点引用了不存在的板卡实例")
            connector = connectors[instance["board_id"]].get(endpoint["connector"])
            if connector is None or endpoint["pin"] not in connector["pins"]:
                raise ValueError("连接端点引用了不存在的连接器或引脚")
            endpoints.append(tuple(endpoint[k] for k in ("instance", "connector", "pin")))
        pair = tuple(sorted(endpoints))
        if endpoints[0] == endpoints[1] or pair in pairs:
            raise ValueError("端点不能自连或重复登记同一连接")
        pairs.add(pair)
        if not isinstance(connection.get("evidence"), list) or any(not _text(x) for x in connection["evidence"]):
            raise ValueError("板间连接 evidence 必须是来源字符串数组，可为空并报告待核实")


def review_product(product):
    validate_product(product)
    findings = []
    for connection in product["connections"]:
        for key in ("signal", "voltage", "return_path", "cable_mapping"):
            if not _text(connection.get(key)) or connection[key].upper() in {"UNKNOWN", "TBD"}:
                findings.append({"connection": connection["id"], "missing": key})
        if not connection["evidence"]:
            findings.append({"connection": connection["id"], "missing": "evidence"})
    if not product["instances"]:
        findings.append({"missing": "instances"})
    return {"product_id": product["id"], "board_count": len(product["boards"]),
            "instance_count": len(product["instances"]), "connection_count": len(product["connections"]),
            "findings": findings, "records_complete": not findings, "engineering_verified": False,
            "limitations": ["只核对显式身份、引脚及记录缺项，不推断同名网连接或电气兼容。",
                            "机械包络、连接器观察面和实际线缆映射需要来源与人工/实物复核。",
                            "各板快照独立采集，不构成跨工程事务。"]}
