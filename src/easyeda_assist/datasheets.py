"""Retain original-document access and support question-driven lookup."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import io
from pathlib import Path

from .contracts import nonempty, validate_task
from .guidance import guide
from .storage import digest, external, initialize, new_id, now, read_json, write_new

MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_PAGES = 500
MAX_TEXT_CHARS = 5_000_000


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
    warnings = ["文字索引仅帮助定位；型号适用性、文档修订和工程含义须按问题核对原件。",
                "PDF 表格/图形/跨页内容可能丢失；必须对照原页，不据此自动填入额定值。",
                "PDF 页码为文件物理页序；文本页码按换页符划分，行号基于提取文本。"]
    if any(not page.strip() for page in pages):
        warnings.append("存在未提取到文字的页面，可能包含扫描图或电路图。")
    source_id = new_id("datasheet")
    source = {"id": source_id, "kind": "datasheet", "reference": str(archived),
              "locator": f"全部 {len(pages)} 页",
              "description": f"目标型号 {identity['mpn']}；修订 {revision or '未确认'}；原件待人工核对",
              "sha256": sha, "review_status": "candidate"}
    task["sources"].append(source)
    pages_path = write_new(evidence / "pages.json", [{"page": i, "text": text} for i, text in enumerate(pages, 1)])
    report = {"schema_version": 2, "created_at": now(), "identity": identity,
              "revision": revision, "revision_status": "unverified", "source_id": source_id,
              "original_path": str(file_path.resolve()), "archived_path": str(archived),
              "sha256": sha, "extractor": extractor, "page_count": len(pages),
              "pages_sha256": digest(pages_path), "limitations": warnings}
    report_path = write_new(evidence / "datasheet.json", report)
    task.setdefault("datasheets", []).append({"report_path": str(report_path), "source_id": source_id,
                                              "mpn": identity["mpn"], "sha256": sha})
    task["missing_inputs"].append(f"按具体问题核对手册 {identity['mpn']} 的完整型号/修订、原页表格与适用工况（{source_id}）")
    validate_task(task)
    write_new(root / "parent-task.json", original)
    write_new(root / "initial-context.json", task.get("context"))
    task_file = write_new(root / "task.json", task)
    lines = [f"# 手册访问：{identity['mpn']}", "", f"任务目标：{task['goal']}", "",
             f"原件：{archived}", f"SHA256：{sha}", "", *[f"- {warning}" for warning in warnings], "",
             "根据绘制者的问题，用 datasheet-search 定位，用 datasheet-page 查完整页；表格/电路图用 --render 查看原页。",
             f"访问清单：{report_path}", "不预先生成关键参数摘录或工程判断。", ""]
    (root / "brief.md").write_text("\n".join(lines) + '\n' + guide(task['mode']), encoding="utf-8")
    return task_file


def access_document(manifest_path: Path) -> tuple[dict, list[dict]]:
    manifest = read_json(manifest_path)
    original = Path(manifest['archived_path'])
    if digest(original) != manifest['sha256']:
        raise ValueError('手册原件 SHA256 不一致，请重新获取可信原件')
    pages_path = manifest_path.parent / 'pages.json'
    if manifest.get('schema_version') == 2:
        if digest(pages_path) != manifest.get('pages_sha256'):
            raise ValueError('手册文字索引 SHA256 不一致')
        pages = read_json(pages_path)
    else:  # Existing v1 task archives remain usable; rebuild from the original.
        texts, _ = extract_pages(original.read_bytes(), original.suffix.lower())
        pages = [{'page': i, 'text': text} for i, text in enumerate(texts, 1)]
    return manifest, pages


def search_document(manifest_path: Path, query: str, limit: int = 20) -> dict:
    if not nonempty(query) or not 1 <= limit <= 100:
        raise ValueError('需要非空查询词，limit 范围 1–100')
    manifest, pages = access_document(manifest_path)
    hits = []
    total = 0
    for page in pages:
        lines = page['text'].splitlines()
        for index, line in enumerate(lines):
            if query.casefold() in line.casefold():
                total += 1
                if len(hits) < limit:
                    start, end = max(0, index - 2), min(len(lines), index + 4)
                    hits.append({'page': page['page'], 'line': index + 1,
                                 'line_start': start + 1, 'line_end': end,
                                 'text': '\n'.join(lines[start:end])})
    return {'source_id': manifest['source_id'], 'original': manifest['archived_path'],
            'sha256': manifest['sha256'], 'query': query, 'matches': hits,
            'total_matches': total, 'truncated': total > len(hits),
            'limitations': manifest['limitations'] + ['按行做大小写无关的字面搜索，不命中不代表原件没有相关内容；可更换关键词或查看原页。']}


def read_page(manifest_path: Path, page: int, render: bool = False) -> dict:
    manifest, pages = access_document(manifest_path)
    if not 1 <= page <= len(pages):
        raise ValueError(f'页码必须在 1–{len(pages)} 之间（物理页序）')
    result = {'source_id': manifest['source_id'], 'original': manifest['archived_path'],
              'sha256': manifest['sha256'], **pages[page - 1],
              'limitations': manifest['limitations']}
    if render:
        if Path(manifest['archived_path']).suffix.lower() != '.pdf':
            raise ValueError('--render 仅用于 PDF 原页')
        try:
            import pymupdf
        except ImportError as exc:
            raise ValueError('原页渲染需要可选依赖 PyMuPDF：python -m pip install PyMuPDF') from exc
        image_path = external(initialize() / 'datasheet-pages' / (new_id('page') + '.png'))
        image_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with pymupdf.open(manifest['archived_path']) as document:
                original_page = document[page - 1]
                # Bound unusual poster-sized pages while preserving aspect ratio.
                scale = min(1.5, 3000 / max(original_page.rect.width, original_page.rect.height))
                original_page.get_pixmap(matrix=pymupdf.Matrix(scale, scale)).save(str(image_path))
        except Exception as exc:
            raise ValueError(f'PDF 原页渲染失败：{exc}') from exc
        result['image_path'] = str(image_path)
    return result
