from __future__ import annotations

from copy import deepcopy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

from easyeda_assist.cli import main
from easyeda_assist.project import (PROJECT_STATES, new_project, validate_project, stage_guide,
                                    project_impact, project_review, revise_project, pending_reviews,
                                    DRAWING_OPERATIONS)
from easyeda_assist.storage import digest, read_json
from easyeda_assist.tasks import create_task
from easyeda_assist.drawing import new_drawing_plan, review_drawing_plan


def design():
    return {
        "schema_version": 1, "id": "p1", "name": "fixture", "goal": "fixture only",
        "created_at": "now", "updated_at": "now", "status": "framing", "scope": [],
        "requirements": [{"id": "R1", "statement": "fixture voltage", "source": "fixture",
                          "priority": "must", "verification_method": "test", "status": "open"}],
        "modules": [{"id": "M1"}, {"id": "M2"}],
        "interfaces": [{"id": "I1", "modules": ["M1", "M2"]}],
        "budgets": [{"id": "B1", "modules": ["M1"]}], "decisions": [],
        "tasks": [{"id": "T1", "status": "ready", "requirement_ids": ["R1"],
                   "module_ids": ["M1"], "depends_on": []},
                  {"id": "T2", "status": "blocked", "depends_on": ["T1"]}],
        "verifications": [{"id": "V1", "requirement_ids": ["R1"],
                           "method": "test", "result": "planned"}]
    }


