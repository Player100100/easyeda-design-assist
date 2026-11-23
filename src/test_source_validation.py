"""Offline validation preparation must never enable or execute CAD writes."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from easyeda_assist.cli import main
from easyeda_assist.design_audit import make_audit
from easyeda_assist.operations import ADAPTERS, SOURCE
from easyeda_assist.source_validation import check_validation, check_roundtrip, load_case, make_case, prepare_validation
from easyeda_assist.storage import read_json, write_new
from test_drawing_execution import snapshot
from test_drawing_properties import line


def evidence(value="old", netlist_value=None, source_value=None, x=10, net="N", errors=(), z_index=None):
    attrs = {"Unique ID": "uid", "Designator": "U1", "Device": "device", "Name": value, "Supplier Part": "keep"}
    rows = dict(attrs)
    if source_value is not None:
        rows["Name"] = source_value
    state = snapshot()
    state["source"] = line("DOCHEAD", "h", {"docType": "SCH_PAGE"}) + line("COMPONENT", "c", {"x": x, "y": 20})
    for index, (key, item) in enumerate(rows.items()):
        payload = {"parentId": "c", "key": key, "value": item}
        if key == "Name" and z_index is not None:
            payload["zIndex"] = z_index
        state["source"] += line("ATTR", f"a{index}", payload)
    target = {"projectId": "p", "schematicName": "S", "pageName": "P"}
    data = {"identity": state["identity"], "window_id": "w", "source": state["source"],
            "primitive_id": "c", "target": target, "editor_version": "4.1.100", "outputs": {
                "component": {"status": "returned", "value": {"primitiveId": "c", "x": x, "y": 20, "properties": attrs}}}}
    query = {"schema_version": 1, "kind": "design-query", "query": "source-component", "captured_at": "fixture",
             "target": {"identity": state["identity"], "window_id": "w", "source_target": target},
             "provenance": {"editor_version": "4.1.100"}, "data": data}
    exported = {**attrs, "Name": value if netlist_value is None else netlist_value}
    raw = json.dumps({"version": "2.0.0", "components": {"netlist-key": {"props": exported,
                     "pinInfoMap": {"pin": {"number": "1", "net": net}}}}})
    audit = make_audit(state, {**data, "raw_netlist": raw, "drc": list(errors)}, "a" * 64)
    return query, audit


class SourceValidationTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        env = patch.dict(os.environ, {"EASYEDA_ASSIST_DATA_DIR": folder.name})
        env.start()
        self.addCleanup(env.stop)
        self.source, self.audit = evidence()
        self.source_path = write_new(self.root / "source.json", self.source)
        self.audit_path = write_new(self.root / "audit.json", self.audit)

    def prepare(self):
        return Path(prepare_validation(self.source_path, self.audit_path, "Name", "trial")['validation_path'])

    def roundtrip(self, expected=(), trial_options=None, restored_options=None, save=False):
        case = self.prepare()
        trial, audit = evidence(value="trial", **(trial_options or {}))
        restored, restored_audit = evidence(**(restored_options or {}))
        paths = [write_new(self.root / f"roundtrip-{i}.json", value) for i, value in enumerate((trial, audit, restored, restored_audit))]
        return check_roundtrip(case, *paths, expected, save)

    def test_expected_drc_stimulus_is_distinct_from_ordinary_gate_and_global_freshness(self):
        warning = {"type": "warn", "rule": "ATTRMATCHCODE", "primitives": [{"primitiveId": "c", "sheet": "d"}]}
        result = self.roundtrip([warning], {"errors": [warning]}, save=True)
        report = result["report"]
        self.assertTrue(report["experiment_expectations_met"])
        self.assertTrue(report["local_rule_response_observed"])
        self.assertFalse(report["trial_observation"]["property_observation_passed"])
        for key in ("drc_global_freshness_established", "verification_passed", "engineering_verified", "general_source_dispatch_enabled"):
            self.assertFalse(report[key])
        archive = read_json(Path(result["report_path"]))
        self.assertEqual(archive["roundtrip"], report)
        self.assertEqual(len(archive["inputs"]), 7)

    def test_unpredicted_drc_cannot_pass_even_after_exact_restoration(self):
        warning = {"type": "warn", "rule": "OTHER", "primitives": [{"primitiveId": "c", "sheet": "d"}]}
        report = self.roundtrip(trial_options={"errors": [warning]})
        self.assertTrue(report["exact_restoration_observed"])
        self.assertFalse(report["expected_drc_response_matched"])
        self.assertFalse(report["experiment_expectations_met"])

    def test_restored_value_with_layer_or_connection_residue_cannot_pass(self):
        for options in ({"z_index": 15}, {"net": "wrong"}):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as folder:
                case = self.prepare()
                trial, audit = evidence(value="trial")
                restored, restored_audit = evidence(**options)
                paths = [write_new(Path(folder) / f"{i}.json", value) for i, value in enumerate((trial, audit, restored, restored_audit))]
                report = check_roundtrip(case, *paths)
                self.assertFalse(report["exact_restoration_observed"])
                self.assertFalse(report["experiment_expectations_met"])

    def test_stale_stimulus_or_extra_geometry_does_not_pass_roundtrip(self):
        for options in ({"netlist_value": "old"}, {"x": 11}):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as folder:
                case = self.prepare()
                trial, audit = evidence(value="trial", **options)
                paths = [write_new(Path(folder) / f"{i}.json", value) for i, value in enumerate((trial, audit, self.source, self.audit))]
                report = check_roundtrip(case, *paths)
                self.assertFalse(report["stimulus_property_checks_passed"])
                self.assertFalse(report["experiment_expectations_met"])

    def test_unchanged_drc_roundtrip_never_invents_rule_response(self):
        report = self.roundtrip()
        self.assertTrue(report["experiment_expectations_met"])
        self.assertFalse(report["local_rule_response_observed"])

    def test_roundtrip_cli_is_offline_and_returns_explicit_experiment_result(self):
        case = self.prepare()
        trial, audit = evidence(value="trial")
        paths = [write_new(self.root / f"cli-roundtrip-{i}.json", value) for i, value in enumerate((trial, audit, self.source, self.audit))]
        output = io.StringIO()
        with redirect_stdout(output), patch("easyeda_assist.cli.discover", side_effect=AssertionError("offline")):
            self.assertEqual(main(["check-source-roundtrip", str(case), *map(str, paths)]), 0)
        self.assertTrue(json.loads(output.getvalue())["experiment_expectations_met"])

    def check(self, **kwargs):
        case = self.prepare()
        source, audit = evidence(**kwargs)
        left = write_new(self.root / "after-source.json", source)
        right = write_new(self.root / "after-audit.json", audit)
        return check_validation(case, left, right)

    def test_preparation_uses_unique_id_mapping_and_keeps_dispatch_closed(self):
        result = prepare_validation(self.source_path, self.audit_path, "Name", "trial")
        case, _, _ = load_case(result["validation_path"])
        self.assertEqual(case["netlist_component_id"], "netlist-key")
        self.assertEqual(case["parameters"]["primitive_id"], "c")
        self.assertFalse(case["cad_write_authorized"])
        self.assertTrue(case["requires_isolated_target_confirmation"])
        self.assertFalse(ADAPTERS[SOURCE]["dispatch_enabled"])
        self.assertFalse((self.root / "drawing-approvals").exists())
        self.assertFalse((self.root / "drawing-executions").exists())

    def test_unsafe_unchanged_missing_or_unexported_properties_rejected(self):
        for key, value in (("Device", "trial"), ("Name", "old"), ("Value", "trial"), ("Name", "")):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                make_case(self.source, self.audit, key, value)
        source, audit = evidence(netlist_value="different")
        with self.assertRaisesRegex(ValueError, "基线网表"):
            make_case(source, audit, "Name", "trial")

    def test_source_and_audit_must_bind_same_source_version_target_and_window(self):
        for mutate in (lambda x: x.update(window_id="another"),
                       lambda x: x.update(editor_version="future"),
                       lambda x: x["identity"].update(document_uuid="another")):
            audit = deepcopy(self.audit)
            mutate(audit)
            with self.subTest(audit=audit["editor_version"]), self.assertRaises(ValueError):
                make_case(self.source, audit, "Name", "trial")
        source, audit = evidence(source_value="other")
        with self.assertRaises(ValueError):
            make_case(source, audit, "Name", "trial")

    def test_mapping_never_falls_back_to_designator_and_rejects_duplicate_unique_ids(self):
        for duplicate in (False, True):
            audit = deepcopy(self.audit)
            raw = json.loads(audit["raw_netlist"])
            if duplicate:
                raw["components"]["second"] = deepcopy(raw["components"]["netlist-key"])
            else:
                raw["components"]["netlist-key"]["props"].pop("Unique ID")
            audit = make_audit({**snapshot(), "source": audit["source"]},
                               {**audit, "raw_netlist": json.dumps(raw)}, "a" * 64)
            with self.subTest(duplicate=duplicate), self.assertRaisesRegex(ValueError, "Unique ID"):
                make_case(self.source, audit, "Name", "trial")
        source, audit = evidence()
        source["data"]["source"] += line("COMPONENT", "other", {"x": 20, "y": 20})
        source["data"]["source"] += line("ATTR", "duplicate-uid", {"parentId": "other", "key": "Unique ID", "value": "uid"})
        state = {**snapshot(), "source": source["data"]["source"]}
        audit = make_audit(state, {**audit, "source": state["source"]}, "a" * 64)
        with self.assertRaisesRegex(ValueError, "Unique ID不唯一"):
            make_case(source, audit, "Name", "trial")

    def test_malformed_or_unavailable_source_query_is_rejected(self):
        for mutate in (lambda q: q.update(schema_version=True), lambda q: q.update(target=None),
                       lambda q: q.update(provenance=[]),
                       lambda q: q["data"]["outputs"]["component"].update(status="unavailable"),
                       lambda q: q["data"]["outputs"]["component"]["value"].update(primitiveId="another")):
            source = deepcopy(self.source)
            mutate(source)
            with self.subTest(source=source.get("schema_version")), self.assertRaises(ValueError):
                make_case(source, self.audit, "Name", "trial")

    def test_changed_after_target_or_page_name_is_rejected(self):
        case = self.prepare()
        for renamed in (False, True):
            source, audit = evidence(value="trial")
            if renamed:
                source["data"]["target"]["pageName"] = "renamed"
            else:
                source["data"]["identity"]["document_uuid"] = "another"
                audit["identity"]["document_uuid"] = "another"
            with tempfile.TemporaryDirectory() as directory:
                left = write_new(Path(directory) / "source.json", source)
                right = write_new(Path(directory) / "audit.json", audit)
                with self.subTest(renamed=renamed), self.assertRaisesRegex(ValueError, "目标|名称"):
                    check_validation(case, left, right)

    def test_property_observation_never_establishes_drc_freshness_or_engineering_pass(self):
        report = self.check(value="trial")
        self.assertTrue(report["property_observation_passed"])
        self.assertTrue(report["netlist_property_observed"])
        self.assertTrue(report["source_property_verified"])
        for field in ("freshness_established", "drc_freshness_established", "verification_passed", "engineering_verified", "cad_write_authorized"):
            self.assertFalse(report[field])

    def test_unchanged_old_models_and_old_source_never_pass(self):
        report = self.check()
        self.assertFalse(report["property_observation_passed"])
        self.assertFalse(report["source_property_verified"])
        self.assertFalse(report["netlist_property_observed"])

    def test_expected_source_but_stale_netlist_does_not_pass(self):
        report = self.check(value="trial", netlist_value="old")
        self.assertTrue(report["source_property_verified"])
        self.assertFalse(report["netlist_property_observed"])
        self.assertFalse(report["property_observation_passed"])

    def test_geometry_connection_or_drc_changes_do_not_pass(self):
        for options in ({"x": 11}, {"net": "unexpected"}, {"errors": [{"type": "warn", "count": 1}]}):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as directory:
                case = self.prepare()
                source, audit = evidence(value="trial", **options)
                source_path = write_new(Path(directory) / "source.json", source)
                audit_path = write_new(Path(directory) / "audit.json", audit)
                report = check_validation(case, source_path, audit_path)
                self.assertFalse(report["property_observation_passed"])

    def test_archived_baseline_bytes_and_recomputed_case_are_checked(self):
        path = self.prepare()
        self.assertEqual((path.parent / "source-before.json").read_bytes(), self.source_path.read_bytes())
        body = read_json(path)
        body["case"]["cad_write_authorized"] = True
        path.write_text(json.dumps(body), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "重算结果"):
            load_case(path)
        path = self.prepare()
        (path.parent / "audit-before.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "字节摘要"):
            load_case(path)

    def test_cli_prepares_and_checks_offline_preserving_every_input(self):
        with patch("easyeda_assist.cli.discover", side_effect=AssertionError("offline")):
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(["prepare-source-validation", str(self.source_path), str(self.audit_path),
                                       "--property", "Name", "--value", "trial"]), 0)
            case = Path(json.loads(out.getvalue())["validation_path"])
            original = [path.read_bytes() for path in (self.source_path, self.audit_path, case)]
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(["check-source-validation", str(case), str(self.source_path), str(self.audit_path), "--save"]), 0)
            result = json.loads(out.getvalue())
            self.assertFalse(result["report"]["property_observation_passed"])
            archive = Path(result["report_path"]).parent
            self.assertEqual((archive / "source-after.json").read_bytes(), self.source_path.read_bytes())
            self.assertEqual([path.read_bytes() for path in (self.source_path, self.audit_path, case)], original)


if __name__ == "__main__":
    unittest.main()
