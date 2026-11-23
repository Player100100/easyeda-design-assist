"""Schematic layout guidance and geometry for comparing planned routes."""
from bisect import bisect_left, bisect_right

from .drawing_source import wire_segments


def layout_review():
    return {
        "guide": "docs/playbooks/schematic.md",
        "scope": "供人工复核，不判定版式合格或授予写入权限。",
        "before": [
            "多模块用统一的无填充细框分区，功能标题置于框内左上角。",
            "先留出图框、标题栏和旧电路保留区，再对齐模块边线；同列尽量等宽，重复支路等距。",
            "按信号流向摆放器件，缩短正交走线并给标注留白；记录对齐基准、接口和布局例外。",
        ],
        "after": [
            "对照同页原生 SVG，看整页框、标题、对齐与净距，再放大检查走线、节点和标注。",
            "独立核对引脚网络与详细 DRC；仅整理版式时，完整网表和详细 DRC 须保持一致。",
        ],
    }


def route_metrics(operations):
    routes, horizontal, vertical = [], [], []
    for operation in operations:
        if operation["operation"] != "add_wire":
            continue
        parameters = operation["parameters"]
        line, net = parameters["line"], parameters["net"]
        segments = wire_segments(line)
        bends = 0
        for path in line if isinstance(line[0], list) else [line]:
            xs = path[::2]
            axes = [a == b for a, b in zip(xs, xs[1:])]
            bends += sum(a != b for a, b in zip(axes, axes[1:]))
        for axis, fixed, low, high in segments:
            (horizontal if axis == "H" else vertical).append((fixed, low, high, net))
        routes.append({"operation": operation["id"], "net": net,
                       "length": sum(high - low for _, _, low, high in segments),
                       "right_angle_bends": bends})
    if not routes:
        return None

    # Contacts at endpoints and parallel overlaps are handled by electrical
    # preflight. Here count proper crossings once for each point and net pair.
    vertical.sort()
    x_positions = [segment[0] for segment in vertical]
    crossings = set()
    for y, left, right, h_net in horizontal:
        for index in range(bisect_right(x_positions, left), bisect_left(x_positions, right)):
            x, bottom, top, v_net = vertical[index]
            if h_net != v_net and bottom < y < top:
                crossings.add((x, y, *sorted((h_net, v_net))))
    return {"unit": "10mil", "scope": "planned_wires", "routes": routes,
            "total_length": sum(route["length"] for route in routes),
            "total_right_angle_bends": sum(route["right_angle_bends"] for route in routes),
            "different_net_crossings": len(crossings)}
