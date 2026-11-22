"""Run with unittest. Set CATIA_LIVE_TESTS=1 for the disposable CATPart tests."""

import json
import os
import sys
import unittest
from functools import wraps
from math import pi

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from catia_mcp.connection import run_in_catia
from catia_mcp.topology import incidence


class IncidenceTest(unittest.TestCase):
    def test_only_direct_unambiguous_face_descriptors_in_this_result(self):
        history = "Face:(Brp:(Pad.1;2);None:();Cf11:())"
        outer = f"Face:(Brp:(SomeFeature;{history});None:();Cf11:())"
        other = "Face:(Brp:(Pad.2;2);None:();Cf11:())"
        edge = f"Selection_REdge:(Edge:({outer};{other};None:(Limits1:({history}));Cf11:());Tip;Z0;G1)"

        def face(descriptor, tip="Tip", version="G1"):
            return "face", f"Selection_RSur:({descriptor};{tip};Z0;{version})", None

        records = {"edge": ("edge", edge, None), "outer": face(outer), "history": face(history),
                   "a": face(other), "b": face(other, version="G2"), "other_tip": face(outer, tip="OtherTip")}
        links = incidence(records)
        self.assertEqual(links["edge"], ["outer"])
        self.assertEqual(links["outer"], ["edge"])
        self.assertTrue(all(not links[k] for k in ["history", "a", "b", "other_tip"]))


def connected(test):
    @wraps(test)
    async def run(self):
        params = StdioServerParameters(command=sys.executable, args=["-m", "catia_mcp", *self.server_args])
        async with stdio_client(params) as streams, ClientSession(*streams) as client:
            self.client = client
            await client.initialize()
            await test(self)

    return run


class ToolTestCase(unittest.IsolatedAsyncioTestCase):
    server_args = ()

    async def call(self, tool, **args):
        response = await self.client.call_tool(tool, args)
        self.assertFalse(response.isError, response.content)
        return json.loads(response.content[0].text)


class ProtocolTest(ToolTestCase):
    @connected
    async def test_table_one_and_sketch_validation(self):
        expected = [
            "line",
            "arc",
            "circle",
            "spline",
            "scp",
            "extrude",
            "selection",
            "revolve",
            "pocket",
            "groove",
            "topo",
            "shell",
            "chamfer",
            "fillet",
            "hole",
            "mirror",
            "mirror_topo",
            "draft",
        ]
        tools = (await self.client.list_tools()).tools
        self.assertCountEqual([tool.name for tool in tools], expected)
        self.assertTrue(all("ctx" not in tool.inputSchema["properties"] for tool in tools))
        start = await self.call("spline", start=[0, 0])
        incomplete = await self.client.call_tool(
            "extrude", {"sketch_id": start["sketch_id"], "length_mm": 5}
        )
        self.assertTrue(incomplete.isError)
        end = await self.call("scp", sketch_id=start["sketch_id"], point=[2, 3])
        self.assertNotEqual(start["sketch_id"], end["sketch_id"])
        invalid = await self.client.call_tool("circle", {"center": [0, 0], "radius_mm": -1})
        self.assertTrue(invalid.isError)


class ScriptProtocolTest(ToolTestCase):
    server_args = ("--with-script",)

    @connected
    async def test_script_catalog_and_arguments(self):
        catalog = (await self.client.list_tools()).tools
        self.assertEqual(len(catalog), 19)
        entry = next(t for t in catalog if t.name == "run_catia_script")
        self.assertEqual(entry.inputSchema["required"], ["code"])
        invalid = await self.client.call_tool("run_catia_script", {"code": "pass", "timeout_seconds": 0})
        self.assertTrue(invalid.isError)


