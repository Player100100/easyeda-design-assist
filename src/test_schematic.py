from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from unittest.mock import patch

from easyeda_assist.bridge import BridgeClient, BridgeError
from easyeda_assist.schematic import SCHEMATIC_QUERY, make_snapshot, neighborhood


def component(designator, nets):
    return {'props': {'Designator': designator, 'Manufacturer Part': 'FICTIONAL'},
            'pinInfoMap': {str(i): {'number': str(i), 'name': str(i), 'net': net}
                           for i, net in enumerate(nets, 1)}}


def snapshot(components=None):
    return make_snapshot({'project': {'uuid': 'p'}, 'document': {
        'uuid': 'd', 'documentType': 1, 'parentProjectUuid': 'p'},
        'raw_netlist': json.dumps({'version': '2.0.0', 'components': components or {
            'a': component('U1', ['SUPPLY', 'OUT', '']),
            'b': component('R1', ['OUT', 'FEEDBACK']),
            'c': component('U2', ['FEEDBACK', '']),
            'd': component('U3', ['', 'OTHER']),
        }})}, 49620, 'window')


class ConnectivityTests(unittest.TestCase):
    def test_traces_real_nets_preserves_pins_and_does_not_join_empty_nets(self):
        one = neighborhood(snapshot(), designator='U1')
        self.assertEqual(set(one['components']), {'a', 'b'})
        self.assertEqual({n['net'] for n in one['nets']}, {'SUPPLY', 'OUT'})
        self.assertEqual(one['components']['a']['pinInfoMap']['3']['net'], '')
        two = neighborhood(snapshot(), component_id='a', depth=2)
        self.assertEqual(set(two['components']), {'a', 'b', 'c'})
        self.assertNotIn('d', neighborhood(snapshot(), component_id='a', depth=3)['components'])
        net = neighborhood(snapshot(), net='FEEDBACK')
        self.assertEqual(set(net['components']), {'b', 'c'})
        self.assertEqual(net['nets'][0]['members'][0]['number'], '2')

    def test_bounds_large_nets_and_marks_omitted_members(self):
        result = neighborhood(snapshot(), designator='U1', limit=1)
        self.assertTrue(result['truncated'])
        self.assertEqual(set(result['components']), {'a'})
        output = next(n for n in result['nets'] if n['net'] == 'OUT')
        self.assertEqual(output['omitted_members'], 1)

    def test_ambiguous_identity_invalid_format_and_tampered_snapshot_fail(self):
        ambiguous = snapshot({'a': component('U1', ['N']), 'b': component('U1', ['N'])})
        with self.assertRaisesRegex(ValueError, '不唯一'):
            neighborhood(ambiguous, designator='U1')
        with self.assertRaises(ValueError):
            neighborhood(snapshot(), net='')
        with self.assertRaises(ValueError):
            neighborhood(snapshot(), net='out')
        with self.assertRaises(ValueError):
            neighborhood(snapshot(), component_id='a', depth=4)
        changed = snapshot()
        changed['raw_netlist'] += ' '
        with self.assertRaisesRegex(ValueError, '哈希'):
            neighborhood(changed, designator='U1')
        malformed = component('U1', ['N'])
        del malformed['pinInfoMap']['1']['net']
        with self.assertRaisesRegex(ValueError, '引脚缺少'):
            snapshot({'a': malformed})
        with self.assertRaisesRegex(ValueError, '重复 JSON'):
            raw = snapshot()
            raw['raw_netlist'] = '{"version":"2.0.0","version":"2.0.0","components":{}}'
            neighborhood(raw, net='N')
        with self.assertRaisesRegex(ValueError, '连接快照'):
            neighborhood([], designator='U1')

    def test_snapshot_keeps_project_identity_without_copying_entire_project_tree(self):
        result = {'project': {'uuid': 'p', 'name': 'user/project', 'private_tree': {'secret': 'large'}},
                  'document': {'uuid': 'd', 'documentType': 1, 'parentProjectUuid': 'p'},
                  'raw_netlist': snapshot()['raw_netlist']}
        captured = make_snapshot(result, 49620, 'w')
        self.assertEqual(captured['project'], {'uuid': 'p', 'name': 'user/project'})

    def test_fixed_read_requires_explicit_window_and_checks_response_identity(self):
        client = BridgeClient(49620)
        with patch.object(client, 'health'), patch.object(client, 'windows', return_value=[
                {'windowId': 'one'}, {'windowId': 'two'}]), patch.object(client, '_request') as request:
            with self.assertRaisesRegex(BridgeError, '多个'):
                client.schematic()
            request.assert_not_called()
            request.return_value = {'success': True, 'windowId': 'other', 'result': {}}
            with self.assertRaisesRegex(BridgeError, '窗口与请求不一致'):
                client.schematic('one')
            self.assertEqual(request.call_args.args[1]['code'], SCHEMATIC_QUERY)


HARNESS = r"""
const fs=require('fs'); const input=JSON.parse(fs.readFileSync(0,'utf8'));
let reads=0; const calls=[];
const eda={
 dmt_Project:{async getCurrentProjectInfo(){return {uuid:'p'};}},
 dmt_SelectControl:{async getCurrentDocumentInfo(){
  return {uuid:++reads>1&&input.changed?'other':'d',tabId:'t',documentType:input.pcb?3:1,parentProjectUuid:'p'};
 }},
 sch_ManufactureData:{async getNetlistFile(name,type){
  calls.push([name,type]);
  if(input.failed)throw new Error('export validation failed');
  return {size:input.large?30000000:100, async text(){return input.raw;}};
 }}
};
const AsyncFunction=Object.getPrototypeOf(async function(){}).constructor;
new AsyncFunction('eda',input.code)(eda).then(result=>{
 process.stdout.write(JSON.stringify({result,calls}));
},error=>process.stdout.write(JSON.stringify({error:String(error),calls})));
"""


@unittest.skipUnless(shutil.which('node'), 'Node required for query runtime test')
class SchematicQueryTests(unittest.TestCase):
    def run_query(self, **options):
        result = subprocess.run([shutil.which('node'), '-e', HARNESS],
            input=json.dumps({'code': SCHEMATIC_QUERY, 'raw': snapshot()['raw_netlist'], **options}),
            capture_output=True, text=True, encoding='utf-8', timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_export_uses_documented_fixed_type_and_preserves_raw(self):
        reply = self.run_query()
        self.assertEqual(reply['calls'], [['design-assist', 'JLCEDA']])
        self.assertEqual(make_snapshot(reply['result'], 49620, 'one')['component_count'], 4)

    def test_document_change_wrong_document_large_export_and_api_failure(self):
        for options, message in [({'changed': True}, '焦点文档变化'),
                                 ({'pcb': True}, '原理图页'),
                                 ({'large': True}, '20 MiB'),
                                 ({'failed': True}, 'export validation failed')]:
            with self.subTest(options=options):
                self.assertIn(message, self.run_query(**options)['error'])
