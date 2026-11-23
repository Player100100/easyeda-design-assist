"""Screen EasyEDA native SVGs for likely wire-to-text overlaps."""
from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections import defaultdict
from xml.etree import ElementTree as ET

from .drawing_api import comparable_source, validate_snapshot
from .drawing_source import records, wire_segments
from .storage import external, initialize, new_id, now, write_new

SVG = "{http://www.w3.org/2000/svg}"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
MAX_SVG_BYTES = 10 * 1024 * 1024
# Both source and SVG are strings in a UTF-8 JSON archive, including escapes.
MAX_RECORD_BYTES = 64 * 1024 * 1024
EPSILON = 1e-6


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _number(value):
    number = float(value)
    if not math.isfinite(number) or abs(number) > 1_000_000:
        raise ValueError("SVG数值超出支持范围")
    return number


def _style(element, allowed):
    style = {}
    for declaration in element.get("style", "").split(";"):
        if declaration.strip():
            key, separator, value = declaration.partition(":")
            key = key.strip()
            if not separator or key not in allowed or key in style:
                raise ValueError("存在未支持的SVG样式")
            style[key] = value.strip()
    return style


def _text_geometry(element):
    if (set(element.attrib) - {"id", "style", "x", "y", "text-anchor", "transform",
                               "dominant-baseline", "c_partid", XML_SPACE} or
            element.get("dominant-baseline") != "ideographic"):
        raise ValueError("未支持的SVG文字定位")
    x, y = (_number(element.get(axis, "")) for axis in ("x", "y"))
    style = _style(element, {"font-family", "font-size", "font-style", "font-weight", "fill", "stroke"})
    size = style.get("font-size", "")
    if not size.endswith("px") or not 0 < (em := _number(size[:-2])) <= 1000:
        raise ValueError("未支持的SVG字号")
    content = element.text or ""
    baseline_x, baseline_y = x, y
    if len(element):
        span = element[0]
        if (len(element) != 1 or content.strip() or span.tag != SVG + "tspan" or len(span) or
                (span.tail or "").strip() or set(span.attrib) - {"x", "dx", "dy", XML_SPACE}):
            raise ValueError("未支持的SVG文字分行或偏移")
        baseline_x = _number(span.get("x", str(x))) + _number(span.get("dx", "0"))
        baseline_y += _number(span.get("dy", "0"))
        content = span.text or ""
    if len(content) > 500 or "\n" in content or "\r" in content:
        raise ValueError("未支持的SVG多行或长文字")
    if not content.strip():
        return None

    # Estimate in SVG user units. Local baseline runs horizontally from (x,y).
    def char_width(char):
        if unicodedata.combining(char):
            return 0
        if unicodedata.east_asian_width(char) in {"F", "W"}:
            return 1
        return 0.36 if char.isspace() else 0.62

    width = sum(map(char_width, content)) * em
    anchor = {"start": 0, "middle": 0.5, "end": 1}.get(element.get("text-anchor", "start"))
    if anchor is None:
        raise ValueError("未支持的SVG文字对齐")
    angle = 0
    cx, cy = x, y
    transform = element.get("transform", "")
    if transform:
        match = re.fullmatch(r"rotate\(\s*([+-]?\d+)\s*[, ]\s*([^,\s]+)\s*[, ]\s*([^,\s]+)\s*\)", transform)
        if not match or len(match[1]) > 4 or int(match[1]) % 90 != 0:
            raise ValueError("未支持的SVG文字旋转")
        angle = int(match[1]) % 360
        cx, cy = (_number(match[index]) for index in (2, 3))
    cos, sin = {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}[angle]

    def rotate(px, py):
        dx, dy = px - cx, py - cy
        return cx + dx * cos - dy * sin, cy + dx * sin + dy * cos

    left, right = baseline_x - anchor * width, baseline_x + (1 - anchor) * width
    top, bottom = baseline_y - 0.86 * em, baseline_y + 0.10 * em
    corners = [rotate(px, py) for px in (left, right) for py in (top, bottom)]
    xs, ys = zip(*corners)
    baseline_axis = "V" if sin else "H"
    baseline_fixed = rotate(baseline_x, baseline_y)[0 if sin else 1]
    return content, tuple(round(v, 6) for v in (min(xs), min(ys), max(xs), max(ys))), baseline_axis, baseline_fixed


