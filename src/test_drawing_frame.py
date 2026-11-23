"""Reserved interiors must be measured from a source-bound native export."""
from copy import deepcopy
import unittest

import test_drawing_incremental
from test_drawing_circuit import line
from easyeda_assist.design_audit import source_digest
from easyeda_assist.drawing_frame import frame_geometry
from easyeda_assist.drawing_source import records
from easyeda_assist.drawing_incremental import validate_baseline, validate_edit_region, validate_frame_box
from easyeda_assist.drawing_execution import preflight
from easyeda_assist.storage import write_new, digest


SVG = ('<svg xmlns="http://www.w3.org/2000/svg"><g class="shapeBox"><g c_partid="sheet">'
       '<g><rect x="0" y="-825" width="1170" height="825"/></g>'
       '<g><rect x="10" y="-815" width="1150" height="805"/></g>'
       '<g c_partid="table"><g><rect x="460" y="-190" width="700" height="180"/></g>'
       '<text x="500" y="-100" transform="rotate(0 500 -100)">Title</text></g>'
       '<image x="484" y="-54" width="165" height="47"/>'
       '</g></g></svg>')


def add_frame(bundle, before):
    """Attach a realistic sheet to existing small independent/branch fixtures."""
    extra = line('COMPONENT', 'frame', {'x':0, 'y':0, 'rotation':0, 'isMirror':False})
    attrs = {'Size':'A4','Width':'1170','Height':'825','Blade Width':'10',
             'Border':'1','Title Block':'1','Title Block Position':'3'}
    for i,(key,value) in enumerate(attrs.items()):
        extra += line('ATTR','frame-attr-'+str(i), {'parentId':'frame','key':key,'value':value})
    before['source'] += extra
    bundle['audit']['source'] += extra
    bundle['audit']['source_sha256'] = source_digest(before['source'])
    bundle['inventory']['source'] += extra
    bundle['inventory']['components'].append({'primitive_id':'frame','component_type':'sheet',
        'x':0,'y':0,'rotation':0,'mirror':False})
    bundle['inventory']['total'] += 1
    for query in bundle['geometry']:
        query['data']['source'] += extra
    bundle['frame_svg'] = {**before, 'svg':SVG}
    return extra


class FrameTests(unittest.TestCase):
    def setUp(self):
        fixture = test_drawing_incremental.IncrementalTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        for name in ('root','bundle','before','plan','project'):
            setattr(self,name,deepcopy(getattr(fixture,name)))
        add_frame(self.bundle,self.before)

    def frame(self):
        return frame_geometry(records(self.before['source']),
            self.bundle['inventory']['components'],self.bundle['frame_svg'])

    def test_native_export_includes_cell_interiors_and_logo(self):
        self.assertEqual(self.frame()['title_block'],[459,6,1161,191])
        validate_baseline(self.bundle)
        validate_frame_box(self.frame(),[20,200,200,240],'fixture')
        for b in ([800,100,900,100],[800,100,800,150],[454,180,454,200],
                  [800,196,900,196],[0,300,30,330],[1156,300,1156,330]):
            with self.subTest(box=b),self.assertRaises(ValueError):
                validate_frame_box(self.frame(),b,'fixture')
        validate_frame_box(self.frame(),[800,197,900,197],'fixture')

    def test_geometry_is_read_from_export_not_symbol_name(self):
        self.bundle['frame_svg']['svg']=SVG.replace('y="-190" width="700" height="180"',
                                                   'y="-250" width="700" height="240"')
        self.assertEqual(self.frame()['title_block'][3],251)

    def test_missing_or_wrong_source_svg_fails_closed(self):
        for change in ('missing','source','window','identity'):
            bundle=deepcopy(self.bundle)
            if change=='missing':
                bundle.pop('frame_svg')
                validate_baseline(bundle)  # Historical audit/cleanup input remains readable.
                with self.assertRaises(ValueError):
                    validate_edit_region([50,150,200,250], self.before, [],
                                         bundle['inventory']['components'], None)
                continue
            if change=='source':bundle['frame_svg']['source']+='\n'+line('WIRE','extra',{})
            elif change=='window':bundle['frame_svg']['window_id']='other'
            else:bundle['frame_svg']['identity']['document_uuid']='other'
            with self.subTest(change=change),self.assertRaises(ValueError):validate_baseline(bundle)

    def test_unknown_geometry_layout_or_duplicate_attributes_fail_closed(self):
        for source in (
            self.before['source'].replace('"value": "3"','"value": "2"'),
            self.before['source'].replace('"value": "A4"','"value": "A3"'),
            self.before['source'].replace('"x": 0, "y": 0','"x": 1, "y": 0'),
            self.before['source']+line('ATTR','duplicate',{'parentId':'frame','key':'Width','value':'1170'})):
            with self.subTest(source=source[-100:]),self.assertRaises(ValueError):
                frame_geometry(records(source),self.bundle['inventory']['components'],self.bundle['frame_svg'])

    def test_unsupported_svg_transform_border_and_missing_table_rejected(self):
        for svg in (SVG.replace('c_partid="sheet"','c_partid="sheet" transform="translate(20 0)"'),
                    SVG.replace('c_partid="table"','c_partid="table" transform="scale(2)"'),
                    SVG.replace('width="1150"','width="1100"'),
                    SVG.replace('c_partid="table"','c_partid="other"'),
                    SVG.replace('rotate(0 500 -100)','rotate(90 500 -100)'),
                    SVG.replace('width="700"','width="NaN"'),
                    '<!DOCTYPE svg [<!ENTITY x "bad">]>'+SVG):
            with self.subTest(svg=svg[:150]),self.assertRaises(ValueError):
                frame_geometry(records(self.before['source']),self.bundle['inventory']['components'],{'svg':svg})

    def test_v4_preflight_exposes_measured_frame_and_rejects_title_region(self):
        bp=write_new(self.root/'frame-baseline.json',self.bundle)
        sp=write_new(self.root/'frame-source.json',self.before)
        self.plan['incremental_baseline']={'path':str(bp),'sha256':digest(bp)}
        self.plan['target'].update(snapshot_path=str(sp),snapshot_sha256=digest(sp))
        checked,_=preflight(self.project,self.plan)
        self.assertEqual(checked['preview']['sheet_frame']['title_block'],[459,6,1161,191])
        self.plan['edit_region']=[800,100,900,150]
        with self.assertRaisesRegex(ValueError,'标题栏'):preflight(self.project,self.plan)


if __name__ == '__main__':
    unittest.main()
