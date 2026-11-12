"""One fixed CAD read and local, bounded connectivity queries for an agent.

The exporter owns electrical net resolution. Never join separate exports by
labels, infer connections from coordinates, or interpret labels as functions.
"""
from __future__ import annotations

import hashlib
import json

from .contracts import nonempty
from .storage import now

# Verified against easyeda-api-skill 1.1.29 SCH_ManufactureData.getNetlistFile
# and ESYS_NetlistType.JLCEDA_PRO. File.text() is a standard Web File API.
# No saveFile, document activation, or user-supplied JavaScript.
SCHEMATIC_QUERY = """
const project=await eda.dmt_Project.getCurrentProjectInfo();
const document=await eda.dmt_SelectControl.getCurrentDocumentInfo();
if(!project||!document||document.documentType!==1)throw new Error('请聚焦目标原理图页');
if(document.parentProjectUuid&&document.parentProjectUuid!==project.uuid)throw new Error('文档归属与工程不一致');
const file=await eda.sch_ManufactureData.getNetlistFile('design-assist','JLCEDA');
if(!file)throw new Error('API 未返回网表文件');
if(file.size>20*1024*1024)throw new Error('网表超过 20 MiB 限制');
const raw=await file.text();
if(raw.length>20*1024*1024)throw new Error('网表文字超过限制');
const after=await eda.dmt_SelectControl.getCurrentDocumentInfo();
const projectAfter=await eda.dmt_Project.getCurrentProjectInfo();
if(!projectAfter||project.uuid!==projectAfter.uuid||!after||after.uuid!==document.uuid||after.tabId!==document.tabId||after.documentType!==document.documentType||after.parentProjectUuid!==document.parentProjectUuid)throw new Error('读取期间工程或焦点文档变化，请重新采集');
return {project,document,raw_netlist:raw};
"""


def components_from_raw(raw: str) -> dict:
    if not isinstance(raw, str) or len(raw.encode('utf-8')) > 20 * 1024 * 1024:
        raise ValueError('网表必须是 20 MiB 以内的 JSON 文本')
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('网表存在重复 JSON 键，不能可靠解析连接')
            result[key] = value
        return result
    data = json.loads(raw, object_pairs_hook=unique_object)
    if not isinstance(data, dict) or data.get('version') != '2.0.0' or not isinstance(data.get('components'), dict):
        raise ValueError('仅支持已验证结构的 JLCEDA 2.0.0 components 网表；保留原数据后检查 API 格式')
    components = data['components']
    for cid, component in components.items():
        if (not nonempty(cid) or not isinstance(component, dict)
                or not isinstance(component.get('props'), dict)
                or not isinstance(component.get('pinInfoMap'), dict)):
            raise ValueError('网表元件缺少 props/pinInfoMap，不把未知连接当成空连接')
        for pin_id, pin in component['pinInfoMap'].items():
            if (not nonempty(pin_id) or not isinstance(pin, dict)
                    or not isinstance(pin.get('net'), str)
                    or not isinstance(pin.get('number'), str)):
                raise ValueError('网表引脚缺少 number/net 或格式不兼容')
    return components


def make_snapshot(result: dict, port: int, window_id: str) -> dict:
    project, document = result.get('project'), result.get('document')
    if (not isinstance(project, dict) or not nonempty(project.get('uuid'))
            or not isinstance(document, dict) or not nonempty(document.get('uuid'))
            or document.get('documentType') != 1
            or document.get('parentProjectUuid', project['uuid']) != project['uuid']):
        raise ValueError('网表采集工程/文档身份无效')
    raw = result.get('raw_netlist')
    components = components_from_raw(raw)
    project_summary = {key: project[key] for key in ('uuid', 'name', 'friendlyName') if key in project}
    return {'schema_version': 1, 'kind': 'schematic-connectivity', 'source': 'live-api',
            'captured_at': now(), 'bridge_port': port, 'window_id': window_id,
            'project': project_summary, 'document': document,
            'scope': 'single-api-netlist-export', 'raw_netlist': raw,
            'raw_sha256': hashlib.sha256(raw.encode('utf-8')).hexdigest(),
            'component_count': len(components),
            'limitations': ['范围以本次 API 导出的网表为准，焦点页只是采集锚点；未证明覆盖整个工程或所有板。',
                            '不与其他快照按同名网络合并；层次、跨页和多板关系须结合 CAD 核对。',
                            '不含几何、导线走势或工作条件；空网络表示导出未分配网络，不自动判定悬空故障。',
                            'API 为 beta；已复核工程与焦点，无法检测采集期间文档内部编辑。']}


