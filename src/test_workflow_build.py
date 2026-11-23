"""Behavioural coverage for the first workflow/traceability build; no live CAD."""
from copy import deepcopy
import base64
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import Mock, patch

from easyeda_assist.cli import main
from easyeda_assist.contracts import validate_task
from easyeda_assist.drawing import new_drawing_plan, review_drawing_plan
from easyeda_assist.drawing_execution import preflight, approve_plan, execute_plan
from easyeda_assist.operations import ADAPTERS, SOURCE, V2, capabilities, adapter_for
from easyeda_assist.product import review_product
from easyeda_assist.project import SECTIONS, project_impact, object_digest, validate_project, review_digest, pending_reviews
from easyeda_assist.storage import read_json, write_new, digest
from easyeda_assist.tasks import create_task
from easyeda_assist.traceability import make_origin, plan_trace, execution_project_digest
from easyeda_assist.workflow import context, validate_workflow, workflow_guide
import test_drawing_execution as execution_fixtures


def advice():
    return {"schema_version": 1, "id": "t1", "mode": "schematic", "goal": "fixture",
            "created_at": "fixture", "status": "analyzing", "context": None, "constraints": [],
            "missing_inputs": [], "sources": [{"id": "s1", "kind": "calculation", "reference": "fixture",
            "locator": "fixture", "description": "fixture only"}], "recommendations": [{"id": "r1",
            "locator": "fixture", "statement": "fixture", "rationale": "fixture", "evidence": ["s1"],
            "applicability": ["fixture"], "assumptions": [], "risks": ["fixture"], "validation": ["fixture"],
            "status": "suggested", "confidence": "low", "decision": "pending"}]}


def product():
    return {"schema_version": 1, "id": "product", "boards": [{"id": "board", "project_uuid": "p",
            "revision": "A", "snapshot_sha256": "0" * 64,
            "connectors": [{"id": "J1", "pins": ["1", "2"]}]}],
            "instances": [{"id": "left", "board_id": "board"}, {"id": "right", "board_id": "board"}],
            "connections": [{"id": "c1", "a": {"instance": "left", "connector": "J1", "pin": "1"},
            "b": {"instance": "right", "connector": "J1", "pin": "2"}, "signal": "explicit-map",
            "voltage": "TBD", "return_path": "unknown", "cable_mapping": "drawing-1", "evidence": []}]}


class WorkflowTests(unittest.TestCase):
    def test_local_and_shared_budget_require_different_scopes(self):
        self.assertEqual(context(scope="component")["analysis_scope"], "component")
        self.assertEqual(context(scope="component", signals=["budget_conflict"])["analysis_scope"], "board")
        self.assertEqual(context(signals=["shared_power", "cross_board"])["analysis_scope"], "product")
        for interaction in ("advice", "collaborative"):
            self.assertFalse(workflow_guide(interaction, signals=["shared_power"])["cad_write_authorized"])

    def test_inconsistent_scope_and_unknown_signals_are_rejected(self):
        invalid = context(signals=["cross_board"])
        invalid["analysis_scope"] = "module"
        for state in (invalid, {"interaction": "advice"}):
            with self.assertRaises(ValueError):
                validate_workflow(state)
        with self.assertRaises(ValueError):
            context(signals=["guess-from-net-name"])

    def test_simple_workflow_is_offline_and_creates_no_records(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"EASYEDA_ASSIST_DATA_DIR": folder}), \
                patch("easyeda_assist.cli.discover", side_effect=AssertionError("must stay offline")):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["workflow", "--scope", "component", "--missing", "ratings"]), 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["interaction"], "advice")
            self.assertEqual(len(result["steps"]), 5)
            self.assertEqual(len(result["next_queries"]), 1)
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_task_keeps_engineering_mode_separate_from_intent_and_scope(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"EASYEDA_ASSIST_DATA_DIR": folder}):
            path = create_task("topology", "fixture", [], interaction="collaborative",
                               scope="component", signals=["cross_board"])
            task = read_json(path)
            self.assertEqual(task["mode"], "topology")
            self.assertEqual(task["workflow"]["interaction"], "collaborative")
            self.assertEqual(task["workflow"]["analysis_scope"], "product")
            brief = (path.parent / "brief.md").read_text(encoding="utf-8")
            self.assertIn("协同绘制（collaborative）", brief)
            self.assertIn("分析范围：product", brief)
            validate_task(task)
            del task["workflow"]
            validate_task(task)  # Old records remain readable.

    def test_newer_client_cannot_select_legacy_or_unvalidated_source(self):
        catalogue = capabilities("4.1.100")
        implementations = [impl for op in catalogue["operations"] for impl in op["implementations"]]
        current = {"schematic-incremental-v4", "schematic-branch-v5"}
        self.assertFalse(any(impl["available"] for impl in implementations
                             if impl["adapter"] not in current))
        self.assertTrue(all(impl["available"] for impl in implementations
                            if impl["adapter"] in current))
        with self.assertRaises(ValueError):
            adapter_for({"execution_contract": ""})
        catalogue["operations"][0]["implementations"][0]["limitations"].append("external mutation")
        self.assertNotIn("external mutation", ADAPTERS[V2]["limitations"])


