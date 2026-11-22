"""Protocol isolation and strict single-sample compilation (no CATIA mutation)."""
import json
from pathlib import Path
import sys
import unittest

from catia_mcp.server import create_server
from catia_mcp.stateful_tools import SelectSpec, SketchFrame
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "whucad_conversion"))
from stateful import arguments, compile_seq


class StatefulContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_protocols_do_not_mix_handles(self):
        legacy = await create_server().list_tools()
        new = await create_server(protocol="whucad-stateful").list_tools()
        self.assertEqual(len(legacy), 18)
        self.assertEqual({x.name for x in new}, {"circle","extrude","selection","fillet","chamfer","model_state"})
        schemas = json.dumps([x.inputSchema for x in new])
        for legacy_handle in ("sketch_id", "object_id", "selection_id", "$match", "support_id"):
            self.assertNotIn(legacy_handle, schemas)
        for tool in new:
            if tool.name != "model_state":
                self.assertIn("model_id", tool.inputSchema["required"])

    def test_unsupported_and_old_programs_fail_closed(self):
        from types import SimpleNamespace
        with self.assertRaises(ValueError):
            compile_seq(SimpleNamespace(extrude_operation=[]))
        for args in ({"radius":{"$ref":[1,"radius"]}}, {"targets":[{"$match":{}}]}, {"model_id":"other"}):
            with self.assertRaises(ValueError):
                arguments({"arguments":args}, "bound_model")
        with self.assertRaises(ValidationError):
            SelectSpec(select_type="Face", body_type="Pad", no_shared_included=[])
        with self.assertRaises(ValidationError):
            SketchFrame(x_direction=(1,0,0), y_direction=(1,0,0))

    def test_script_bypass_is_disabled(self):
        with self.assertRaises(ValueError):
            create_server(protocol="whucad-stateful", with_script=True)


if __name__ == "__main__":
    unittest.main()