@unittest.skipUnless(os.environ.get("CATIA_LIVE_TESTS") == "1", "needs CATIA V5")
class LiveGeometryTest(ToolTestCase):
    server_args = ("--with-script",)

    async def solid(self, operation="extrude", **args):
        result = await self.call(operation, **args)
        doc_name = result["document"]

        def close(app):
            alerts = app.DisplayFileAlerts
            try:
                app.DisplayFileAlerts = False
                app.Documents.Item(doc_name).Close()
            finally:
                app.DisplayFileAlerts = alerts

        self.addAsyncCleanup(run_in_catia, close)
        return result["model_id"]

    @connected
    async def test_solid_edges_selection_group_rollback_and_stale_id(self):
        profile = await self.call("circle", center=[0, 0], radius_mm=10)
        model = await self.solid(sketch_id=profile["sketch_id"], length_mm=12)

        async def candidates():
            return await self.call("topo", model_id=model, kind="edge")

        initial = await candidates()
        # A consumed circular sketch also appears in CATIA's body search.
        # Only the two actual solid rims belong in the MCP menu.
        self.assertEqual(len(initial["candidates"]), 2)
        faces = (await self.call("topo", model_id=model, kind="face"))["candidates"]
        face_by_id = {f["object_id"]: f for f in faces}
        for rim in initial["candidates"]:
            center = rim["circle_center_mm"]
            self.assertAlmostEqual(rim["radius_mm"], 10)
            self.assertAlmostEqual(center[0], 0)
            self.assertAlmostEqual(center[1], 0)
            self.assertAlmostEqual(center[2], rim["midpoint_mm"][2])
            neighbors = [face_by_id[key] for key in rim["adjacent_face_ids"]]
            self.assertCountEqual([f["geometry_type"] for f in neighbors], ["plane", "cylinder"])
            for face in neighbors:
                self.assertIn(rim["object_id"], face["boundary_edge_ids"])
                self.assertEqual(face["center_kind"], "area_centroid")
                if face["geometry_type"] == "plane":
                    self.assertAlmostEqual(abs(face["normal_unit"][2]), 1)
        edge = max(initial["candidates"], key=lambda row: row["midpoint_mm"][2])["object_id"]
        await self.call("selection", model_id=model, group_id=initial["group_id"], object_id=edge)
        invalid = await self.client.call_tool(
            "fillet", {"model_id": model, "edge_id": edge, "radius_mm": 200}
        )
        self.assertTrue(invalid.isError)
        self.assertEqual(
            {r["object_id"] for r in (await candidates())["candidates"]},
            {r["object_id"] for r in initial["candidates"]},
        )
        await self.call("fillet", model_id=model, edge_id=initial["group_id"], radius_mm=1)
        stale = await self.client.call_tool("selection", {"model_id": model, "object_id": edge})
        self.assertTrue(stale.isError)

    @connected
    async def test_revolution_axes_on_localized_catia(self):
        outer = await self.call("circle", center=[8, 0], radius_mm=2.5)
        model = await self.solid("revolve", sketch_id=outer["sketch_id"], angle_deg=360)
        inner = await self.call("circle", center=[9, 0], radius_mm=2)
        await self.call("groove", model_id=model, sketch_id=inner["sketch_id"], angle_deg=90)

    @connected
    async def test_script_creates_editable_circular_pattern(self):
        profile = await self.call("circle", center=[0, 0], radius_mm=30)
        model = await self.solid(sketch_id=profile["sketch_id"], length_mm=5)
        faces = (await self.call("topo", model_id=model, kind="face"))["candidates"]
        top = max(faces, key=lambda f: f["center_mm"][2])
        seed_profile = await self.call("circle", center=[20, 0], radius_mm=2)
        pocket = await self.call("pocket", model_id=model, sketch_id=seed_profile["sketch_id"],
                                 depth_mm=5, frame={"origin": [0, 0, 5], "support_id": top["object_id"]})
        before = await self.call("topo", model_id=model, kind="edge")
        code = """\
part = catia.ActiveDocument.Part
points = part.HybridBodies.Add()
factory = part.HybridShapeFactory
origin = factory.AddNewPointCoord(0, 0, 0)
points.AppendHybridShape(origin)
center = part.CreateReferenceFromObject(origin)
axis = factory.AddNewLinePtDir(center, factory.AddNewDirectionByCoord(0, 0, 1), 0, 10, False)
points.AppendHybridShape(axis)
part.Update()
seed = part.MainBody.Shapes.Item(%r)
part.InWorkObject = seed
pattern = part.ShapeFactory.AddNewCircPattern(
    seed, 1, 6, 0., 60., 1, 1, center, part.CreateReferenceFromObject(axis), False, 0., True)
pattern.CircularPatternParameters = 1  # catCompleteCrown
pattern.AngularRepartition.InstancesCount.Value = 6
part.Update()
volume = catia.ActiveDocument.GetWorkbench("SPAWorkbench").GetMeasurable(
    part.CreateReferenceFromObject(part.MainBody)).Volume
print(pattern.AngularRepartition.InstancesCount.Value, volume)
""" % pocket["feature_name"]
        response = await self.call("run_catia_script", model_id=model, code=code)
        count, volume_m3 = response["stdout"].split()
        self.assertEqual(count, "6")
        self.assertAlmostEqual(float(volume_m3) * 1e9, pi * (30**2 - 6 * 2**2) * 5, places=3)
        stale = await self.client.call_tool("selection", {
            "model_id": model, "object_id": before["candidates"][0]["object_id"]})
        self.assertTrue(stale.isError)
        rims = (await self.call("topo", model_id=model, kind="edge"))["candidates"]
        holes = [r for r in rims if abs(r.get("radius_mm", 0) - 2) < 1e-6]
        self.assertCountEqual([round(r["circle_center_mm"][2], 5) for r in holes],
                              [0] * 6 + [5] * 6, rims)
        edit = """\
part = catia.ActiveDocument.Part
shapes = part.MainBody.Shapes
pattern = shapes.Item(shapes.Count)
pattern.CircularPatternParameters = 0  # catInstancesandAngularSpacing
pattern.AngularRepartition.InstancesCount.Value = 8
pattern.AngularRepartition.AngularSpacing.Value = 45
part.Update()
print(catia.ActiveDocument.GetWorkbench("SPAWorkbench").GetMeasurable(
    part.CreateReferenceFromObject(part.MainBody)).Volume)
"""
        updated = await self.call("run_catia_script", model_id=model, code=edit)
        self.assertAlmostEqual(float(updated["stdout"]) * 1e9, pi * (30**2 - 8 * 2**2) * 5, places=3)

    @connected
    async def test_script_diagnostics_and_recovery(self):
        for code, timeout, exit_code in [
            ("print('中文'); raise ValueError('bad dimension')", 10, 1),
            ("import time; print('中文'); time.sleep(30)", 3, None),
        ]:
            result = await self.client.call_tool("run_catia_script", {"code": code, "timeout_seconds": timeout})
            self.assertTrue(result.isError)
            data = result.structuredContent
            self.assertEqual(data["stdout"].strip(), "中文")
            self.assertEqual(data["exit_code"], exit_code)
            self.assertEqual(data["timed_out"], exit_code is None)
            if exit_code is not None:
                self.assertIn("ValueError: bad dimension", data["stderr"])
        recovered = await self.call("run_catia_script", code="print(catia.Name)")
        self.assertEqual(recovered["exit_code"], 0)
        self.assertTrue(recovered["stdout"].strip())


if __name__ == "__main__":
    unittest.main()
