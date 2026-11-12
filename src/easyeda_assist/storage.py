"""Generated artifacts live outside the source checkout."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def external(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved == PROJECT_ROOT or PROJECT_ROOT in resolved.parents:
        raise ValueError("生成产物必须位于源码项目目录之外")
    return resolved


def data_root() -> Path:
    configured = os.environ.get("EASYEDA_ASSIST_DATA_DIR")
    if configured and not Path(configured).expanduser().is_absolute():
        raise ValueError("EASYEDA_ASSIST_DATA_DIR 必须是绝对路径")
    return external(Path(configured) if configured else PROJECT_ROOT.with_name(PROJECT_ROOT.name + "-data"))


def initialize() -> Path:
    root = data_root()
    for name in ("tasks", "contexts", "handoffs"):
        external(root / name).mkdir(parents=True, exist_ok=True)
    return root


def new_id(prefix: str) -> str:
    return f"{prefix}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid4().hex[:12]}"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_new(path: Path, value) -> Path:
    path = external(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return path