class TraceExecutionTests(unittest.TestCase):
    def setUp(self):
        execution_fixtures.ExecutionTests.setUp(self)
        self.task_path = write_new(self.root / "task.json", advice())
        self.project["decisions"] = [{"id": "D1", "status": "selected", "statement": "fixture"}]
        self.plan.update(schema_version=2, interaction="collaborative",
                         origin=make_origin(self.project, self.task_path, ["r1"], ["D1"]))

    def approve(self):
        checked, _ = preflight(self.project, self.plan)
        return approve_plan(self.project, self.plan, checked["binding"]["plan_sha256"], "fixture assent", "test")

    def bridge(self):
        bridge = Mock()
        bridge._place_component.side_effect = lambda before, params: execution_fixtures.reply(before, params)
        return bridge

    def test_trace_runs_from_advice_through_plan_to_result_without_editing_task(self):
        raw_task = self.task_path.read_bytes()
        consent = self.approve()
        self.project["updated_at"] = "new progress time"
        self.project["tasks"][0]["status"] = "in_progress"
        result = execute_plan(self.project, self.plan, consent, self.bridge)
        self.assertEqual(result["status"], "verified")
        trace = plan_trace(self.project, self.plan)
        self.assertFalse(trace["needs_review"])
        self.assertEqual(trace["result"]["origin"], self.plan["origin"])
        self.assertEqual(self.task_path.read_bytes(), raw_task)
        evidence = read_json(Path(result["run_dir"]) / "origin-task-evidence.json")
        self.assertEqual(base64.b64decode(evidence["raw_base64"]), raw_task)
        self.assertEqual(result["engineering_validation"], "not-established-by-cad-execution")

    def test_design_changes_invalidate_v2_consent(self):
        consent = self.approve()
        for field, value in (("goal", "different goal"), ("unknown_new_constraint", "new constraint")):
            project = deepcopy(self.project)
            project[field] = value
            factory = Mock(side_effect=AssertionError("no CAD call"))
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "认可记录已失效"):
                execute_plan(project, self.plan, consent, factory)
            factory.assert_not_called()

    def test_changed_source_or_decision_reopens_review_and_blocks_execution(self):
        consent = self.approve()
        changed = advice()
        changed["sources"][0]["description"] = "new evidence"
        self.task_path.write_text(json.dumps(changed), encoding="utf-8")
        self.assertTrue(plan_trace(self.project, self.plan)["needs_review"])
        with self.assertRaisesRegex(ValueError, "任务或建议依据已变化"):
            execute_plan(self.project, self.plan, consent, self.bridge)
        self.task_path.write_text(json.dumps(advice()), encoding="utf-8")
        # Rebind exact bytes, then change a project decision.
        self.plan["origin"] = make_origin(self.project, self.task_path, ["r1"], ["D1"])
        self.project["decisions"][0]["statement"] = "different circuit"
        trace = plan_trace(self.project, self.plan)
        self.assertTrue(trace["needs_review"])
        with self.assertRaisesRegex(ValueError, "原工程决策已变化"):
            preflight(self.project, self.plan)

    def test_advice_mode_does_not_execute_and_task_acceptance_is_not_consent(self):
        self.plan["interaction"] = "advice"
        with self.assertRaisesRegex(ValueError, "collaborative"):
            preflight(self.project, self.plan)
        self.plan["interaction"] = "collaborative"
        with self.assertRaises(FileNotFoundError):
            execute_plan(self.project, self.plan, self.root / "missing-approval.json", self.bridge)

    def test_legacy_binding_and_preview_remain_strict(self):
        self.plan["schema_version"] = 1
        for key in ("interaction", "origin"):
            del self.plan[key]
        checked, _ = preflight(self.project, self.plan)
        self.assertNotIn("binding_version", checked["binding"])
        self.assertNotIn("capability", checked["preview"])
        self.assertEqual(checked["binding"]["project_sha256"], object_digest(self.project))
        consent = self.approve()
        self.project["updated_at"] = "changed"
        with self.assertRaisesRegex(ValueError, "认可记录已失效"):
            execute_plan(self.project, self.plan, consent, self.bridge)

    def test_link_creation_rejects_missing_inputs_and_unknown_recommendations(self):
        task = advice()
        task["missing_inputs"] = ["input voltage"]
        self.task_path.write_text(json.dumps(task), encoding="utf-8")
        for ids in (["r1"], ["missing"]):
            with self.assertRaises(ValueError):
                new_drawing_plan(self.project, "fixture", task_path=self.task_path, recommendation_ids=ids)

    def test_v2_digest_keeps_policy_workflow_and_task_dependencies(self):
        baseline = execution_project_digest(self.project)
        for mutate in (lambda p: p["drawing_policy"]["allowed_operations"].append("set_property"),
                       lambda p: p.update(workflow=context()),
                       lambda p: p["tasks"][1].update(depends_on=[])):
            project = deepcopy(self.project)
            mutate(project)
            self.assertNotEqual(baseline, execution_project_digest(project))