def neighborhood(snapshot: dict, *, designator: str | None = None,
                 component_id: str | None = None, net: str | None = None,
                 depth: int = 1, limit: int = 100) -> dict:
    if sum(value is not None for value in (designator, component_id, net)) != 1:
        raise ValueError('必须且只能指定 designator、component-id 或 net')
    if not 1 <= depth <= 3 or not 1 <= limit <= 500:
        raise ValueError('depth 范围 1–3，limit 范围 1–500')
    if not isinstance(snapshot, dict) or snapshot.get('kind') != 'schematic-connectivity' or snapshot.get('schema_version') != 1:
        raise ValueError('需要 schematic 命令保存的连接快照')
    raw = snapshot.get('raw_netlist')
    components = components_from_raw(raw)
    if hashlib.sha256(raw.encode('utf-8')).hexdigest() != snapshot.get('raw_sha256'):
        raise ValueError('原始网表哈希不一致')
    members: dict[str, list[dict]] = {}
    component_nets = {}
    for cid, component in components.items():
        component_nets[cid] = set()
        for pin_id, pin in component['pinInfoMap'].items():
            name = pin['net']
            if not name.strip():
                continue  # Two unassigned pins are never electrically joined.
            component_nets[cid].add(name)
            members.setdefault(name, []).append({'component_id': cid, 'pin_id': pin_id,
                'designator': component['props'].get('Designator'),
                'number': pin['number'], 'name': pin.get('name')})
    seed_nets = set()
    if net is not None:
        if net not in members:
            raise ValueError('快照中没有该网络；网络名精确匹配')
        seeds = set()
        seed_nets.add(net)
    elif component_id is not None:
        if component_id not in components:
            raise ValueError('快照中没有该 component-id（使用网表键，不猜测画布图元 ID）')
        seeds = {component_id}
    else:
        seeds = {cid for cid, c in components.items() if c['props'].get('Designator') == designator}
        if len(seeds) != 1:
            raise ValueError('位号未找到或不唯一；请检查快照或用 component-id 消除歧义')
    visited = set(seeds)
    frontier = set(seeds)
    traversed = set()
    truncated = False
    for step in range(depth):
        nets = seed_nets if step == 0 and net is not None else set().union(
            *(component_nets[cid] for cid in frontier))
        traversed.update(nets)
        reached = {p['component_id'] for name in nets for p in members[name]} - visited
        room = max(0, limit - len(visited))
        frontier = set(sorted(reached)[:room])
        truncated |= len(reached) > room
        visited.update(frontier)
        if truncated or not frontier:
            break
    # Bound large ground/power nets as well as component count.
    net_results = []
    for name in sorted(traversed):
        endpoints = [p for p in members[name] if p['component_id'] in visited]
        retained = endpoints[:2000]
        omitted = len(members[name]) - len(retained)
        truncated |= omitted > 0
        net_results.append({'net': name, 'members': retained,
                            'total_members': len(members[name]), 'omitted_members': omitted})
    project = snapshot.get('project') or {}
    return {'project': {key: project[key] for key in ('uuid', 'name', 'friendlyName') if key in project},
            'document': snapshot.get('document'),
            'captured_at': snapshot.get('captured_at'), 'raw_sha256': snapshot['raw_sha256'],
            'scope': snapshot.get('scope'), 'query': {'designator': designator,
                'component_id': component_id, 'net': net, 'depth': depth, 'limit': limit},
            'components': {cid: components[cid] for cid in sorted(visited)},
            'nets': net_results, 'truncated': truncated,
            'limitations': snapshot.get('limitations', []) + [
                'depth 是元件→网络→相邻元件的扩展轮数；连接关系不等于功能/信号方向。',
                '边界元件的其他引脚保留原值，但其其他网络未必已展开；按问题继续查询。',
                'props 中的 Datasheet/型号是 CAD 库属性，须核对厂商原文与适用型号。']}
