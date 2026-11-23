"""Verify device provenance from bounded epro2 exports, without extracting files."""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import zipfile
from pathlib import Path

from .drawing_api import comparable_source, VERIFIED_EDITOR_VERSION
from .storage import external

MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_LOG_BYTES = 100 * 1024 * 1024


def device_sources(archive_base64: str) -> dict[str, str]:
    """Resolve DEVICE META log winners by ticket, then smaller client ID.

    Deletion and conflicting records are rejected for the requested mapping.
    We deliberately require the imported device to exist before a write.
    """
    if not isinstance(archive_base64, str) or len(archive_base64) > (MAX_ARCHIVE_BYTES + 2) // 3 * 4:
        raise ValueError("工程归档缺失或过大")
    try:
        raw = base64.b64decode(archive_base64, validate=True)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            logs = [m for m in members if m.filename.endswith(".epru")]
            if (len(members) > 10000 or len(logs) != 1 or
                    sum(m.file_size for m in members) > MAX_LOG_BYTES or logs[0].file_size > MAX_LOG_BYTES):
                raise ValueError("工程归档结构不兼容或解压后过大")
            text = archive.read(logs[0]).decode("utf-8-sig")
    except (binascii.Error, zipfile.BadZipFile, UnicodeError, RuntimeError, OSError, NotImplementedError) as exc:
        raise ValueError("工程归档无法解析") from exc
    winners = {}
    header = None
    try:
        for line in text.splitlines():
            if not line.strip():
                continue
            outer, separator, inner = line.partition("||")
            if not separator:
                raise ValueError("工程日志记录缺少分隔符")
            outer = json.loads(outer)
            if not isinstance(outer, dict):
                raise ValueError("工程日志外层无效")
            relevant = outer.get("type") == "DOCHEAD" or (
                header and header.get("docType") == "DEVICE" and outer.get("type") in {"META", "DELETE_DOC"})
            if not relevant:
                continue
            inner = inner.rstrip()
            encoded = inner[:-1] if inner.endswith("|") else inner
            # Exported tombstones may have an empty payload (|||), not only JSON "".
            value = json.loads(encoded) if encoded else ""
            if outer.get("type") == "DOCHEAD":
                if not isinstance(value, dict):
                    raise ValueError("工程日志文档头无效")
                header = value
                continue
            if not header or header.get("docType") != "DEVICE" or outer.get("type") not in {"META", "DELETE_DOC"}:
                continue
            if outer.get("type") == "META" and outer.get("id") not in (None, "META"):
                raise ValueError("工程器件 META 标识不兼容")
            uuid, client = header.get("uuid"), header.get("client")
            ticket = outer.get("ticket", 0)
            if (not isinstance(uuid, str) or not uuid or not isinstance(client, str) or
                    type(ticket) is not int or ticket < 0):
                raise ValueError("工程器件日志身份或时序无效")
            # META id is a singleton; don't accept multiple arbitrary IDs as separate identities.
            key = (uuid, outer["type"])
            old = winners.get(key)
            if old and old[:2] == (ticket, client) and old[2] != value:
                raise ValueError("工程器件日志存在同序冲突")
            if not old or ticket > old[0] or (ticket == old[0] and client < old[1]):
                winners[key] = (ticket, client, value)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("工程日志格式不兼容") from exc
    result = {}
    for (uuid, kind), (_, _, value) in winners.items():
        deleted = winners.get((uuid, "DELETE_DOC"))
        if kind == "META" and isinstance(value, dict) and not (
            deleted and (not isinstance(deleted[2], dict) or deleted[2].get("isDelete") is not False)
        ):
            source = value.get("source")
            if isinstance(source, str):
                result[uuid] = source
    return result


def verify_mapping(record: dict, snapshot: dict, parameters: dict, *, editor_version=VERIFIED_EDITOR_VERSION) -> dict:
    """Require provenance and a stable page from the same window and project."""
    if (not isinstance(record, dict) or record.get("identity") != snapshot["identity"] or
            record.get("window_id") != snapshot["window_id"] or
            not isinstance(record.get("source"), str) or
            comparable_source(record["source"]) != comparable_source(snapshot["source"])):
        raise ValueError("工程库证据与目标窗口、身份或文档源不一致")
    info = record.get("project_info")
    if not isinstance(info, dict) or info.get("editorVersion") != editor_version:
        raise ValueError("工程库证据的编辑器版本尚未核验")
    if "current_editor_version" in record and record["current_editor_version"] != editor_version:
        raise ValueError("运行中的编辑器版本尚未核验")
    encoded = record.get("archive_base64")
    if not isinstance(encoded, str) or len(encoded) > (MAX_ARCHIVE_BYTES + 2) // 3 * 4:
        raise ValueError("工程归档缺失或过大")
    try:
        archive = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("工程归档 base64 无效") from exc
    if "file_size" in record and (type(record["file_size"]) is not int or len(archive) != record["file_size"]):
        raise ValueError("工程归档大小不一致")
    sources = device_sources(record.get("archive_base64"))
    local_uuid = parameters["expected_project_device_uuid"]
    expected = parameters["device_uuid"] + "|" + parameters["library_uuid"]
    if sources.get(local_uuid) != expected:
        raise ValueError("工程 DEVICE/META.source 未证明系统库与工程 UUID 映射")
    return {"project_device_uuid": local_uuid, "source": expected,
            "editor_version": editor_version,
            "archive_sha256": hashlib.sha256(archive).hexdigest()}


def read_mapping_evidence(snapshot: dict, parameters: dict) -> tuple[dict, dict]:
    evidence = parameters["mapping_evidence"]
    path = Path(evidence["path"])
    if not path.is_absolute():
        raise ValueError("工程映射证据路径必须为绝对路径")
    path = external(path)
    if path.stat().st_size > 90 * 1024 * 1024:
        raise ValueError("工程映射证据文件过大")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != evidence["sha256"]:
        raise ValueError("工程映射证据哈希不一致")
    record = json.loads(raw.decode("utf-8-sig"))
    return record, verify_mapping(record, snapshot, parameters)


def load_mapping_evidence(snapshot: dict, parameters: dict) -> dict:
    return read_mapping_evidence(snapshot, parameters)[1]
