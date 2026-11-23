"""Audit differences must preserve ambiguity, identity and stale-read limits."""
from copy import deepcopy
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from easyeda_assist.cli import main
from easyeda_assist.design_audit import compare_audits, compare_files, load_comparison, make_audit
from easyeda_assist.drawing_execution import approve_plan, execute_plan, preflight
from easyeda_assist.feedback import drawing_feedback
from easyeda_assist.project import affected_objects
from easyeda_assist.queries import read_query
from easyeda_assist.storage import digest, read_json, write_new
from easyeda_assist.traceability import make_origin, plan_trace
import test_drawing_execution as execution_fixtures
from test_drawing_execution import snapshot
from test_workflow_build import advice


def violation(primitive="pin-1", level="warn", net="N"):
    return {"rule": "IN-IN", "type": level, "net": net,
            "primitives": [{"primitiveId": primitive, "sheet": "d", "designator": "U1", "name": "1"}]}


def audit(source="fixture-source", errors=(), net="N", component_id="stable-cid", designator="U1", version="4.1.100"):
    before = {**snapshot(), "source": source}
    raw = json.dumps({"version": "2.0.0", "components": {component_id: {
        "props": {"Designator": designator, "Value": "fixture"},
        "pinInfoMap": {"pin-internal-id": {"number": "1", "net": net}}}}})
    return make_audit(before, {"identity": before["identity"], "window_id": before["window_id"],
        "source": source, "raw_netlist": raw, "drc": list(errors), "editor_version": version}, "a" * 64)