def _wire_geometry(element):
    group = element.get("data-group-id")
    if (element.tag != SVG + "polyline" or not group or
            set(element.attrib) - {"points", "id", "data-group-id", "c_partid", "style"}):
        raise ValueError("未支持的SVG导线格式")
    style = _style(element, {"stroke-width", "stroke", "vector-effect", "fill"})
    width = _number(style.get("stroke-width", "1"))
    if not 0 < width <= 100 or style.get("stroke") == "none":
        raise ValueError("未支持的SVG导线样式")
    points = [_number(token) for token in re.split(r"[\s,]+", element.get("points", "").strip())]
    return group, width, wire_segments(points)


def _shapes(svg):
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as exc:
        raise ValueError("原生SVG格式无效") from exc
    if (root.tag != SVG + "svg" or
            set(root.attrib) - {"width", "height", "viewBox", "version", "style", "id"}):
        raise ValueError("未支持的原生SVG根元素")
    _style(root, {"fill"})
    for css in root.iter(SVG + "style"):
        if re.sub(r'\s+', '', css.text or '').rstrip(";") not in {
                "", "*{stroke-linejoin:round;stroke-linecap:round}"}:
            raise ValueError("SVG含未支持的全局样式，请查看原图")
    shapes = [e for e in root if e.tag == SVG + "g" and e.get("class") == "shapeBox"]
    if len(shapes) != 1:
        raise ValueError("SVG中找不到唯一的原理图绘制区域")
    stack = [(shapes[0], True)]
    while stack:
        element, supported = stack.pop()
        if element.get("c_partid") == "sheet":
            continue
        yield element, supported
        supported = (supported and element.tag == SVG + "g" and
                     not (set(element.attrib) - {"class", "c_partid", "id", "noconnect"}))
        stack.extend((child, supported) for child in reversed(element))


def _candidates(texts, segments):
    for text_id, content, bounds, owner, baseline_axis, baseline_fixed in texts:
        left, top, right, bottom = bounds
        for group, half_width, (axis, fixed, low, high) in segments:
            # An EasyEDA NET attribute may sit directly on its own baseline.
            if group == owner and axis == baseline_axis and abs(fixed - baseline_fixed) <= EPSILON:
                continue
            across = (top, bottom) if axis == "H" else (left, right)
            if not across[0] - half_width < fixed < across[1] + half_width:
                continue
            along = (left, right) if axis == "H" else (top, bottom)
            start = round(max(low, along[0] - half_width), 6)
            end = round(min(high, along[1] + half_width), 6)
            if end - start > EPSILON:
                yield {"text_id": text_id, "text": content, "text_box": bounds, "wire_group": group,
                       "axis": axis, "fixed": round(fixed, 6), "overlap_interval": [start, end]}


