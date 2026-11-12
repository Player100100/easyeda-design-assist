"""Archive a local datasheet and propose source-located passages for review.

Keyword retrieval is intentionally separate from parameter interpretation. No
numeric rating is inferred, and imported claims never become verified facts.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import io
from pathlib import Path
import re

from .contracts import nonempty, validate_task
from .storage import digest, external, initialize, new_id, now, read_json, write_new

MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_PAGES = 500
MAX_TEXT_CHARS = 5_000_000
MAX_PASSAGES_PER_CATEGORY = 20
CATEGORIES = {
    "absolute_maximum": ("绝对最大额定值", r"absolute\s+maximum|maximum\s+ratings|绝对最大|极限参数"),
    "operating_conditions": ("推荐工作条件与电气特性", r"recommended\s+operating|operating\s+conditions|electrical\s+characteristics|推荐工作|工作条件|电气特性"),
    "pinout": ("引脚功能", r"pin\s+(?:functions?|configuration|description|assignment)|引脚(?:功能|定义|说明|配置)"),
    "application": ("应用拓扑与外围", r"typical\s+application|application\s+(?:information|circuit)|external\s+components?|典型应用|应用电路|外围电路"),
    "protection": ("保护相关", r"over[\s-]?(?:voltage|current|temperature)|short[\s-]?circuit|thermal\s+shutdown|ESD\s+(?:rating|protection)|过压|过流|过温|短路保护|静电保护"),
    "layout": ("布局与散热", r"layout\s+(?:guidelines?|considerations?|recommendations?|example)|thermal\s+(?:information|resistance|considerations?)|PCB\s+layout|布局(?:指南|要求|建议)|热阻|散热"),
}


def component_identity(task: dict, mpn: str | None, manufacturer: str | None,
                       primitive_id: str | None = None) -> dict:
    context = task.get("context") or {}
    components = [p for p in context.get("selected_primitives", [])
                  if p["properties"].get("primitive_type") == "Component"]
    if primitive_id:
        components = [p for p in components if p["primitive_id"] == primitive_id]
        if len(components) != 1:
            raise ValueError("指定 primitive-id 不对应上下文中已采集的元件")
    elif len(components) > 1:
        raise ValueError("上下文中有多个元件；请用 --primitive-id 指定手册对象")
    component = components[0] if len(components) == 1 else None
    properties = component["properties"] if component else {}
    captured_mpn = properties.get("ManufacturerId")
    identity_origin = "provided"
    if not nonempty(mpn):
        if not nonempty(captured_mpn):
            raise ValueError("需要 --mpn 指定完整型号，或上下文中唯一目标元件的 ManufacturerId；不从 Name 猜测型号")
        mpn = captured_mpn
        identity_origin = "cad-manufacturer-id"
    if component and nonempty(captured_mpn) and mpn.strip() != captured_mpn.strip():
        raise ValueError("--mpn 与目标元件 ManufacturerId 不一致；请核对目标或使用独立离线任务")
    return {"mpn": mpn.strip(), "manufacturer": manufacturer or properties.get("Manufacturer"),
            "primitive_id": component["primitive_id"] if component else None,
            "designator": properties.get("Designator"),
            "origin": identity_origin,
            "document_match": "unverified"}


def extract_pages(content: bytes, suffix: str) -> tuple[list[str], str]:
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise ValueError("PDF 导入需要可选依赖 pypdf：python -m pip install pypdf") from exc
        try:
            reader = PdfReader(io.BytesIO(content))
            if reader.is_encrypted:
                raise ValueError("不支持加密 PDF，请提供未加密原件")
            if len(reader.pages) > MAX_PAGES:
                raise ValueError(f"手册超过 {MAX_PAGES} 页限制")
            pages = []
            length = 0
            for page in reader.pages:
                text = page.extract_text() or ""
                length += len(text)
                if length > MAX_TEXT_CHARS:
                    raise ValueError("提取文字量超过限制")
                pages.append(text)
        except Exception as exc:
            raise ValueError(f"PDF 提取失败：{exc}") from exc
        return pages, "pypdf"
    if suffix not in {".txt", ".md"}:
        raise ValueError("手册仅支持 .pdf、UTF-8 .txt 或 .md")
    text = content.decode("utf-8-sig")
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError("手册文字量超过限制")
    pages = text.replace("\r\n", "\n").replace("\r", "\n").split("\f")
    # A trailing form feed terminates the last page; it is not another page.
    if len(pages) > 1 and not pages[-1].strip():
        pages.pop()
    if len(pages) > MAX_PAGES:
        raise ValueError(f"手册超过 {MAX_PAGES} 页限制")
    return pages, "utf8-text"


def find_passages(pages: list[str]) -> tuple[list[dict], dict]:
    passages = []
    coverage = {}
    for category, (label, pattern) in CATEGORIES.items():
        matches = []
        for page_number, text in enumerate(pages, 1):
            lines = text.splitlines()
            spans = []
            for index, line in enumerate(lines):
                if re.search(pattern, line, flags=re.IGNORECASE):
                    start, end = max(0, index - 1), min(len(lines), index + 4)
                    if spans and start <= spans[-1][1]:
                        spans[-1][1] = max(end, spans[-1][1])
                    else:
                        spans.append([start, end])
            for start, end in spans:
                matches.append({"category": category, "page": page_number,
                                "line_start": start + 1, "line_end": end,
                                "quote": "\n".join(lines[start:end]), "review_status": "candidate"})
        coverage[category] = {"label": label, "passages_found": len(matches),
                              "passages_retained": min(len(matches), MAX_PASSAGES_PER_CATEGORY),
                              "truncated": len(matches) > MAX_PASSAGES_PER_CATEGORY}
        passages.extend(matches[:MAX_PASSAGES_PER_CATEGORY])
    return passages, coverage


def import_datasheet(task_path: Path, file_path: Path, *, mpn: str | None = None,
                     manufacturer: str | None = None, primitive_id: str | None = None,
                     revision: str | None = None) -> Path:
    original = read_json(task_path)
    validate_task(original)
    identity = component_identity(original, mpn, manufacturer, primitive_id)
    with file_path.open("rb") as stream:
        content = stream.read(MAX_FILE_BYTES + 1)
    if len(content) > MAX_FILE_BYTES:
        raise ValueError("手册文件超过 50 MiB 限制")
    pages, extractor = extract_pages(content, file_path.suffix.lower())
    if not any(page.strip() for page in pages):
        raise ValueError("未提取到文字，扫描版手册需先 OCR 并人工核对原图")
    passages, coverage = find_passages(pages)
    task = deepcopy(original)
    task.update(id=new_id("task"), created_at=now(), status="analyzing",
                parent_task={"id": original["id"], "path": str(task_path.resolve()),
                             "sha256": digest(task_path)})
    root = external(initialize() / "tasks" / task["id"])
    root.mkdir(exist_ok=False)
    evidence = external(root / "evidence")
    evidence.mkdir()
    archived = external(evidence / ("datasheet" + file_path.suffix.lower()))
    with archived.open("xb") as stream:
        stream.write(content)
    sha = hashlib.sha256(content).hexdigest()
    warnings = ["摘录由关键词定位，尚未校验型号适用性、文档修订或工程含义。",
                "PDF 表格/图形/跨页内容可能丢失；必须对照原页，不据此自动填入额定值。",
                "PDF 页码为文件物理页序；文本页码按换页符划分，行号基于提取文本。"]
    if any(not page.strip() for page in pages):
        warnings.append("存在未提取到文字的页面，可能包含扫描图或电路图。")
    if any(item["truncated"] for item in coverage.values()):
        warnings.append("部分分类的候选摘录已截断；完整提取文字仍保留。")
    source_id = new_id("datasheet")
    source = {"id": source_id, "kind": "datasheet", "reference": str(archived),
              "locator": f"全部 {len(pages)} 页",
              "description": f"目标型号 {identity['mpn']}；修订 {revision or '未确认'}；原件待人工核对",
              "sha256": sha, "review_status": "candidate"}
    task["sources"].append(source)
    for index, passage in enumerate(passages, 1):
        passage["source_id"] = f"{source_id}-excerpt-{index}"
        task["sources"].append({
            "id": passage["source_id"], "kind": "datasheet-excerpt", "reference": str(archived),
            "locator": f"page {passage['page']}; extracted lines {passage['line_start']}-{passage['line_end']}",
            "description": f"{identity['mpn']}；{CATEGORIES[passage['category']][0]}；修订 {revision or '未确认'}；未核对候选",
            "quote": passage["quote"], "sha256": sha, "review_status": "candidate",
        })
    report = {"schema_version": 1, "created_at": now(), "identity": identity,
              "revision": revision, "revision_status": "unverified", "source_id": source_id,
              "original_path": str(file_path.resolve()), "archived_path": str(archived),
              "sha256": sha, "extractor": extractor, "page_count": len(pages),
              "coverage": coverage, "passages": passages, "limitations": warnings}
    report_path = write_new(evidence / "datasheet.json", report)
    write_new(evidence / "pages.json", [{"page": i, "text": text} for i, text in enumerate(pages, 1)])
    task.setdefault("datasheets", []).append({"report_path": str(report_path), "source_id": source_id,
                                              "mpn": identity["mpn"], "sha256": sha})
    task["missing_inputs"].append(f"人工核对手册 {identity['mpn']} 的完整型号/修订、原页表格与候选摘录（{source_id}）")
    validate_task(task)
    write_new(root / "parent-task.json", original)
    write_new(root / "initial-context.json", task.get("context"))
    task_file = write_new(root / "task.json", task)
    lines = [f"# 手册要点候选：{identity['mpn']}", "", f"任务目标：{task['goal']}", "",
             f"原件：{archived}", f"SHA256：{sha}", "", *[f"- {warning}" for warning in warnings], "",
             "尚未找到关键词的分类不代表手册没有相应内容。请逐项对照原页，补充工况后再形成工程建议。", ""]
    for passage in passages:
        lines.extend([f"## {CATEGORIES[passage['category']][0]} · p.{passage['page']} · {passage['source_id']}",
                      "", *["> " + line for line in passage["quote"].splitlines()], ""])
    (root / "brief.md").write_text("\n".join(lines), encoding="utf-8")
    return task_file
