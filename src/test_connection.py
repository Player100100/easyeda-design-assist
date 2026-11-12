from __future__ import annotations

import json
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from easyeda_assist.bridge import BridgeClient, BridgeError
from easyeda_assist.connection import connect, ensure_bridge, scan_bridge
from easyeda_assist.cli import main


class ConnectionTests(unittest.TestCase):
    def client(self, windows):
        client = Mock(spec=BridgeClient)
        client.port = 49620
        client.health.return_value = {"service": "easyeda-bridge"}
        client.windows.return_value = windows
        return client

    def test_existing_bridge_reused_without_node_or_files(self):
        client = self.client([])
        with patch("easyeda_assist.connection.scan_bridge", return_value=client), \
             patch("easyeda_assist.connection.subprocess.Popen") as launch, \
             patch("easyeda_assist.connection.data_root") as data:
            actual, metadata = ensure_bridge(Path("nonexistent"))
        self.assertIs(actual, client)
        self.assertFalse(metadata["started"])
        launch.assert_not_called()
        data.assert_not_called()

    def test_spawn_is_detached_with_external_log_and_recheck(self):
        client = self.client([])
        with tempfile.TemporaryDirectory() as tmp, \
             patch.dict(os.environ, {"EASYEDA_ASSIST_DATA_DIR": tmp}):
            skill = Path(tmp) / "skill"
            (skill / "scripts").mkdir(parents=True)
            (skill / "scripts" / "bridge-server.mjs").write_text("// test", encoding="utf-8")
            (skill / "node_modules" / "ws").mkdir(parents=True)
            (skill / "node_modules" / "ws" / "package.json").write_text("{}", encoding="utf-8")
            process = Mock(pid=12345)
            process.poll.return_value = None
            with patch("easyeda_assist.connection.scan_bridge", side_effect=[None, None, client]), \
                 patch("easyeda_assist.connection.shutil.which", return_value="node"), \
                 patch("easyeda_assist.connection.subprocess.run", return_value=Mock(stdout="v22.0.0\n")), \
                 patch("easyeda_assist.connection.subprocess.Popen", return_value=process) as launch:
                actual, metadata = ensure_bridge(skill)
            self.assertIs(actual, client)
            self.assertTrue(metadata["started"])
            self.assertTrue(Path(metadata["log_path"]).is_file())
            self.assertEqual(launch.call_args.args[0], ["node", str(skill / "scripts" / "bridge-server.mjs")])
            options = launch.call_args.kwargs
            self.assertNotIn("shell", options)
            if os.name == "nt":
                self.assertEqual(options["creationflags"], 0x00000008 | 0x00000200)
            else:
                self.assertTrue(options["start_new_session"])
            saved = list((Path(tmp) / "runtime").glob("*.json"))
            self.assertEqual(json.loads(saved[0].read_text(encoding="utf-8"))["launch_pid"], 12345)

    def test_missing_skill_fails_without_starting_process(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.dict(os.environ, {"EASYEDA_ASSIST_DATA_DIR": tmp}), \
             patch("easyeda_assist.connection.scan_bridge", return_value=None), \
             patch("easyeda_assist.connection.subprocess.Popen") as launch:
            with self.assertRaisesRegex(BridgeError, "找不到"):
                ensure_bridge(Path(tmp) / "absent")
            launch.assert_not_called()

    def test_wait_timeout_preserves_bridge_and_reports_reconnect_action(self):
        client = self.client([])
        with patch("easyeda_assist.connection.ensure_bridge", return_value=(client, {"started": False})):
            result = connect(0)
        self.assertEqual(result["status"], "waiting_for_client")
        self.assertIn("重新连接", result["message"])
        client._request.assert_not_called()

    def test_uses_explicit_window_without_selecting_global_active_window(self):
        client = self.client([{"windowId": "one"}, {"windowId": "two"}])
        with patch("easyeda_assist.connection.ensure_bridge", return_value=(client, {"started": False})):
            self.assertEqual(connect(0)["status"], "multiple_windows")
            self.assertEqual(connect(0, window_id="two")["window_id"], "two")
            self.assertEqual(connect(0, window_id="absent")["status"], "waiting_for_client")
        client._request.assert_not_called()

    def test_retries_until_client_arrives(self):
        client = self.client([])
        client.windows.side_effect = [[], [{"windowId": "one"}]]
        with patch("easyeda_assist.connection.ensure_bridge", return_value=(client, {"started": False})), \
             patch("easyeda_assist.connection.time.sleep"):
            self.assertEqual(connect(5)["status"], "connected")

    def test_nonfinite_or_negative_timeout_rejected_before_start(self):
        for value in (-1, float("nan"), float("inf")):
            with self.subTest(value=value), patch("easyeda_assist.connection.ensure_bridge") as start:
                with self.assertRaises(ValueError):
                    connect(value)
                start.assert_not_called()

    def test_scan_does_not_accept_other_services(self):
        with patch.object(BridgeClient, "health", side_effect=BridgeError("端口不是 EasyEDA bridge")):
            self.assertIsNone(scan_bridge())

    def test_cli_exit_codes_distinguish_bridge_ready_from_target_window_ready(self):
        for status, args, expected in (
            ("connected", [], 0),
            ("waiting_for_client", ["--timeout", "0"], 0),
            ("waiting_for_client", ["--timeout", "0", "--window-id", "missing"], 3),
            ("waiting_for_client", [], 3),
            ("multiple_windows", ["--timeout", "0"], 3),
        ):
            with self.subTest(status=status, args=args), tempfile.TemporaryDirectory() as tmp, \
                 patch.dict(os.environ, {"EASYEDA_ASSIST_DATA_DIR": tmp}), \
                 patch("easyeda_assist.cli.connect_api", return_value={"status": status}), \
                 patch("sys.stdout", new_callable=io.StringIO) as output:
                self.assertEqual(main(["connect", *args]), expected)
                report = json.loads(output.getvalue())
                self.assertEqual(report["status"], status)
                self.assertTrue(Path(report["status_path"]).is_file())