def _review(record):
    if not isinstance(record, dict) or not isinstance(record.get("captured_at"), str):
        raise ValueError("需要 drawing-svg 生成的证据 JSON")
    validate_snapshot({**record, "schema_version": 1, "kind": "drawing-source", "domain": "schematic"})
    svg = record.get("svg")
    if not isinstance(svg, str) or not svg:
        raise ValueError("证据缺少原生SVG")
    raw_svg = svg.encode("utf-8")
    if len(raw_svg) > MAX_SVG_BYTES or re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", svg, re.IGNORECASE):
        raise ValueError("SVG过大或包含未支持的声明")
    source = records(record["source"])
    if source["DOCHEAD"][1].get("uuid") != record["identity"]["document_uuid"]:
        raise ValueError("SVG归档中的源文档与目标身份不一致")
    net_attributes = {rid: (value.get("value"), value.get("parentId")) for rid, (env, value) in source.items()
                      if env["type"] == "ATTR" and value.get("key") == "NET"}
    texts, wire_paths = [], defaultdict(list)
    scanned = {"texts": 0, "wire_elements": 0, "unsupported_texts": 0, "unsupported_wire_elements": 0}
    for element, supported in _shapes(svg):
        is_wire = element.get("c_partid") == "wire"
        if is_wire:
            kind = "wire_elements"
        elif element.tag == SVG + "text":
            kind = "texts"
        else:
            continue
        try:
            if not supported:
                raise ValueError("未支持的SVG父元素变换或样式")
            if is_wire:
                group, width, segments = _wire_geometry(element)
                for axis, fixed, low, high in segments:
                    wire_paths[group, width, axis, fixed].append((low, high))
            else:
                geometry = _text_geometry(element)
                if geometry is None:
                    continue
                content, bounds, axis, fixed = geometry
                text_id = element.get("id")
                net, owner = net_attributes.get(text_id, (None, None))
                texts.append((text_id, content, bounds, owner if net == content else None, axis, fixed))
        except ValueError:
            scanned["unsupported_" + kind] += 1
        else:
            scanned[kind] += 1
    # Merge collinear pieces before comparing. LINE segment IDs are volatile.
    wires = [(group, width / 2, (axis, fixed, low, high))
             for (group, width, axis, fixed), intervals in wire_paths.items()
             for low, high in _merge_intervals(intervals)]
    if len(texts) * len(wires) > 2_000_000:
        raise ValueError("SVG文字和导线数量超出离线筛查范围，请查看原图")
    overlaps = []
    for overlap in _candidates(texts, wires):
        if len(overlaps) >= 5000:
            raise ValueError("SVG疑似遮挡数量超出报告范围，请查看原图")
        overlaps.append(overlap)
    return {"identity": record["identity"], "window_id": record["window_id"],
            "captured_at": record["captured_at"], "svg_sha256": _sha(raw_svg),
            "source_sha256": _sha(comparable_source(record["source"]).encode("utf-8")),
            "scanned": scanned, "candidate_count": len(overlaps), "candidates": overlaps}


def _merge_intervals(intervals):
    merged = []
    for low, high in sorted(intervals):
        if merged and low <= merged[-1][1] + EPSILON:
            merged[-1] = (merged[-1][0], max(high, merged[-1][1]))
        else:
            merged.append((low, high))
    return merged


def _new_candidates(before, after):
    def key(candidate):
        return (candidate["text"], candidate["text_box"], candidate["axis"], candidate["fixed"])

    old_intervals = defaultdict(list)
    for candidate in before["candidates"]:
        old_intervals[key(candidate)].append(candidate["overlap_interval"])
    coverage = {loc: _merge_intervals(intervals) for loc, intervals in old_intervals.items()}

    added = []
    for candidate in after["candidates"]:
        low, high = candidate["overlap_interval"]
        uncovered = []
        for start, end in coverage.get(key(candidate), ()):
            if end <= low:
                continue
            if start >= high:
                break
            if start - low > EPSILON:
                uncovered.append([low, start])
            low = max(low, end)
            if low >= high:
                break
        if high - low > EPSILON:
            uncovered.append([low, high])
        if uncovered:
            added.append({**candidate, "new_overlap_intervals": uncovered})
    return added


def review_files(after_path, before_path=None, save=False):
    def load(path):
        path = external(path)
        with path.open("rb") as stream:
            raw = stream.read(MAX_RECORD_BYTES + 1)
        if len(raw) > MAX_RECORD_BYTES:
            raise ValueError("SVG证据文件过大")
        return _review(json.loads(raw.decode("utf-8-sig"))) | {"path": str(path), "archive_sha256": _sha(raw)}

    after = load(after_path)
    result = {"schema_version": 1, "kind": "drawing-visual-review", "reviewed_at": now(), "after": after,
              "coordinate_system": "svg_viewbox",
              "scope": "估算原生SVG中正交导线与单行文字的遮挡，需人工查看整页和变更区域。网表与DRC独立核对。"}
    if before_path is not None:
        before = load(before_path)
        if any(before["identity"][k] != after["identity"][k] for k in ("project_uuid", "document_uuid")):
            raise ValueError("前后SVG必须来自同一工程的同一原理图页")
        added = _new_candidates(before, after)
        result.update(before=before, new_candidate_count=len(added), new_candidates=added)
    if save:
        path = write_new(initialize() / "drawing-visual-reviews" / (new_id("visual") + ".json"), result)
        return {"review_path": str(path), **result}
    return result
