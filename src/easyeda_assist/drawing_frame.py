"""Read reserved frame geometry from the editor's bound native SVG export."""
from __future__ import annotations

import math
import re
from xml.etree import ElementTree as ET

from .drawing_circuit import _number


def _rectangle(element):
    if element.tag != "{http://www.w3.org/2000/svg}rect":
        raise ValueError("图框必须使用已核验的矩形格式")
    try:
        x, y, width, height = [float(element.get(k, "nan")) for k in ("x", "y", "width", "height")]
    except (TypeError, ValueError) as exc:
        raise ValueError("图框SVG矩形数据无效") from exc
    if not all(math.isfinite(n) for n in (x, y, width, height)) or width <= 0 or height <= 0:
        raise ValueError("图框SVG矩形边界无效")
    return [x, -y-height, x+width, -y]


def frame_geometry(rows, inventory, svg_record):
    """Fail closed for unrecognized frames; never use text/name as geometry."""
    sheets = [item for item in inventory if item["component_type"] == "sheet"]
    attributes = [value for env, value in rows.values() if env["type"] == "ATTR"]
    markers = {v.get("parentId") for v in attributes if v.get("key") in {"Title Block Position", "Title Block"}}
    if not sheets and not markers:
        if svg_record is not None:
            raise ValueError("无图框基线不应携带图框SVG证据")
        return None
    if len(sheets) != 1 or markers != {sheets[0]["primitive_id"]}:
        raise ValueError("无法唯一绑定图框及标题栏属性")
    sheet = sheets[0]
    env, source = rows[sheet["primitive_id"]]
    layout = {}
    expected = {"Size": "A4", "Width": "1170", "Height": "825", "Blade Width": "10",
                "Border": "1", "Title Block": "1", "Title Block Position": "3"}
    for v in attributes:
        key = v.get("key")
        if v.get("parentId") == sheet["primitive_id"] and key in expected:
            if key in layout:
                raise ValueError("图框布局属性重复")
            layout[key] = v.get("value")
    if (layout != expected or env["type"] != "COMPONENT" or
            any(not _number(item.get(k)) or item[k] != 0
                for item in (source, sheet) for k in ("x", "y", "rotation")) or
            source.get("isMirror") is not False or sheet.get("mirror") is not False):
        raise ValueError("当前图框布局尚未核验，无法确认标题栏避让")
    if not isinstance(svg_record, dict) or not isinstance(svg_record.get("svg"), str):
        raise ValueError("增量基线缺少同源图框SVG证据，请重新采集")
    svg = svg_record["svg"]
    if len(svg.encode("utf-8")) > 10 * 1024 * 1024 or "<!DOCTYPE" in svg.upper() or "<!ENTITY" in svg.upper():
        raise ValueError("图框SVG过大或包含不支持的声明")
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as exc:
        raise ValueError("图框SVG格式无效") from exc
    tag = lambda name: "{http://www.w3.org/2000/svg}" + name
    if root.tag != tag("svg"):
        raise ValueError("图框证据必须为原生SVG")
    frames = [e for e in root.iter() if e.get("c_partid") == "sheet"]
    parents = {child: parent for parent in root.iter() for child in parent}
    if len(frames) != 1:
        raise ValueError("SVG中图框数量不唯一")
    frame = frames[0]
    ancestor = frame
    while ancestor is not None:
        if ancestor.get("transform") or ancestor.get("style") or (
                ancestor is not root and ancestor.tag != tag("g")):
            # The export root only has a background fill; it cannot alter geometry.
            if ancestor is not root or ancestor.get("transform") or ancestor.get("style", "") != "fill:#FFFCF8;":
                raise ValueError("SVG图框包含未支持的坐标变换或样式")
        ancestor = parents.get(ancestor)
    for e in frame.iter():
        transform = e.get("transform")
        if e.tag == tag("svg") or (transform and not (
                e.tag == tag("text") and re.fullmatch(r"rotate\(0[ ,]+[-+0-9.eE]+[ ,]+[-+0-9.eE]+\)", transform))):
            raise ValueError("SVG图框包含未支持的坐标变换")
    tables = [e for e in frame.iter() if e.get("c_partid") == "table"]
    if len(tables) != 1:
        raise ValueError("SVG中标题栏数量不唯一")
    table = tables[0]
    table_elements = set(table.iter())
    allowed = {tag(n) for n in ("g", "rect", "line", "text", "tspan")}
    if any(e.tag not in allowed for e in table_elements):
        raise ValueError("标题栏包含未支持的几何")
    cells = [_rectangle(e) for e in table.iter() if e.tag == tag("rect")]
    if not cells:
        raise ValueError("标题栏缺少完整单元格边界")
    borders = [_rectangle(e) for e in frame.iter() if e.tag == tag("rect") and e not in table_elements]
    if len(borders) != 2 or sorted(borders) != [[0, 0, 1170, 825], [10, 10, 1160, 815]]:
        raise ValueError("SVG图框边界与A4布局不一致")
    # Include the editor's company logo. The table union conservatively reserves
    # all cell interiors, even cells with hidden/empty text.
    for image in (e for e in frame.iter() if e.tag == tag("image")):
        rectangle = ET.Element(tag("rect"), image.attrib)
        cells.append(_rectangle(rectangle))
    bounds = [min(b[0] for b in cells), min(b[1] for b in cells),
              max(b[2] for b in cells), max(b[3] for b in cells)]
    if not (10-1e-6 <= bounds[0] < bounds[2] <= 1160+1e-6 and 0 <= bounds[1] < bounds[3] <= 815):
        raise ValueError("标题栏边界超出图框")
    # One unit outward covers stroke and float rounding. Text glyph bounds
    # remain outside this geometric contract.
    bounds = [math.floor(round(bounds[0], 6))-1, math.floor(round(bounds[1], 6))-1,
              math.ceil(round(bounds[2], 6))+1, math.ceil(round(bounds[3], 6))+1]
    return {"primitive_id": sheet["primitive_id"], "profile": "a4-native-svg-position-3",
            "inner_border": [10, 10, 1160, 815], "title_block": bounds, "clearance": 5}
