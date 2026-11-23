"""Readability regressions with native wire, label and source shapes."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from easyeda_assist.cli import main
from easyeda_assist.drawing_readability import route_metrics
from easyeda_assist.drawing_visual import _review


def wire(group, points, line_id="line"):
    return (f'<polyline c_partid="wire" id="{line_id}" data-group-id="{group}" points="{points}" '
            'style="stroke:#008800;stroke-width:1;vector-effect:none;"/>')


def label(text, x=0, y=0, rotation=0, text_id="text", shift=0, anchor="start"):
    return (f'<text id="{text_id}" x="{x}" y="{y}" style="font-size:10px;" text-anchor="{anchor}" '
            f'dominant-baseline="ideographic" transform="rotate({rotation}, {x}, {y})">'
            f'<tspan x="{x}" dy="{shift}">{text}</tspan></text>')


def record(*elements, doc="doc"):
    return {
        "captured_at": "2026-11-20T00:00:00+00:00", "window_id": "w",
        "identity": {"project_uuid": "p", "document_uuid": doc, "tab_id": doc + "@p", "document_type": 1},
        "source": ('{"type":"DOCHEAD"}||' + json.dumps({"docType": "SCH_PAGE", "uuid": doc}) +
                   '|\n{"type":"ATTR","id":"net"}||{"key":"NET","value":"SIGNAL","parentId":"owner"}|\n'),
        "svg": ('<svg xmlns="http://www.w3.org/2000/svg"><g class="shapeBox">'
                + "".join(elements) + "</g></svg>"),
    }


class VisualReviewTests(unittest.TestCase):
    def test_only_net_labels_own_baseline_is_ignored(self):
        result = _review(record(label("SIGNAL", text_id="net"), wire("owner", "-10 0 50 0"),
                                wire("owner", "10 -20 10 0"), wire("other", "-10 0 50 0"),
                                wire("distant", "100 -30 100 30")))
        self.assertCountEqual([(row["wire_group"], row["axis"]) for row in result["candidates"]],
                              [("owner", "V"), ("other", "H")])

    def test_shift_and_rotation_are_applied_before_screening(self):
        rotated = label("GND", x=40, rotation=-90, shift=12, anchor="middle")
        result = _review(record(rotated, wire("hit", "48 -20 48 20"), wire("miss", "37 -20 37 20"),
                                label("Complex").replace('dy="0"', 'dy="0 4"')))
        self.assertEqual([row["wire_group"] for row in result["candidates"]], ["hit"])
        self.assertEqual(result["scanned"]["unsupported_texts"], 1)

    def test_cli_saves_new_coverage_and_ignores_line_splitting(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"EASYEDA_ASSIST_DATA_DIR": temporary}):
            root = Path(temporary)
            before, after = root / "before.json", root / "after.json"
            text = label("SIGNAL")
            before.write_text(json.dumps(record(text, wire("old", "-20 -4 60 -4"))), encoding="utf-8")
            after.write_text(json.dumps(record(text, wire("old", "-20 -4 20 -4", "new-1"),
                                               wire("old", "20 -4 60 -4", "new-2"),
                                               wire("added", "25 -15 25 10"))), encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["review-drawing-visual", str(after), "--before", str(before), "--save"]), 0)
            report = json.loads(output.getvalue())
            self.assertEqual(report["before"]["candidate_count"], 1)
            self.assertEqual(report["after"]["candidate_count"], 2)
            self.assertEqual([row["wire_group"] for row in report["new_candidates"]], ["added"])
            with Path(report["review_path"]).open(encoding="utf-8") as stream:
                self.assertEqual(json.load(stream)["new_candidate_count"], 1)

    def test_svg_with_entity_declaration_is_rejected(self):
        invalid = record()
        invalid["svg"] = '<!DOCTYPE svg [<!ENTITY x "value">]>' + invalid["svg"]
        with self.assertRaisesRegex(ValueError, "声明"):
            _review(invalid)

    def test_route_metrics_measure_bends_and_exclude_endpoint_contacts(self):
        def operation(name, net, line):
            return {"id": name, "operation": "add_wire", "parameters": {"net": net, "line": line}}

        metrics = route_metrics([
            operation("a", "A", [0, 0, 10, 0, 20, 0, 20, 10]),
            operation("b", "B", [10, -5, 10, 5]),
            operation("c", "C", [5, -5, 5, 0]),
        ])
        self.assertEqual(metrics["total_length"], 45)
        self.assertEqual(metrics["total_right_angle_bends"], 1)
        self.assertEqual(metrics["different_net_crossings"], 1)