class ProductTests(unittest.TestCase):
    def test_optional_context_preserves_old_review_digest_until_explicitly_added(self):
        from test_project import design
        project = design()
        key = "tasks/T2"
        legacy = object_digest({"object": key, "design": {
            field: project.get(field, [] if field in SECTIONS else None)
            for field in (*SECTIONS, "name", "goal", "scope", "assumptions", "open_questions", "drawing_policy")}})
        self.assertEqual(review_digest(project, key), legacy)
        project["pending_reviews"] = [key]
        project["review_records"] = [{"object": key, "record": "historical review", "reviewed_against": legacy}]
        self.assertEqual(pending_reviews(project), [])
        for field, value in (("workflow", context()), ("product_context", product())):
            updated = deepcopy(project)
            updated[field] = value
            self.assertNotEqual(review_digest(updated, key), legacy)
            self.assertEqual(pending_reviews(updated), [key])

    def test_repeated_board_design_preserves_distinct_instances_and_explicit_pins(self):
        manifest = product()
        result = review_product(manifest)
        self.assertEqual(result["board_count"], 1)
        self.assertEqual(result["instance_count"], 2)
        self.assertFalse(result["engineering_verified"])
        self.assertEqual({x["missing"] for x in result["findings"]}, {"voltage", "return_path", "evidence"})

    def test_unknown_pins_and_same_endpoint_are_rejected(self):
        for mutate in (lambda p: p["connections"][0]["b"].update(pin="3"),
                       lambda p: p["connections"][0]["b"].update(instance="missing"),
                       lambda p: p["connections"][0].update(b=deepcopy(p["connections"][0]["a"]))):
            manifest = product()
            mutate(manifest)
            with self.assertRaises(ValueError):
                review_product(manifest)

    def test_board_revision_change_propagates_to_project_review(self):
        from test_project import design
        before = design()
        before["product_context"] = product()
        validate_project(before)
        after = deepcopy(before)
        after["product_context"]["boards"][0]["revision"] = "B"
        impact = project_impact(before, after)
        self.assertIn("product_context", impact["global_changes"])
        self.assertIn("interfaces/I1", impact["needs_review"])


if __name__ == "__main__":
    unittest.main()
