"""The staged protocol must not silently collapse unsupported parameter meanings."""
import unittest
from pathlib import Path
import sys
from pydantic import ValidationError
from catia_mcp.server import create_server
from catia_mcp.stateful_migration import Selection,History,select
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'whucad_conversion'))
from migration import compile_vec


class MigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_separates_query_and_markers(self):
        tools={x.name:x for x in await create_server(protocol='whucad-migration').list_tools()}
        self.assertEqual(set(tools),{'model_state','sol','topo','mirror_topo','selection','line','arc','circle','spline','scp',
                                    'extrude','pocket','revolve','groove','fillet','chamfer','shell','mirror','hole','draft'})
        self.assertIn('does not query',tools['topo'].description)
        for name,tool in tools.items():
            if name!='model_state':self.assertIn('model_id',tool.inputSchema['required'])
            for old in ('object_id','edge_id','sketch_id','group_id'):
                self.assertNotIn(old,tool.inputSchema['properties'])
        self.assertNotIn('frame',tools['circle'].inputSchema['properties'])
        self.assertIn('frame',tools['extrude'].inputSchema['properties'])

    def test_nested_selection_markers_preserved(self):
        wire=Selection(select_type='Wire',body_type='Sketch',body_no=2,no=4)
        face=Selection(select_type='Face',body_type='Pad',body_no=2,no=0,operation_list=[wire],
            all_oriented_included={'Limits1':[wire],'Limits2':[wire]},all_partially_included=[wire])
        native=face.native()
        self.assertEqual(native.operation_list[0].no,4)
        self.assertEqual(native.all_oriented_included['Limits2'][0].body_no,2)
        self.assertEqual(native.all_partially_included[0].body_type,'Sketch')
        with self.assertRaises(ValidationError):Selection(select_type='Face',body_type='Pad',body_no=-1)

    def test_missing_enum_is_not_a_valid_negative_python_index(self):
        import numpy as np
        row=np.full((1,33),-1);row[0,0]=7
        with self.assertRaisesRegex(ValueError,'source_missing_or_invalid_limit'):compile_vec(row)
        row[0,15:17]=0
        row[0,19]=3
        with self.assertRaisesRegex(ValueError,'source_missing_or_invalid_boolean'):compile_vec(row)

    def test_old_sketch_is_not_shifted_by_later_hole(self):
        h=History(sketch_offset=3,profile_count=1,sketch_bindings={1:1})
        model=SimpleNamespace(id='test',migration_history=h)
        result=select(model,[Selection(select_type='Face',body_type='Pad',body_no=1,operation_list=[Selection(select_type='Wire',body_type='Sketch',body_no=1,no=2)])])
        self.assertIn('Sketch.1;2',result['descriptions'][0])
        self.assertNotIn('Sketch.4',result['descriptions'][0])

    def test_explicit_padding_compat_is_logged_and_rejects_other_invalid_values(self):
        import numpy as np
        vector=np.full((2,33),-1);vector[:,0]=7;vector[:,15:17]=0;vector[0,19]=0
        fake=SimpleNamespace(BOOLEAN_OPERATIONS=['Add','Cut','Intersect'],EXTENT_TYPE=['distance','next','last','plane','surface','through'],Macro_Seq=SimpleNamespace(from_vector=lambda *a:SimpleNamespace(extrude_operation=[])))
        with patch('migration.original',return_value=fake):
            with self.assertRaisesRegex(ValueError,'invalid_boolean'):compile_vec(vector)
            result=compile_vec(vector,original_padding_compat=True)
            self.assertEqual(result['normalizations'][0]['value'],'Intersect')
            self.assertEqual(result['normalizations'][0]['raw'],-1)
            vector[1,19]=-2
            with self.assertRaisesRegex(ValueError,'invalid_boolean'):compile_vec(vector,original_padding_compat=True)

    def test_surface_hole_internal_sketch_is_not_translated_twice(self):
        h=History(sketch_offset=1,profile_count=1,sketch_bindings={1:1},hole_replacements={1:dict(pocket=1,sketch=2)})
        result=select(SimpleNamespace(id='test',migration_history=h),[Selection(select_type='Face',body_type='Hole',body_no=1,no=3)])
        self.assertIn('Sketch.2;1',result['descriptions'][0])


if __name__=='__main__':unittest.main()
