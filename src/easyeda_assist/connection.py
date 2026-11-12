"""Start the installed gateway detached, reuse it, and wait for a client.

Inspired by the sibling audit project's src/eda.py lifecycle. No dependency on
that repository, no arbitrary JavaScript executor, and no process termination.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import math
import os
from pathlib import Path
import shutil
import subprocess
import time

from .bridge import BridgeClient, BridgeError
from .storage import data_root, external, new_id, now, write_new


def scan_bridge() -> BridgeClient | None:
    def probe(port):
        client = BridgeClient(port, timeout=0.3)
        try:
            client.health()
            return client
        except BridgeError:
            return None

    with ThreadPoolExecutor(max_workers=10) as pool:
        clients = list(pool.map(probe, range(49620, 49630)))
    return next((client for client in clients if client is not None), None)


def skill_directory(configured: Path | None = None) -> Path:
    override = configured or os.environ.get("EASYEDA_SKILL_DIR")
    candidates = ([Path(override).expanduser()] if override else [
        Path.home() / ".claude" / "skills" / "easyeda-api-skill",
        Path.home() / ".codex" / "skills" / "easyeda-api-skill",
    ])
    for path in candidates:
        if (path / "scripts" / "bridge-server.mjs").is_file():
            return path.resolve()
    raise BridgeError("找不到已安装的 easyeda-api-skill；请用 --skill-dir 或 EASYEDA_SKILL_DIR 指定技能目录")


@contextmanager
def startup_lock(path: Path, timeout: float = 30):
    # OS releases the advisory lock even if the launcher crashes. Keep the file;
    # removing a lock file could let another process lock a different inode.
    with path.open("a+b") as stream:
        if stream.seek(0, 2) == 0:
            stream.write(b"0")
            stream.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise BridgeError("另一个启动脚本仍在初始化 bridge，请稍后重试")
                time.sleep(0.2)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def ensure_bridge(skill_dir: Path | None = None, start_timeout: float = 20) -> tuple[BridgeClient, dict]:
    bridge = scan_bridge()
    if bridge:
        return bridge, {"started": False}
    runtime = external(data_root() / "runtime")
    runtime.mkdir(parents=True, exist_ok=True)
    with startup_lock(external(runtime / "startup.lock")):
        bridge = scan_bridge()
        if bridge:
            return bridge, {"started": False}
        skill = skill_directory(skill_dir)
        node = shutil.which("node")
        if not node:
            raise BridgeError("未找到 Node.js；请安装 Node.js 18+ 并重新打开终端")
        try:
            version = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=5, check=True)
            if int(version.stdout.strip().lstrip("v").split(".")[0]) < 18:
                raise ValueError("Node.js 版本低于 18")
        except (subprocess.SubprocessError, ValueError) as exc:
            raise BridgeError(f"需要可正常运行的 Node.js 18+：{exc}") from exc
        if not (skill / "node_modules" / "ws" / "package.json").is_file():
            raise BridgeError(f"bridge 缺少 ws 依赖，请在技能目录运行 npm install：{skill}")
        run_id = new_id("bridge")
        log_path = external(runtime / (run_id + ".log"))
        with log_path.open("xb") as log:
            options = {"cwd": str(skill), "stdin": subprocess.DEVNULL,
                       "stdout": log, "stderr": log, "close_fds": True}
            if os.name == "nt":
                options["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                options["start_new_session"] = True
            process = subprocess.Popen([node, str(skill / "scripts" / "bridge-server.mjs")], **options)
        metadata = {"started": True, "launch_pid": process.pid, "log_path": str(log_path),
                    "started_at": now(), "skill_dir": str(skill)}
        write_new(runtime / (run_id + ".json"), metadata)
        deadline = time.monotonic() + start_timeout
        while True:
            bridge = scan_bridge()
            if bridge:
                return bridge, metadata
            if process.poll() is not None:
                raise BridgeError(f"bridge 进程已退出（{process.returncode}），请查看日志：{log_path}")
            if time.monotonic() >= deadline:
                raise BridgeError(f"bridge 启动等待超时，请查看日志：{log_path}")
            time.sleep(0.3)


def connect(timeout: float = 60, skill_dir: Path | None = None,
            window_id: str | None = None, progress=None) -> dict:
    if not math.isfinite(timeout) or timeout < 0:
        raise ValueError("timeout 必须为非负有限秒数")
    bridge, metadata = ensure_bridge(skill_dir)
    bridge.timeout = 2
    if progress:
        progress(f"bridge 已就绪：127.0.0.1:{bridge.port}；等待 EDA 窗口（最多 {timeout:g} 秒）。")
        progress("若未自动接入，请在嘉立创 EDA 的 API Gateway 菜单点击重新连接。")
    deadline = time.monotonic() + timeout
    next_progress = time.monotonic() + 5
    while True:
        health = bridge.health()
        windows = bridge.windows()
        matched = [w for w in windows if w.get("windowId") == window_id] if window_id else windows
        result = {"port": bridge.port, **metadata, "health": health, "windows": windows}
        if matched:
            if not window_id and len(matched) > 1:
                return {**result, "status": "multiple_windows", "window_id": None,
                        "message": "多个窗口已连接；请用 --window-id 指定目标窗口"}
            return {**result, "status": "connected", "window_id": matched[0]["windowId"],
                    "message": "已连接；请打开目标画布后用 context 读取上下文"}
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {**result, "status": "waiting_for_client", "window_id": window_id,
                    "message": "bridge 保持运行。请在 EDA 中启用 API Gateway 并点击重新连接，再运行本脚本。",
                    "extension_url": "https://jlc-ext.com/item/oshwhub/run-api-gateway"}
        if progress and time.monotonic() >= next_progress:
            progress("仍在等待 EDA 客户端连接…")
            next_progress = time.monotonic() + 5
        time.sleep(min(1, remaining))
