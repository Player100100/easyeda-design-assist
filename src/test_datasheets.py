from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from easyeda_assist.contracts import validate_task
from easyeda_assist.datasheets import component_identity, extract_pages, find_passages, import_datasheet
from easyeda_assist.storage import read_json
from easyeda_assist.tasks import create_task, handoff


TEXT = ("Fictional TEST-REG-123 Rev A\nAbsolute Maximum Ratings\nVIN -0.3 to 35 V\n"
        "Not a recommended operating voltage.\n\f"
        "Recommended Operating Conditions\nVIN 4 to 20 V\n\n"
        "Pin Functions\nIN supply input\n\nLayout Guidelines\nKeep input capacitor close.\n")


class DatasheetTests(unittest.TestCase):
    def test_passages_keep_physical_page_and_exact_text_without_inference(self):
        pages, extractor = extract_pages(TEXT.encode("utf-8"), ".txt")
        self.assertEqual(extractor, "utf8-text")
        passages, coverage = find_passages(pages)
        maximum = next(p for p in passages if p["category"] == "absolute_maximum")
        operating = next(p for p in passages if p["category"] == "operating_conditions" and p["page"] == 2)
        self.assertEqual(maximum["page"], 1)
        self.assertEqual(operating["page"], 2)
        self.assertIn("35 V", maximum["quote"])
        self.assertIn("20 V", operating["quote"])
        negation = next(p for p in passages if p["category"] == "operating_conditions" and p["page"] == 1)
        self.assertIn("Not a recommended operating voltage.", negation["quote"])
        for passage in passages:
            lines = pages[passage["page"] - 1].splitlines()
            self.assertEqual(passage["quote"], "\n".join(lines[passage["line_start"] - 1:passage["line_end"]]))
            self.assertEqual(passage["review_status"], "candidate")
        self.assertEqual(coverage["protection"]["passages_found"], 0)

    def test_import_creates_new_task_archives_original_and_does_not_verify_sources(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"EASYEDA_ASSIST_DATA_DIR": tmp}):
            task_path = create_task("datasheet", "提取虚构器件手册", [])
            original = task_path.read_bytes()
            file = Path(tmp) / "input.txt"
            file.write_text(TEXT, encoding="utf-8")
            output = import_datasheet(task_path, file, mpn="TEST-REG-123", revision="A")
            task = read_json(output)
            validate_task(task)
            self.assertNotEqual(output, task_path)
            self.assertEqual(task_path.read_bytes(), original)
            self.assertEqual(task["parent_task"]["sha256"], hashlib.sha256(original).hexdigest())
            self.assertEqual(task["recommendations"], [])
            self.assertEqual(task["status"], "analyzing")
            self.assertTrue(task["missing_inputs"])
            self.assertTrue(all(s["review_status"] == "candidate" for s in task["sources"]))
            report = read_json(Path(task["datasheets"][0]["report_path"]))
            self.assertEqual(report["identity"]["document_match"], "unverified")
            self.assertEqual(report["revision_status"], "unverified")
            self.assertEqual(Path(report["archived_path"]).read_bytes(), file.read_bytes())
            self.assertEqual(report["sha256"], hashlib.sha256(file.read_bytes()).hexdigest())
            manifest = read_json(handoff(output))
            self.assertEqual(manifest["task_id"], task["id"])

    def test_mpn_requires_evidence_not_component_name_and_multiple_objects_need_id(self):
        context = {"selected_primitives": [{"primitive_id": "one", "properties": {
            "primitive_type": "Component", "Name": "SHOULD_NOT_BECOME_MPN"}}]}
        task = {"context": context}
        with self.assertRaisesRegex(ValueError, "不从 Name"):
            component_identity(task, None, None)
        context["selected_primitives"][0]["properties"]["ManufacturerId"] = "TEST-REG-123"
        identity = component_identity(task, None, None)
        self.assertEqual(identity["mpn"], "TEST-REG-123")
        self.assertEqual(identity["origin"], "cad-manufacturer-id")
        with self.assertRaisesRegex(ValueError, "不一致"):
            component_identity(task, "other", None)
        another = deepcopy(context["selected_primitives"][0])
        another["primitive_id"] = "two"
        context["selected_primitives"].append(another)
        with self.assertRaisesRegex(ValueError, "多个元件"):
            component_identity(task, "TEST-REG-123", None)
        self.assertEqual(component_identity(task, None, None, "two")["primitive_id"], "two")

    def test_candidate_evidence_cannot_support_verified_recommendation(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"EASYEDA_ASSIST_DATA_DIR": tmp}):
            task = read_json(create_task("datasheet", "check", []))
            task["sources"] = [{"id": "source", "kind": "datasheet", "reference": "test.pdf",
                                "locator": "page 1", "description": "test", "review_status": "candidate"}]
            task["recommendations"] = [{"id": "R1", "status": "verified", "locator": "U1",
                "statement": "test", "rationale": "test", "evidence": ["source"],
                "applicability": ["test"], "assumptions": [], "risks": ["test"],
                "validation": ["test"], "confidence": "low", "decision": "pending"}]
            with self.assertRaisesRegex(ValueError, "尚未核对"):
                validate_task(task)
            task["sources"][0]["review_status"] = "reviewed"
            with self.assertRaisesRegex(ValueError, "review_record"):
                validate_task(task)
            task["sources"][0]["review_record"] = "test reviewer checked original page"
            validate_task(task)

    def test_blank_input_fails_before_creating_a_task(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"EASYEDA_ASSIST_DATA_DIR": tmp}):
            task = create_task("datasheet", "check", [])
            blank = Path(tmp) / "blank.txt"
            blank.write_text("\n\f", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "未提取到文字"):
                import_datasheet(task, blank, mpn="TEST")
            self.assertEqual(len(list((Path(tmp) / "tasks").iterdir())), 1)

    def test_limits_candidate_count_without_claiming_missing_content(self):
        pages = ["Layout Guidelines\nTest instructions"] * 25
        passages, coverage = find_passages(pages)
        self.assertEqual(len(passages), 20)
        self.assertTrue(coverage["layout"]["truncated"])
        self.assertEqual(coverage["layout"]["passages_found"], 25)

    @unittest.skipUnless(importlib.util.find_spec("pypdf"), "optional pypdf not installed")
    def test_real_pdf_extraction_preserves_pages_and_rejects_image_only_pages(self):
        import io
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

        writer = PdfWriter()
        for text in ("Absolute Maximum Ratings", "Recommended Operating Conditions"):
            page = writer.add_blank_page(width=612, height=792)
            font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                     NameObject("/Subtype"): NameObject("/Type1"),
                                     NameObject("/BaseFont"): NameObject("/Helvetica")})
            page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
            stream = DecodedStreamObject()
            stream.set_data(f"BT /F1 12 Tf 50 720 Td ({text}) Tj ET".encode("ascii"))
            page[NameObject("/Contents")] = writer._add_object(stream)
        buffer = io.BytesIO()
        writer.write(buffer)
        pages, extractor = extract_pages(buffer.getvalue(), ".pdf")
        self.assertEqual(extractor, "pypdf")
        self.assertEqual(len(pages), 2)
        self.assertIn("Absolute Maximum", pages[0])
        self.assertIn("Operating Conditions", pages[1])

        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        buffer = io.BytesIO()
        writer.write(buffer)
        self.assertFalse(any(p.strip() for p in extract_pages(buffer.getvalue(), ".pdf")[0]))
