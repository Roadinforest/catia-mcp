import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from execute_mcp_json import validate,validators


class ExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):self.schemas=await validators()

    def program(self,calls):return dict(protocol='whucad-migration',version=3,calls=calls)

    async def test_valid_calls_accept_runtime_model_binding(self):
        p=self.program([dict(tool='circle',arguments=dict(center=[0,0],radius=2)),dict(tool='extrude',arguments=dict(length=5))])
        self.assertEqual(validate(p,self.schemas),2)

    async def test_rejects_legacy_and_embedded_model_binding(self):
        for argument in ({'model_id':'old'},{'radius':{'$ref':[1,'radius']}},{'radius':{'$match':{}}}):
            with self.assertRaises(ValueError):validate(self.program([dict(tool='circle',arguments=argument)]),self.schemas)

    async def test_rejects_invalid_radius_and_management_calls(self):
        for call in (dict(tool='circle',arguments=dict(center=[0,0],radius=0)),dict(tool='model_state',arguments={})):
            with self.assertRaises(ValueError):validate(self.program([call]),self.schemas)


if __name__=='__main__':unittest.main()