class AuditDifferenceTests(unittest.TestCase):
    def test_capture_rejects_target_window_and_source_changes(self):
        for field, value in (("identity", {**snapshot()["identity"], "document_uuid": "other"}),
                             ("window_id", "other"), ("source", "edited-during-capture")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                make_audit(snapshot(), {**audit(), field: value}, "a" * 64)

    def test_unchanged_old_models_never_prove_freshness_after_source_change(self):
        before = audit(errors=[violation()])
        after = audit(source="edited-source", errors=[violation()])
        delta = compare_audits(before, after)
        self.assertTrue(delta["source_changed"])
        self.assertTrue(delta["no_observed_regressions"])
        self.assertFalse(delta["freshness_established"])
        self.assertFalse(delta["verification_passed"])
        self.assertFalse(delta["engineering_verified"])

    def test_same_drc_count_with_different_objects_is_added_and_removed(self):
        delta = compare_audits(audit(errors=[violation("pin-old")]), audit(errors=[violation("pin-new")]))["drc"]
        self.assertEqual(len(delta["added"]), 1)
        self.assertEqual(len(delta["removed"]), 1)
        self.assertFalse(delta["no_new_violations"])

    def test_severity_net_and_unknown_field_changes_are_preserved(self):
        for changed in (violation(level="error"), violation(net="other"), {**violation(), "newField": "unknown"}):
            with self.subTest(changed=changed):
                delta = compare_audits(audit(errors=[violation()]), audit(errors=[changed]))["drc"]
                self.assertEqual(len(delta["changed"]), 1)
                self.assertEqual(delta["changed"][0]["after"], changed)
                self.assertFalse(delta["no_new_violations"])

    def test_primitive_order_does_not_change_a_violation(self):
        error = violation()
        error["primitives"].append({"primitiveId": "pin-2", "sheet": "d"})
        reverse = {**error, "primitives": list(reversed(error["primitives"]))}
        delta = compare_audits(audit(errors=[error]), audit(errors=[reverse]))["drc"]
        self.assertEqual(len(delta["unchanged"]), 1)
        self.assertTrue(delta["no_new_violations"])

    def test_summary_missing_sheet_and_duplicate_candidates_remain_unmatched(self):
        no_sheet = violation()
        no_sheet["primitives"][0].pop("sheet")
        cases = [([{"type": "warn", "count": 2}], [{"type": "warn", "count": 2}]),
                 ([no_sheet], [no_sheet]),
                 ([violation(net="A"), violation(net="B")], [violation(net="C")])]
        for left, right in cases:
            with self.subTest(left=left):
                delta = compare_audits(audit(errors=left), audit(errors=right))["drc"]
                self.assertTrue(delta["unmatched"]["before"])
                self.assertFalse(delta["no_new_violations"])

    def test_removed_violation_does_not_require_empty_baseline(self):
        delta = compare_audits(audit(errors=[violation()]), audit())["drc"]
        self.assertEqual(len(delta["removed"]), 1)
        self.assertTrue(delta["no_new_violations"])

    def test_exact_duplicate_violations_preserve_multiplicity_and_residual_ambiguity(self):
        before = [violation(net="A")] * 30 + [violation(net="B"), violation(net="C")]
        after = [violation(net="A")] * 30 + [violation(net="D")]
        delta = compare_audits(audit(errors=before), audit(errors=after))["drc"]
        self.assertEqual(len(delta["unchanged"]), 30)
        self.assertEqual(len(delta["unmatched"]["before"]), 2)
        self.assertEqual(len(delta["unmatched"]["after"]), 1)
        self.assertEqual(delta["changed"], [])
        self.assertFalse(delta["no_new_violations"])

    def test_pins_use_component_identity_and_pin_number_not_designator(self):
        delta = compare_audits(audit(), audit(net="OTHER", designator="U2"))["connectivity"]
        self.assertEqual(delta["changed_pins"], [{"component_id": "stable-cid", "pin": "1", "before": "N", "after": "OTHER"}])
        self.assertEqual(delta["renamed_components"][0]["after"], "U2")
        delta = compare_audits(audit(), audit(component_id="replacement"))["connectivity"]
        self.assertEqual(delta["removed_components"], ["stable-cid"])
        self.assertEqual(delta["added_components"], ["replacement"])
        self.assertFalse(delta["unchanged"])

    def test_cross_target_rejected_and_version_change_requires_new_baseline(self):
        before, after = audit(), audit(version="future")
        delta = compare_audits(before, after)
        self.assertFalse(delta["comparable"])
        self.assertIsNone(delta["drc"])
        self.assertFalse(delta["no_observed_regressions"])
        after["identity"]["document_uuid"] = "other"
        with self.assertRaisesRegex(ValueError, "不同工程或图页"):
            compare_audits(before, after)

    def test_blank_and_duplicate_pin_numbers_cannot_be_compared(self):
        for number in (" ", "1"):
            record = audit()
            raw = json.loads(record["raw_netlist"])
            raw["components"]["stable-cid"]["pinInfoMap"]["other-pin"] = {"number": number, "net": "OTHER"}
            record = make_audit(snapshot(), {**record, "raw_netlist": json.dumps(raw)}, "a" * 64)
            with self.subTest(number=number), self.assertRaisesRegex(ValueError, "编号缺失或重复"):
                compare_audits(audit(), record)

    def test_tampered_hash_settings_and_freshness_claim_rejected(self):
        for changes in ({"source": "changed"}, {"raw_netlist": "{}"}, {"drc_settings": {}},
                        {"freshness": {"source": "checked-before-after", "derived": "verified"}}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                compare_audits(audit(), {**audit(), **changes})

    def test_archive_retains_exact_inputs_and_recomputes_report(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"EASYEDA_ASSIST_DATA_DIR": folder}):
            before, after = Path(folder) / "old.json", Path(folder) / "new.json"
            before.write_text(json.dumps(audit()), encoding="utf-8-sig")
            after.write_text(json.dumps(audit(source="edited")), encoding="utf-8")
            saved = compare_files(before, after, save=True)
            report = Path(saved["comparison_path"])
            self.assertEqual((report.parent / "before.json").read_bytes(), before.read_bytes())
            self.assertEqual(load_comparison(report)[0], saved["comparison"])
            body = read_json(report)
            body["comparison"]["verification_passed"] = True
            report.write_text(json.dumps(body), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "结论与原始输入"):
                load_comparison(report)

    def test_unified_audit_query_keeps_raw_detail_and_marks_freshness_missing(self):
        bridge = Mock()
        bridge.drawing_snapshot.return_value = snapshot()
        record = audit(errors=[violation()])
        bridge.drawing_circuit_audit.return_value = {**record, "drc_passed": False}
        query = read_query(bridge, "audit", "w")
        self.assertEqual(query["data"]["drc"], record["drc"])
        self.assertIn("derived_freshness", query["missing_fields"])
        bridge.drawing_circuit_audit.assert_called_once_with(snapshot())
        self.assertTrue(compare_audits(query, query)["no_observed_regressions"])
        for field in ("target", "provenance"):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "外壳"):
                compare_audits({**query, field: None}, record)
        query["target"]["identity"] = {**query["target"]["identity"], "document_uuid": "wrong"}
        with self.assertRaisesRegex(ValueError, "外壳"):
            compare_audits(query, record)

    def test_archive_input_corruption_is_rejected_before_recomputation(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"EASYEDA_ASSIST_DATA_DIR": folder}):
            before = write_new(Path(folder) / "old.json", audit())
            after = write_new(Path(folder) / "new.json", audit())
            report = Path(compare_files(before, after, save=True)["comparison_path"])
            archived = report.parent / "after.json"
            archived.write_bytes(archived.read_bytes() + b"\n")
            with self.assertRaisesRegex(ValueError, "输入摘要不一致"):
                load_comparison(report)

    def test_cli_comparison_is_offline(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"EASYEDA_ASSIST_DATA_DIR": folder}), \
                patch("easyeda_assist.cli.discover", side_effect=AssertionError("offline")):
            before = write_new(Path(folder) / "a.json", audit())
            after = write_new(Path(folder) / "b.json", audit(errors=[violation()]))
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(["compare-design-audits", str(before), str(after), "--save"]), 0)
            delta = json.loads(out.getvalue())
            self.assertFalse(delta["comparison"]["no_observed_regressions"])
            self.assertTrue(Path(delta["comparison_path"]).is_file())


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        execution_fixtures.ExecutionTests.setUp(self)
        self.task_path = write_new(self.root / "task-origin.json", advice())
        self.project["decisions"] = [{"id": "D1", "status": "selected", "statement": "fixture"}]
        self.project["tasks"][0].update(decision_ids=["D1"], task_id="t1",
                                       task_path=str(self.task_path), task_sha256=digest(self.task_path))
        self.plan.update(schema_version=2, interaction="collaborative",
                         origin=make_origin(self.project, self.task_path, ["r1"], ["D1"]))

    def execute(self, uncertain=False):
        from test_drawing_execution import reply
        checked, _ = preflight(self.project, self.plan)
        approval = approve_plan(self.project, self.plan, checked["binding"]["plan_sha256"], "fixture", "test")
        bridge = Mock()
        bridge._place_component.side_effect = TimeoutError("response lost") if uncertain else lambda before, params: reply(before, params)
        return execute_plan(self.project, self.plan, approval, lambda: bridge)

    def test_feedback_preserves_user_decision_and_validation_items(self):
        original = self.task_path.read_bytes()
        self.execute()
        project = deepcopy(self.project)
        path = drawing_feedback(self.project, self.plan)
        feedback = read_json(path)
        self.assertEqual(feedback["execution_status"], "verified")
        self.assertEqual(feedback["original_validation_items"], [{"recommendation_id": "r1", "validation": ["fixture"]}])
        self.assertFalse(feedback["engineering_verified"])
        self.assertTrue(feedback["needs_review"])
        self.assertIn("capture_audit_comparison", {item["action"] for item in feedback["next_actions"]})
        self.assertEqual(self.task_path.read_bytes(), original)
        self.assertEqual(self.project, project)
        self.assertIn(str(path), plan_trace(self.project, self.plan)["feedback_paths"])

    def test_uncertain_execution_retains_lock_and_reopens_explicit_dependents(self):
        result = self.execute(uncertain=True)
        feedback = read_json(drawing_feedback(self.project, self.plan))
        self.assertEqual(feedback["execution_status"], "uncertain")
        self.assertTrue(Path(result["target_lock"]).exists())
        targets = {item["object"] for item in feedback["review_targets"] if item["review_required"]}
        self.assertEqual(targets, {"decisions/D1", "tasks/T1", "tasks/T2"})
        self.assertIn("inspect_scene_and_request", {item["action"] for item in feedback["next_actions"]})
        self.assertEqual(affected_objects(self.project, ["decisions/D1"]), sorted(targets))

    def test_changed_origin_still_uses_frozen_validation_and_reports_revisit(self):
        self.execute()
        self.task_path.unlink()
        self.project["decisions"][0]["statement"] = "new constraints"
        feedback = read_json(drawing_feedback(self.project, self.plan))
        self.assertTrue(feedback["needs_review"])
        self.assertTrue(feedback["original_validation_items"])
        self.assertIn("revisit_origin", {item["action"] for item in feedback["next_actions"]})

    def test_bound_comparison_never_suppresses_unknown_freshness(self):
        result = self.execute()
        run = Path(result["run_dir"])
        before = read_json(run / "input-snapshot.json")
        after = read_json(run / "001-snapshot.json")
        left = write_new(self.root / "left.json", audit(source=before["source"]))
        right = write_new(self.root / "right.json", audit(source=after["source"]))
        report = Path(compare_files(left, right, save=True)["comparison_path"])
        feedback = read_json(drawing_feedback(self.project, self.plan, report))
        self.assertTrue(feedback["comparison"]["no_observed_regressions"])
        self.assertTrue(feedback["needs_review"])
        self.assertTrue(all(item["review_required"] for item in feedback["review_targets"]))
        self.assertIn("establish_derived_freshness", {item["action"] for item in feedback["next_actions"]})
        unrelated = write_new(self.root / "unrelated.json", audit(source="another execution"))
        wrong_report = Path(compare_files(left, unrelated, save=True)["comparison_path"])
        with self.assertRaisesRegex(ValueError, "未绑定"):
            drawing_feedback(self.project, self.plan, wrong_report)

    def test_unexecuted_feedback_does_not_claim_completion_or_create_approval(self):
        feedback = read_json(drawing_feedback(self.project, self.plan))
        self.assertIsNone(feedback["execution_status"])
        self.assertTrue(feedback["needs_review"])
        self.assertFalse((self.root / "drawing-approvals").exists())

    def test_cli_feedback_is_offline_and_does_not_modify_inputs(self):
        project_path = write_new(self.root / "project.json", self.project)
        plan_path = write_new(self.root / "plan.json", self.plan)
        original = [path.read_bytes() for path in (project_path, plan_path, self.task_path)]
        out = io.StringIO()
        with patch("easyeda_assist.cli.discover", side_effect=AssertionError("offline")), redirect_stdout(out):
            self.assertEqual(main(["drawing-feedback", str(project_path), str(plan_path)]), 0)
        feedback_path = Path(json.loads(out.getvalue())["feedback_path"])
        self.assertTrue(feedback_path.is_file())
        self.assertFalse(read_json(feedback_path)["engineering_verified"])
        self.assertEqual([path.read_bytes() for path in (project_path, plan_path, self.task_path)], original)


if __name__ == "__main__":
    unittest.main()
