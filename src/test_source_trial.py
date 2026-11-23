"""Experiments keep general dispatch closed, bind live baselines and journal once."""
from copy import deepcopy
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from easyeda_assist.operations import ADAPTERS, SOURCE
from easyeda_assist.cli import main
from easyeda_assist.drawing_source import records
from easyeda_assist.project import object_digest
from easyeda_assist.source_trial import (ACTIVE_CLAIM, execute_trial, lock_inventory,
                                        review_trial_state, close_reviewed_trial)
from easyeda_assist.source_validation import prepare_validation
from easyeda_assist.storage import read_json, write_new
from easyeda_assist.traceability import canonical_digest
from test_source_validation import evidence


class SourceTrialTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        env = patch.dict(os.environ, {"EASYEDA_ASSIST_DATA_DIR": folder.name})
        env.start()
        self.addCleanup(env.stop)
        self.before, self.before_audit = evidence()
        self.after, self.after_audit = evidence(value="trial")
        source = write_new(self.root / "source.json", self.before)
        audit = write_new(self.root / "audit.json", self.before_audit)
        prepared = prepare_validation(source, audit, "Name", "trial")
        self.case_path, self.case_sha = Path(prepared["validation_path"]), prepared["case_sha256"]
        key = object_digest({"project_uuid": "p", "document_uuid": "d"})
        self.lock = self.root / "drawing-locks" / key
        self.bridge = Mock()
        self.bridge.health.return_value = {"pendingRequests": 0}
        self.bridge.design_query.side_effect = [self.before, self.before_audit, self.after, self.after_audit]
        self.bridge._execute_fixed.return_value = ({"status": "verified", "attempted": True,
            "primitive_id": "c", "source_target": self.after["data"]["target"], "stable_reads": 3,
            "identity": self.after["data"]["identity"], "source": self.after["data"]["source"],
            "original": self.before["data"]["outputs"]["component"]["value"],
            "observed": self.after["data"]["outputs"]["component"]["value"]}, "w")

    def execute(self, **kwargs):
        return execute_trial(self.case_path, self.case_sha, "fixture test authorization", "fixture-message",
                             lambda: self.bridge, pause=lambda seconds: None, **kwargs)

    def test_success_journals_fixed_call_and_keeps_general_dispatch_closed(self):
        result = self.execute()
        self.assertEqual(result["status"], "property_observed")
        self.assertFalse(ADAPTERS[SOURCE]["dispatch_enabled"])
        self.assertFalse(result["drc_freshness_established"])
        self.assertFalse(self.lock.exists())
        run = Path(result["run_dir"])
        for name in ("authorization.json", "case.json", "intent.json", "reply.json", "result.json", "001-observation.json"):
            self.assertTrue((run / name).is_file(), name)
        self.assertEqual(self.bridge._execute_fixed.call_count, 1)
        script = self.bridge._execute_fixed.call_args.args[0]
        self.assertEqual(script.count("sch_SourcePrimitiveComponent.update("), 1)
        self.assertNotIn("setNetlist(", script)
        with self.assertRaisesRegex(ValueError, "禁止重放"):
            self.execute()

    def test_wrong_digest_empty_authorization_and_pending_requests_block_before_write(self):
        with self.assertRaisesRegex(ValueError, "摘要"):
            execute_trial(self.case_path, "wrong", "fixture", "message", lambda: self.bridge)
        with self.assertRaisesRegex(ValueError, "授权"):
            execute_trial(self.case_path, self.case_sha, "", "message", lambda: self.bridge)
        self.bridge.health.return_value = {"pendingRequests": 1}
        with self.assertRaisesRegex(ValueError, "待处理"):
            self.execute()
        self.bridge._execute_fixed.assert_not_called()

    def test_live_baseline_change_blocks_before_claim_or_write(self):
        changed, changed_audit = evidence(net="changed")
        self.bridge.design_query.side_effect = [changed, changed_audit]
        with self.assertRaisesRegex(ValueError, "基线已变化"):
            self.execute()
        self.bridge._execute_fixed.assert_not_called()
        self.assertFalse(self.lock.exists())

    def test_lost_response_retains_single_claim_and_never_retries(self):
        self.bridge._execute_fixed.side_effect = TimeoutError("lost response")
        result = self.execute()
        self.assertEqual(result["status"], "uncertain")
        self.assertTrue((self.lock / ACTIVE_CLAIM).is_file())
        self.assertEqual(self.bridge._execute_fixed.call_count, 1)
        with self.assertRaisesRegex(ValueError, "未关闭"):
            self.execute()

    def test_historical_lock_requires_exact_review_digest_and_remains_byte_identical(self):
        old = write_new(self.lock / "owner.json", {"run_dir": "historical-uncertain", "status": "uncertain"})
        original = old.read_bytes()
        with self.assertRaisesRegex(ValueError, "历史锁"):
            self.execute()
        result = self.execute(reviewed_lock_sha256=lock_inventory(self.lock)[1])
        self.assertEqual(result["status"], "property_observed")
        self.assertEqual(old.read_bytes(), original)
        self.assertFalse((self.lock / ACTIVE_CLAIM).exists())

    def test_mismatched_window_or_extra_source_change_retains_uncertainty(self):
        for wrong_window in (False, True):
            with self.subTest(wrong_window=wrong_window), tempfile.TemporaryDirectory() as folder, \
                    patch.dict(os.environ, {"EASYEDA_ASSIST_DATA_DIR": folder}):
                reply, window = self.bridge._execute_fixed.return_value
                reply = deepcopy(reply)
                if not wrong_window:
                    reply["observed"]["x"] = 999
                bridge = Mock()
                bridge.health.return_value = {"pendingRequests": 0}
                bridge.design_query.side_effect = [self.before, self.before_audit]
                bridge._execute_fixed.return_value = (reply, "other" if wrong_window else window)
                result = execute_trial(self.case_path, self.case_sha, "fixture", "message", lambda: bridge)
                self.assertEqual(result["status"], "uncertain")
                self.assertTrue((Path(result["target_lock"]) / ACTIVE_CLAIM).is_file())

    def test_stale_export_repeats_only_reads_and_retains_observation_failure(self):
        stale_source, stale_audit = evidence(value="trial", netlist_value="old")
        self.bridge.design_query.side_effect = [self.before, self.before_audit] + [stale_source, stale_audit] * 6
        result = self.execute()
        self.assertEqual(result["status"], "observation_failed")
        self.assertTrue((self.lock / ACTIVE_CLAIM).exists())
        self.assertEqual(self.bridge._execute_fixed.call_count, 1)
        self.assertEqual(self.bridge.design_query.call_count, 14)

    def test_blocked_before_write_keeps_claim_history_but_releases_active_marker(self):
        self.bridge._execute_fixed.return_value = ({"status": "blocked", "attempted": False, "error": "stale"}, "w")
        result = self.execute()
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["attempted"])
        self.assertFalse(self.lock.exists())
        self.assertTrue((Path(result["run_dir"]) / "claim.json").is_file())

    def test_explicit_state_review_closes_only_experimental_claim_and_preserves_old_result(self):
        old = write_new(self.lock / "owner.json", {"status": "uncertain", "run_dir": "old"})
        old_bytes = old.read_bytes()
        self.bridge._execute_fixed.side_effect = TimeoutError("lost response")
        result = self.execute(reviewed_lock_sha256=lock_inventory(self.lock)[1])
        run = Path(result["run_dir"])
        original_result = (run / "result.json").read_bytes()
        source = write_new(self.root / "review-source.json", self.after)
        audit = write_new(self.root / "review-audit.json", self.after_audit)
        review, sha = review_trial_state(run, source, audit)
        self.assertTrue(review["source_changes"])
        with self.assertRaisesRegex(ValueError, "复核摘要"):
            close_reviewed_trial(run, source, audit, "wrong", "reviewed", "fixture", lambda: self.bridge)
        self.bridge.design_query.side_effect = [self.after, self.after_audit]
        self.after_audit["captured_at"] = "a-new-read-time"
        path = close_reviewed_trial(run, source, audit, sha, "explicitly reviewed fixture difference", "fixture", lambda: self.bridge)
        self.assertEqual(read_json(path)["status"], "reviewed-known-state")
        self.assertEqual((run / "result.json").read_bytes(), original_result)
        self.assertEqual(old.read_bytes(), old_bytes)
        self.assertFalse((self.lock / ACTIVE_CLAIM).exists())

    def test_review_of_stale_evidence_or_live_drift_cannot_release_claim(self):
        self.bridge._execute_fixed.side_effect = TimeoutError("lost")
        result = self.execute()
        run = Path(result["run_dir"])
        source = write_new(self.root / "review-source.json", self.after)
        audit = write_new(self.root / "review-audit.json", self.after_audit)
        with self.assertRaisesRegex(ValueError, "尚未观察"):
            review_trial_state(run, run / 'source-before.json', run / 'audit-before.json')
        sha = review_trial_state(run, source, audit)[1]
        changed, changed_audit = evidence(value="trial", net="new")
        self.bridge.design_query.side_effect = [changed, changed_audit]
        with self.assertRaisesRegex(ValueError, "现场已不符合"):
            close_reviewed_trial(run, source, audit, sha, "reviewed", "fixture", lambda: self.bridge)
        self.assertTrue((self.lock / ACTIVE_CLAIM).exists())
        failed_captures = sorted((run / "reviews").iterdir())
        self.assertEqual(len(failed_captures), 1)
        failed_bytes = (failed_captures[0] / "audit.json").read_bytes()
        self.bridge.design_query.side_effect = [self.after, self.after_audit]
        path = close_reviewed_trial(run, source, audit, sha, "reviewed same fixture state", "fixture", lambda: self.bridge)
        self.assertNotEqual(Path(read_json(path)["review_evidence_dir"]), failed_captures[0])
        self.assertEqual((failed_captures[0] / "audit.json").read_bytes(), failed_bytes)

    def test_z_index_side_effect_remains_uncertain_even_with_expected_name_and_netlist(self):
        before, before_audit = evidence(z_index=16)
        after, after_audit = evidence(value="trial", z_index=15)
        source = write_new(self.root / "z-before-source.json", before)
        audit = write_new(self.root / "z-before-audit.json", before_audit)
        prepared = prepare_validation(source, audit, "Name", "trial")
        self.bridge.design_query.side_effect = [before, before_audit]
        reply, window = self.bridge._execute_fixed.return_value
        reply["source"] = after["data"]["source"]
        result = execute_trial(prepared["validation_path"], prepared["case_sha256"], "fixture", "message", lambda: self.bridge)
        self.assertEqual(result["status"], "uncertain")
        self.assertIn("方案外源差异", result["error"])
        self.assertEqual(self.bridge._execute_fixed.call_count, 1)
        run = Path(result["run_dir"])
        original = (run / "result.json").read_bytes()
        source_after = write_new(self.root / "z-after-source.json", after)
        audit_after = write_new(self.root / "z-after-audit.json", after_audit)
        review, sha = review_trial_state(run, source_after, audit_after)
        self.assertEqual(len(review["source_changes"]), 1)
        changed = review["source_changes"][0]
        self.assertEqual((changed["before"][1]["zIndex"], changed["after"][1]["zIndex"]), (16, 15))
        self.bridge.design_query.side_effect = [after, after_audit]
        path = close_reviewed_trial(run, source_after, audit_after, sha, "reviewed exact Name and zIndex delta", "fixture", lambda: self.bridge)
        self.assertFalse(read_json(path)["verification_passed"])
        self.assertEqual((run / "result.json").read_bytes(), original)
        self.assertFalse(ADAPTERS[SOURCE]["dispatch_enabled"])

    def test_restore_against_reviewed_baseline_does_not_hide_original_z_index_residue(self):
        trial, trial_audit = evidence(value="trial", z_index=15)
        restored, restored_audit = evidence(z_index=15)
        original, _ = evidence(z_index=16)
        source = write_new(self.root / "restore-source.json", trial)
        audit = write_new(self.root / "restore-audit.json", trial_audit)
        prepared = prepare_validation(source, audit, "Name", "old")
        reply, window = self.bridge._execute_fixed.return_value
        reply.update(source=restored["data"]["source"], original=trial["data"]["outputs"]["component"]["value"],
                     observed=restored["data"]["outputs"]["component"]["value"])
        self.bridge.design_query.side_effect = [trial, trial_audit, restored, restored_audit]
        result = execute_trial(prepared["validation_path"], prepared["case_sha256"], "fixture restore authorization", "fixture", lambda: self.bridge)
        self.assertEqual(result["status"], "property_observed")
        self.assertFalse(result["drc_freshness_established"])
        old, new = records(original["data"]["source"]), records(restored["data"]["source"])
        self.assertNotEqual(old, new)
        self.assertEqual([rid for rid in old if old[rid] != new[rid]], ["a3"])
        self.assertEqual(new["a3"][1]["value"], old["a3"][1]["value"])
        self.assertEqual((old["a3"][1]["zIndex"], new["a3"][1]["zIndex"]), (16, 15))

    def test_trial_cli_preserves_failure_exit_status_and_authorization(self):
        args = ["execute-source-trial", str(self.case_path), "--shown-sha256", self.case_sha,
                "--user-message", "fixture authorization", "--reference", "fixture-message",
                "--reviewed-lock-sha256", "reviewed-fixture", "--port", "49620"]
        for status, expected in (("property_observed", 0), ("observation_failed", 3), ("uncertain", 3), ("blocked", 3)):
            output = io.StringIO()
            with self.subTest(status=status), patch("easyeda_assist.cli.execute_trial", return_value={"status": status}) as trial, redirect_stdout(output):
                self.assertEqual(main(args), expected)
            self.assertEqual(json.loads(output.getvalue())["status"], status)
            self.assertEqual(trial.call_args.args[:4], (self.case_path, self.case_sha, "fixture authorization", "fixture-message"))
            self.assertEqual(trial.call_args.args[5], "reviewed-fixture")
            self.assertEqual(trial.call_args.kwargs["backend"], "canvas-attribute")

    def test_canvas_backend_is_explicit_journaled_and_never_changes_normal_dispatch(self):
        reply, window = self.bridge._execute_fixed.return_value
        reply["backend"] = "canvas-attribute"
        result = self.execute(backend="canvas-attribute")
        self.assertEqual(result["status"], "property_observed")
        self.assertEqual(result["backend"], "canvas-attribute")
        run = Path(result["run_dir"])
        for name in ("authorization.json", "claim.json", "intent.json", "result.json"):
            self.assertEqual(read_json(run / name)["backend"], "canvas-attribute")
        script = self.bridge._execute_fixed.call_args.args[0]
        self.assertIn("sch_PrimitiveAttribute.modify(", script)
        self.assertNotIn("sch_SourcePrimitiveComponent.update(", script)
        self.assertFalse(ADAPTERS[SOURCE]["dispatch_enabled"])

    def test_canvas_backend_mismatch_is_uncertain_and_cannot_fall_back(self):
        result = self.execute(backend="canvas-attribute")
        self.assertEqual(result["status"], "uncertain")
        self.assertIn("后端不符", result["error"])
        self.assertEqual(self.bridge._execute_fixed.call_count, 1)
        self.assertTrue((self.lock / ACTIVE_CLAIM).exists())


if __name__ == "__main__":
    unittest.main()
