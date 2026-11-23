"""A historical zIndex restore cannot become a general document-source writer."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from easyeda_assist.drawing_source import records
from easyeda_assist.project import object_digest
from easyeda_assist.source_repair import prepare_z_index_restore, execute_z_index_restore, _make_restore, _script
from easyeda_assist.source_trial import ACTIVE_CLAIM, lock_inventory
from easyeda_assist.storage import read_json, write_new
from test_source_validation import evidence


class ZIndexRestoreTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        env = patch.dict(os.environ, {"EASYEDA_ASSIST_DATA_DIR": folder.name})
        env.start()
        self.addCleanup(env.stop)
        self.original, self.original_audit = evidence(z_index=16)
        self.current, self.current_audit = evidence(z_index=15)
        self.paths = [write_new(self.root / f"input-{i}.json", value) for i, value in enumerate(
            (self.original, self.original_audit, self.current, self.current_audit))]
        self.prepared = prepare_z_index_restore(*self.paths)
        self.case_path = Path(self.prepared["case_path"])
        self.lock = self.root / "drawing-locks" / object_digest({"project_uuid": "p", "document_uuid": "d"})
        self.bridge = Mock()
        self.bridge.health.return_value = {"pendingRequests": 0}
        self.bridge.design_query.side_effect = [self.current, self.current_audit, self.original, self.original_audit]
        self.bridge._execute_fixed.return_value = ({"status": "verified", "attempted": True, "stable_reads": 3,
            "identity": self.original["data"]["identity"], "source": self.original["data"]["source"]}, "w")

    def execute(self, **kwargs):
        return execute_z_index_restore(self.case_path, self.prepared["case_sha256"], "fixture repair authorization",
                                       "fixture", lambda: self.bridge, pause=lambda seconds: None, **kwargs)

    def test_single_write_restores_exact_original_records_and_preserves_history(self):
        old = write_new(self.lock / "owner.json", {"status": "uncertain"})
        old_bytes = old.read_bytes()
        result = self.execute(reviewed_lock_sha256=lock_inventory(self.lock)[1])
        self.assertEqual(result["status"], "restored_observed")
        self.assertFalse(result["drc_freshness_established"])
        self.assertEqual(self.bridge._execute_fixed.call_count, 1)
        self.assertEqual(old.read_bytes(), old_bytes)
        self.assertFalse((self.lock / ACTIVE_CLAIM).exists())
        for name in ("authorization.json", "intent.json", "reply.json", "001-source.json", "001-audit.json", "result.json"):
            self.assertTrue((self.case_path.parent / name).is_file())
        with self.assertRaisesRegex(ValueError, "禁止重放"):
            self.execute()

    def test_changed_value_geometry_connectivity_or_multiple_records_cannot_prepare(self):
        for options in ({"value": "other"}, {"x": 11}, {"net": "other"}):
            with self.subTest(options=options):
                current, audit = evidence(z_index=15, **options)
                with self.assertRaises(ValueError):
                    _make_restore(self.original, self.original_audit, current, audit)
        with self.assertRaisesRegex(ValueError, "一个已有属性"):
            _make_restore(self.original, self.original_audit, self.original, self.original_audit)

    def test_live_drift_or_unreviewed_historical_lock_blocks_before_write(self):
        old = write_new(self.lock / "owner.json", {"status": "uncertain"})
        with self.assertRaisesRegex(ValueError, "历史锁未复核"):
            self.execute()
        self.bridge.design_query.side_effect = [self.original, self.original_audit]
        with self.assertRaisesRegex(ValueError, "漂移"):
            self.execute(reviewed_lock_sha256=lock_inventory(self.lock)[1])
        self.bridge._execute_fixed.assert_not_called()
        self.assertFalse((self.lock / ACTIVE_CLAIM).exists())

    def test_lost_response_keeps_claim_and_never_retries(self):
        self.bridge._execute_fixed.side_effect = TimeoutError("lost reply")
        result = self.execute()
        self.assertEqual(result["status"], "uncertain")
        self.assertTrue((self.lock / ACTIVE_CLAIM).exists())
        self.assertEqual(self.bridge._execute_fixed.call_count, 1)
        with self.assertRaisesRegex(ValueError, "禁止重放"):
            self.execute()

    def test_archived_input_tampering_cannot_be_submitted(self):
        path = self.case_path.parent / "original-source.json"
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "字节摘要"):
            self.execute()
        self.bridge._execute_fixed.assert_not_called()

    def test_wrong_window_cannot_release_claim_even_if_reply_says_blocked(self):
        self.bridge._execute_fixed.return_value = ({"status": "blocked", "attempted": False}, "other-window")
        result = self.execute()
        self.assertEqual(result["status"], "uncertain")
        self.assertTrue(result["attempted"])
        self.assertTrue((self.lock / ACTIVE_CLAIM).exists())

    def test_historical_change_after_claim_blocks_before_write(self):
        from easyeda_assist.drawing_execution import _durable_new as durable_new
        def changed_claim(path, value):
            result = durable_new(path, value)
            if path == self.case_path.parent / "claim.json":
                write_new(self.lock / "concurrent-owner.json", {"changed": True})
            return result
        with patch("easyeda_assist.source_repair._durable_new", side_effect=changed_claim):
            result = self.execute()
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["attempted"])
        self.bridge._execute_fixed.assert_not_called()
        self.assertTrue((self.lock / "concurrent-owner.json").exists())

    @unittest.skipUnless(shutil.which("node"), "Node required for fixed repair script")
    def test_fixed_source_setter_restores_only_computed_patch_and_blocks_wrong_target(self):
        case, original, current, desired = _make_restore(self.original, self.original_audit, self.current, self.current_audit)
        self.assertEqual(records(desired), records(original[1]["source"]))
        script = _script(case, current, desired)
        harness = r"""
const input=JSON.parse(require('fs').readFileSync(0,'utf8'));let source=input.before,calls=0;
const eda={dmt_Project:{async getCurrentProjectInfo(){return {uuid:'p'};}},
dmt_SelectControl:{async getCurrentDocumentInfo(){return {uuid:input.wrong?'other':'d',tabId:'t',documentType:1,parentProjectUuid:'p'};}},
dmt_Schematic:{async getCurrentSchematicInfo(){return {uuid:'s',name:'S'};},async getCurrentSchematicPageInfo(){return {uuid:'d',name:'P',parentSchematicUuid:'s'};}},
sys_Environment:{getEditorCurrentVersion(){return '4.1.100';}},
sys_FileManager:{async getDocumentSource(){return source;},async setDocumentSource(value){calls++;source=value;return true;}}};
const F=Object.getPrototypeOf(async function(){}).constructor;
new F('eda','setTimeout',input.script)(eda,fn=>fn()).then(result=>process.stdout.write(JSON.stringify({result,calls,source})));
"""
        for wrong in (False, True):
            with self.subTest(wrong=wrong):
                process = subprocess.run([shutil.which("node"), "-e", harness], input=json.dumps({
                    "script": script, "before": current[1]["source"], "wrong": wrong}),
                    text=True, encoding="utf-8", capture_output=True, timeout=10, check=True)
                result = json.loads(process.stdout)
                self.assertEqual(result["calls"], 0 if wrong else 1)
                self.assertEqual(result["result"]["status"], "blocked" if wrong else "verified")
                if not wrong:
                    self.assertEqual(records(result["source"]), records(original[1]["source"]))


if __name__ == "__main__":
    unittest.main()