class ProjectTests(unittest.TestCase):
    def test_offline_cli_creates_external_draft_and_checks_it(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"EASYEDA_ASSIST_DATA_DIR": folder}):
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(["new-project", "--name", "测试", "--goal", "验证离线建档"])
            self.assertEqual(code, 0)
            path = Path(json.loads(output.getvalue())["project_path"])
            self.assertTrue(path.is_relative_to(Path(folder)))
            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(record["status"], "framing")
            self.assertEqual(record["requirements"], [])
            self.assertEqual(record["drawing_policy"], {
                "mode": "explicit_batch", "trigger": "explicit_user_request",
                "allowed_operations": sorted(DRAWING_OPERATIONS), "require_batch_approval": True,
                "require_snapshot_binding": True})
            self.assertTrue((path.parent / "README.md").is_file())
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(["validate-project", str(path)]), 0)

    def test_missing_references_are_rejected_across_objects(self):
        validate_project(design())
        for section, key in [("interfaces", "modules"), ("budgets", "modules"),
                             ("tasks", "module_ids"), ("tasks", "requirement_ids"),
                             ("tasks", "depends_on"), ("verifications", "requirement_ids")]:
            record = deepcopy(design())
            record[section][0][key] = ["missing"]
            with self.subTest(section=section, key=key), self.assertRaisesRegex(ValueError, "不存在"):
                validate_project(record)

    def test_dependency_cycle_and_self_cycle_are_rejected(self):
        for dependency in ("T1", "T2"):
            record = design()
            record["tasks"][0]["depends_on"] = [dependency]
            with self.subTest(dependency=dependency), self.assertRaisesRegex(ValueError, "循环依赖"):
                validate_project(record)

    def test_duplicate_objects_and_interface_endpoints_are_rejected(self):
        record = design()
        record["modules"].append({"id": "M1"})
        with self.assertRaisesRegex(ValueError, "不得重复"):
            validate_project(record)
        record = design()
        record["interfaces"][0]["modules"] = ["M1", "M1"]
        with self.assertRaisesRegex(ValueError, "重复引用"):
            validate_project(record)

    def test_invalid_project_cli_reports_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "project.json"
            record = design()
            record["tasks"][1]["depends_on"] = ["missing"]
            path.write_text(json.dumps(record), encoding="utf-8")
            with redirect_stderr(io.StringIO()):
                self.assertEqual(main(["validate-project", str(path)]), 2)

    def test_blank_name_is_rejected_before_artifacts_are_created(self):
        with self.assertRaisesRegex(ValueError, "不能为空"):
            new_project(" ", "goal")

    def test_drawing_policy_requires_explicit_trigger_approval_and_allowlist(self):
        record = design()
        record["drawing_policy"] = {"mode": "draft_only", "trigger": "explicit_user_request",
                                     "allowed_operations": [], "require_batch_approval": True,
                                     "require_snapshot_binding": True}
        validate_project(record)
        for field, value, message in (("trigger", "task_done", "触发条件"),
                                      ("require_batch_approval", False, "明确授权"),
                                      ("require_snapshot_binding", False, "绑定"),
                                      ("allowed_operations", ["delete_project"], "无效操作"),
                                      ("allowed_operations", [{}], "无效操作"),
                                      ("mode", {}, "mode"),
                                      ("allowed_operations", ["add_wire"], "仅 explicit_batch")):
            changed = deepcopy(record)
            changed["drawing_policy"][field] = value
            with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, message):
                validate_project(changed)
        record["drawing_policy"].update(mode="explicit_batch", allowed_operations=["add_wire"])
        validate_project(record)

    def test_offline_drawing_plan_checks_scope_without_enabling_execution(self):
        record = design()
        record["drawing_policy"] = {"mode": "explicit_batch", "trigger": "explicit_user_request",
                                     "allowed_operations": ["place_component"], "require_batch_approval": True,
                                     "require_snapshot_binding": True}
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"EASYEDA_ASSIST_DATA_DIR": folder}), \
                patch("easyeda_assist.cli.discover", side_effect=AssertionError("offline must not access bridge")):
            project_path = Path(folder) / "project.json"
            project_path.write_text(json.dumps(record), encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["new-drawing-plan", str(project_path), "--request", "fixture"]), 0)
            path = Path(json.loads(output.getvalue())["plan_path"])
            plan = read_json(path)
            self.assertTrue(review_drawing_plan(record, plan)["findings"])
            plan["target"] = {"window_id": "w", "project_uuid": "p", "document_uuid": "d",
                              "domain": "schematic", "snapshot_path": "fixture.json", "snapshot_sha256": "a" * 64}
            plan["operations"] = [{"id": "o1", "operation": "place_component", "target": "R1",
                                   "before": "absent", "after": "fixture", "expected_result": "fixture"}]
            plan.update(evidence=["fixture"], readback_plan="fixture", failure_plan="stop")
            report = review_drawing_plan(record, plan)
            self.assertTrue(report["record_complete"])
            self.assertTrue(report["within_policy_scope"])
            self.assertFalse(report["execution_available"])
            plan["approval"] = {"plan_sha256": report["plan_sha256"]}
            approved = review_drawing_plan(record, plan)
            self.assertFalse(approved["approval_verified"])
            self.assertEqual(approved["plan_sha256"], report["plan_sha256"])
            plan["operations"][0]["operation"] = "add_wire"
            changed = review_drawing_plan(record, plan)
            self.assertNotEqual(changed["plan_sha256"], report["plan_sha256"])
            self.assertFalse(changed["within_policy_scope"])
            self.assertTrue(any("不在项目允许" in x for x in changed["policy_findings"]))
            # Legacy projects permit draft review but do not acquire write policy implicitly.
            self.assertEqual(review_drawing_plan(design(), plan)["policy_mode"], "draft_only")
            for field, value, message in (("operation", {}, "操作类型"), ("id", "", "缺少 id")):
                malformed = deepcopy(plan)
                malformed["operations"][0][field] = value
                self.assertTrue(any(message in x for x in review_drawing_plan(record, malformed)["findings"]))
            duplicate = deepcopy(plan)
            duplicate["operations"].append(deepcopy(duplicate["operations"][0]))
            duplicate["unresolved"] = ["fixture unknown"]
            findings = review_drawing_plan(record, duplicate)["findings"]
            self.assertTrue(any("重复" in x for x in findings))
            self.assertTrue(any("未解决" in x for x in findings))
            plan_path = Path(folder) / "edited-plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(["review-drawing-plan", str(project_path), str(plan_path)]), 0)
            plan["project_id"] = "other"
            with self.assertRaisesRegex(ValueError, "项目 ID"):
                review_drawing_plan(record, plan)

    def test_drawing_off_prevents_new_artifacts_and_policy_changes_are_tracked(self):
        record = design()
        changed = deepcopy(record)
        changed["drawing_policy"] = {"mode": "off", "trigger": "explicit_user_request",
                                     "allowed_operations": [], "require_batch_approval": True,
                                     "require_snapshot_binding": True}
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"EASYEDA_ASSIST_DATA_DIR": folder}):
            with self.assertRaisesRegex(ValueError, "关闭"):
                new_drawing_plan(changed, "fixture")
            self.assertEqual(list(Path(folder).iterdir()), [])
            plan = read_json(new_drawing_plan(record, "fixture"))
            self.assertFalse(review_drawing_plan(changed, plan)["within_policy_scope"])
        self.assertIn("drawing_policy", project_impact(record, changed)["global_changes"])

    def test_all_stage_guides_and_missing_section(self):
        for stage in PROJECT_STATES:
            self.assertIn("目标", stage_guide(stage))
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["project-guide", "--stage", stage]), 0)
            self.assertEqual(json.loads(output.getvalue())["stage"], stage)
        with tempfile.TemporaryDirectory() as folder:
            docs = Path(folder) / "docs"
            docs.mkdir()
            (docs / "project-stages.md").write_text("# empty", encoding="utf-8")
            with patch("easyeda_assist.project.PROJECT_ROOT", Path(folder)):
                with self.assertRaisesRegex(ValueError, "缺少阶段指引"):
                    stage_guide("framing")

    def test_impact_propagates_old_and_new_explicit_dependencies(self):
        previous = design()
        previous["modules"][0]["requirement_ids"] = ["R1"]
        previous["decisions"] = [{"id": "D1", "status": "selected", "interface_ids": ["I1"]}]
        current = deepcopy(previous)
        current["modules"][0].pop("requirement_ids")
        current["requirements"][0]["statement"] = "revised fixture"
        report = project_impact(previous, current)
        self.assertEqual(set(report["affected"]), {
            "requirements/R1", "modules/M1", "interfaces/I1", "budgets/B1",
            "tasks/T1", "tasks/T2", "verifications/V1", "decisions/D1"})
        current = deepcopy(previous)
        current["goal"] = "changed goal"
        self.assertEqual(len(project_impact(previous, current)["affected"]), 9)
        current["id"] = "another"
        with self.assertRaisesRegex(ValueError, "同一项目"):
            project_impact(previous, current)

    def test_old_minimal_record_review_reports_gaps_without_approval(self):
        record = design()
        validate_project(record)
        report = project_review(record)
        self.assertEqual(report["engineering_approval"], "not_assessed")
        self.assertTrue(report["tasks"][1]["blocked_by"])
        self.assertTrue(report["findings"])
        self.assertIn("integration_record", " ".join(x["message"] for x in report["findings"]))
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"EASYEDA_ASSIST_DATA_DIR": folder}):
            path = new_project("draft", "fixture only")
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["project-review", str(path)]), 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["stage_record_reviews"]["framing"]["record_review"], "needs_work")

    def test_review_is_invalidated_by_upstream_change_even_if_consumer_unchanged(self):
        record = design()
        record["pending_reviews"] = ["tasks/T2"]
        review_hash = project_review(record)["review_targets"]["tasks/T2"]
        record["review_records"] = [{"object": "tasks/T2", "record": "fixture review",
                                      "reviewed_against": review_hash}]
        self.assertEqual(pending_reviews(record), [])
        record["tasks"][0]["goal"] = "changed upstream"
        self.assertEqual(pending_reviews(record), ["tasks/T2"])

    def test_removed_objects_can_be_reviewed_and_archives_preserve_exact_inputs(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"EASYEDA_ASSIST_DATA_DIR": folder}):
            before, edited = Path(folder) / "before.json", Path(folder) / "edited.json"
            previous = design()
            previous["pending_reviews"] = ["modules/M2"]
            current = deepcopy(previous)
            current["interfaces"] = []
            before.write_text(json.dumps(previous), encoding="utf-8-sig")
            edited.write_text(json.dumps(current), encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["project-impact", str(before), str(edited)]), 0)
            self.assertIn("interfaces/I1", json.loads(output.getvalue())["removed"])
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["project-revise", str(before), str(edited), "--reason", "fixture change"]), 0)
            path = Path(json.loads(output.getvalue())["project_path"])
            self.assertEqual((path.parent / "previous-project.json").read_bytes(), before.read_bytes())
            self.assertEqual((path.parent / "edited-project.json").read_bytes(), edited.read_bytes())
            for item in read_json(path.parent / "manifest.json")["files"]:
                self.assertEqual(digest(path.parent / item["path"]), item["sha256"])
            revised = read_json(path)
            self.assertIn("modules/M2", pending_reviews(revised))
            self.assertIn("interfaces/I1", pending_reviews(revised))
            for key, target in project_review(revised)["review_targets"].items():
                revised.setdefault("review_records", []).append({"object": key, "record": "fixture review", "reviewed_against": target})
            self.assertEqual(pending_reviews(revised), [])

    def test_linked_tasks_report_missing_id_hash_and_survive_revision_relocation(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"EASYEDA_ASSIST_DATA_DIR": folder}):
            task_path = create_task("selection", "fixture only", [])
            path = Path(folder) / "project.json"
            record = design()
            task = record["tasks"][0]
            task.update(task_path=str(task_path.relative_to(Path(folder))),
                        task_id=read_json(task_path)["id"], task_sha256=digest(task_path))
            self.assertEqual(project_review(record, path)["tasks"][0]["linked_task"], "matched")
            self.assertEqual(project_review(record)["tasks"][0]["linked_task"], "needs_review")
            for field, value in (("task_path", "missing.json"), ("task_id", "wrong"), ("task_sha256", "0" * 64)):
                changed = deepcopy(record)
                changed["tasks"][0][field] = value
                self.assertEqual(project_review(changed, path)["tasks"][0]["linked_task"], "needs_review")
            path.write_text(json.dumps(record), encoding="utf-8")
            revised_path = revise_project(path, path, "fixture archive")
            self.assertEqual(project_review(read_json(revised_path), revised_path)["tasks"][0]["linked_task"], "matched")
            task_path.write_text("{}", encoding="utf-8")
            self.assertEqual(project_review(record, path)["tasks"][0]["linked_task"], "needs_review")

    def test_partial_task_link_is_rejected(self):
        record = design()
        record["tasks"][0]["task_path"] = "task.json"
        with self.assertRaisesRegex(ValueError, "需同时提供"):
            validate_project(record)


if __name__ == "__main__":
    unittest.main()
