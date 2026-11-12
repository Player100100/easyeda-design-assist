from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

from easyeda_assist.bridge import BridgeClient, BridgeError, CONTEXT_QUERY
from easyeda_assist.contracts import validate_context, validate_task
from easyeda_assist.storage import PROJECT_ROOT, data_root, external
from easyeda_assist.tasks import create_task, handoff


def context(domain="schematic"):
    return {"schema_version": 1, "source": "fixture", "captured_at": "2026-11-12T00:00:00Z",
            "project": {"uuid": "project-1"},
            "document": {"uuid": "doc-1", "documentType": 1 if domain == "schematic" else 3},
            "domain": domain, "selected_ids": [], "limitations": ["fixture only"]}


def recommendation():
    return {"id": "R-01", "status": "suggested", "locator": "SCH/U1",
            "statement": "按目标负载重新核对热耗散", "rationale": "封装热阻决定可用功耗",
            "evidence": ["S-01"], "assumptions": ["环境温度尚未确认"],
            "applicability": ["当前稳压器封装与厂商手册一致时"],
            "risks": ["温度假设可能低估结温"], "confidence": "medium",
            "validation": ["按最高环境温度和负载计算结温"], "decision": "pending"}


class ContractTests(unittest.TestCase):
    def test_context_requires_matching_document_domain(self):
        validate_context(context())
        invalid = context("pcb")
        invalid["document"]["documentType"] = 1
        with self.assertRaisesRegex(ValueError, "不一致"):
            validate_context(invalid)

    def test_task_rejects_unresolved_source_reference(self):
        task = {"schema_version": 1, "id": "t", "mode": "schematic", "goal": "check",
                "created_at": "now", "status": "draft", "context": context(),
                "constraints": [], "missing_inputs": [], "sources": [],
                "recommendations": [recommendation()]}
        with self.assertRaisesRegex(ValueError, "不存在的 source"):
            validate_task(task)

    def test_task_requires_evidence_or_explicit_missing_input_state(self):
        task = {"schema_version": 1, "id": "t", "mode": "schematic", "goal": "check",
                "created_at": "now", "status": "draft", "context": context(),
                "constraints": [], "missing_inputs": [], "sources": [],
                "recommendations": [recommendation()]}
        task["recommendations"][0].update(status="needs_input", evidence=[])
        validate_task(task)
        task["recommendations"][0]["status"] = "suggested"
        with self.assertRaisesRegex(ValueError, "必须引用来源"):
            validate_task(task)

    def test_ready_task_cannot_have_missing_inputs(self):
        task = {"schema_version": 1, "id": "t", "mode": "schematic", "goal": "check",
                "created_at": "now", "status": "ready_for_user", "context": context(),
                "constraints": [], "missing_inputs": ["手册"], "sources": [],
                "recommendations": [recommendation()]}
        task["recommendations"][0].update(status="needs_input", evidence=[])
        with self.assertRaisesRegex(ValueError, "关键输入未补齐"):
            validate_task(task)

    def test_decision_requires_confirmation_record(self):
        task = {"schema_version": 1, "id": "t", "mode": "schematic", "goal": "check",
                "created_at": "now", "status": "draft", "context": context(),
                "constraints": [], "missing_inputs": [], "sources": [],
                "recommendations": [recommendation()]}
        task["recommendations"][0].update(status="needs_input", evidence=[], decision="accepted")
        with self.assertRaisesRegex(ValueError, "确认记录"):
            validate_task(task)


class BridgeTests(unittest.TestCase):
    def test_http_error_preserves_actionable_gateway_message(self):
        bridge = BridgeClient(49620)
        message = "当前焦点文档不是原理图页或PCB（documentType=0）；请点击目标原理图页或PCB画布后重试"
        body = json.dumps({"success": False, "error": message}, ensure_ascii=False).encode("utf-8")
        error = HTTPError(bridge.base + "/execute", 500, "Internal Server Error", {}, io.BytesIO(body))
        with patch.object(bridge.opener, "open", side_effect=error):
            with self.assertRaises(BridgeError) as raised:
                bridge._request("/execute", {"code": CONTEXT_QUERY, "windowId": "w1"})
        self.assertIn("HTTP 500", str(raised.exception))
        self.assertIn(message, str(raised.exception))

    def test_http_error_with_unusable_body_keeps_status(self):
        bridge = BridgeClient(49620)
        for body in (b"upstream unavailable", b"", b"[]", b'{"error":42}'):
            with self.subTest(body=body):
                error = HTTPError(bridge.base + "/execute", 502, "Bad Gateway", {}, io.BytesIO(body))
                with patch.object(bridge.opener, "open", side_effect=error):
                    with self.assertRaisesRegex(BridgeError, "HTTP 502"):
                        bridge._request("/execute")

    def request_mock(self, route, payload=None):
        if route == "/health":
            return {"service": "easyeda-bridge"}
        if route == "/eda-windows":
            return {"windows": [{"windowId": "w1", "connected": True}]}
        self.assertEqual(payload["windowId"], "w1")
        self.assertEqual(payload["code"], CONTEXT_QUERY)
        result = context()
        return {"success": True, "windowId": "w1", "result": {
            k: result[k] for k in ("project", "document", "domain", "selected_ids")}}

    def test_capture_pins_single_window_and_records_limits(self):
        bridge = BridgeClient(49620)
        with patch.object(bridge, "_request", side_effect=self.request_mock):
            result = bridge.capture()
        self.assertEqual(result["window_id"], "w1")
        self.assertEqual(result["source"], "live-api")
        self.assertTrue(result["limitations"])

    def test_multiple_windows_cannot_use_implicit_active_window(self):
        bridge = BridgeClient(49620)
        windows = [{"windowId": x, "connected": True} for x in ("w1", "w2")]
        with patch.object(bridge, "health", return_value={}), patch.object(bridge, "windows", return_value=windows):
            with self.assertRaisesRegex(BridgeError, "多个 EDA 窗口"):
                bridge.capture()

    def test_wrong_window_response_is_rejected(self):
        bridge = BridgeClient(49620)
        with patch.object(bridge, "health", return_value={}), \
             patch.object(bridge, "windows", return_value=[{"windowId": "w1", "connected": True}]), \
             patch.object(bridge, "_request", return_value={"success": True, "windowId": "w2"}):
            with self.assertRaisesRegex(BridgeError, "窗口与请求不一致"):
                bridge.capture("w1")


class StorageTests(unittest.TestCase):
    def test_rejects_project_internal_artifacts(self):
        with self.assertRaisesRegex(ValueError, "项目目录之外"):
            external(PROJECT_ROOT / "outputs" / "bad.json")

    def test_data_root_requires_absolute_override(self):
        with patch.dict(os.environ, {"EASYEDA_ASSIST_DATA_DIR": "relative/path"}):
            with self.assertRaisesRegex(ValueError, "绝对路径"):
                data_root()

    def test_create_validate_and_handoff(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"EASYEDA_ASSIST_DATA_DIR": tmp}):
            path = create_task("schematic", "核对稳压器", [], context())
            task = json.loads(path.read_text(encoding="utf-8"))
            task["missing_inputs"] = []
            task["sources"] = [{"id": "S-01", "kind": "datasheet", "reference": "Vendor.pdf",
                                "locator": "p. 4", "description": "Thermal resistance"}]
            task["recommendations"] = [recommendation()]
            path.write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")
            validate_task(task)
            manifest_path = handoff(path)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["task_id"], task["id"])
            self.assertEqual(len(manifest["files"][0]["sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
