"""Experimental WHUCAD protocol: circle/pad/selection and fillet or chamfer.

COM references live in the worker's per-model state; they are never JSON handles.
Selection uses the unmodified WHUCAD Generic Naming builder, without topo matching.
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
import os
import sys

from mcp.server.fastmcp import Context
from pydantic import Field, model_validator
from .profiles import Input, Frame, Point2, Point3, Positive, Nonnegative, cross


def original():
    root = Path(os.environ.get("WHUCAD_CONVERTER", str(
        Path(__file__).resolve().parents[2] / "vendor" / "whucad_converter")))
    if not (root / "whucad_lib/cadlib/Catia_utils.py").is_file():
        raise RuntimeError("Set WHUCAD_CONVERTER to the original converter directory")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from whucad_lib.cadlib import Catia_utils
    return Catia_utils


class SelectSpec(Input):
    select_type: Literal["Wire", "Face", "Edge"]
    body_type: Literal["None", "Sketch", "Pad"] = "None"
    body_no: int = Field(default=0, ge=0)
    no: int = Field(default=0, ge=0)
    operation_list: list["SelectSpec"] = Field(default_factory=list)

    def native(self):
        return original().Select(self.select_type, self.body_type, self.body_no, self.no,
                                 [x.native() for x in self.operation_list], [], {}, None)


class SketchFrame(Input):
    origin: Point3 = (0, 0, 0)
    x_direction: Point3 = (1, 0, 0)
    y_direction: Point3 = (0, 1, 0)

    @model_validator(mode="after")
    def orthonormal(self):
        Frame(**self.model_dump())
        return self


@dataclass
class State:
    sketch: object = None
    current_feature: object = None
    selected: list = field(default_factory=list)
    revision: int = 0
    selected_revision: int = -1

    def clear(self):
        self.selected = []
        self.selected_revision = -1


def state(model):
    if not hasattr(model, "whucad_state"):
        if model.body.Shapes.Count or model.body.Sketches.Count or model.part.Bodies.Count != 1:
            raise ValueError("Prototype must bind a blank CATPart; existing history is not inferred")
        model.whucad_state = State()
    return model.whucad_state


def status(model):
    s = state(model)
    return {"model_id": model.id, "revision": s.revision,
            "current_sketch": None if s.sketch is None else s.sketch.Name,
            "current_feature": None if s.current_feature is None else s.current_feature.Name,
            "selected_count": len(s.selected)}


def circle_impl(model, center, radius, frame):
    s = state(model)
    if s.sketch is not None or s.current_feature is not None:
        raise ValueError("Minimum prototype supports one circle in the first sketch only")
    created = {}

    def build(m, track):
        p, h = m.part, m.part.HybridShapeFactory
        normal = cross(frame.x_direction, frame.y_direction)
        a = h.AddNewPointCoord(*frame.origin)
        b = h.AddNewPointCoord(*(x+y for x,y in zip(frame.origin, normal)))
        line = h.AddNewLinePtPt(p.CreateReferenceFromObject(a), p.CreateReferenceFromObject(b))
        plane = h.AddNewPlaneNormal(p.CreateReferenceFromObject(line), p.CreateReferenceFromObject(a))
        m.body.InsertHybridShape(plane)
        track(plane)
        sketch = track(m.body.Sketches.Add(plane))
        sketch.SetAbsoluteAxisData((*frame.origin, *frame.x_direction, *frame.y_direction))
        p.InWorkObject = sketch
        f = sketch.OpenEdition()
        try:
            curve = f.CreateClosedCircle(*center, radius)
            curve.ReportName = 1
        finally:
            sketch.CloseEdition()
        created["sketch"] = sketch
        return sketch

    model.run(build)
    s.sketch = created["sketch"]
    s.revision += 1
    s.clear()
    return status(model)


def extrude_impl(model, length, second_length, reversed):
    s = state(model)
    if s.sketch is None or s.current_feature is not None:
        raise ValueError("Extrude requires the pending first circle sketch")
    created = {}
    def build(m, track):
        pad = track(m.part.ShapeFactory.AddNewPad(s.sketch, length))
        pad.SecondLimit.Dimension.Value = second_length
        if reversed:
            pad.DirectionOrientation = 1
        created["feature"] = pad
        return pad
    model.run(build)
    s.current_feature = created["feature"]
    s.revision += 1
    s.clear()
    return status(model)


def selection_impl(model, targets):
    s = state(model)
    # A failed selection must never leave an older successful target armed.
    s.clear()
    if s.current_feature is None or not targets:
        raise ValueError("Selection requires a current feature and nonempty targets")
    model.doc.Activate()
    resolved, labels = [], []
    for spec in targets:
        if spec.select_type not in ("Face", "Edge"):
            raise ValueError("Root selection must be Face or Edge; Wire describes its source")
        def validate(node):
            if node.body_type in ("Sketch", "Pad") and node.body_no != 1:
                raise ValueError("Minimum chain has only Sketch.1 and Pad.1")
            for child in node.operation_list:
                validate(child)
        validate(spec)
        brep = original().parse_BrepName(spec.native(), True, sketch_offset=0, hole_sketch_map={})
        prefix = "RSur" if spec.select_type == "Face" else "REdge"
        label = prefix + ":(" + brep + ";WithTemporaryBody;WithoutBuildError;WithSelectingFeatureSupport;MFBRepVersion_CXR15)"
        ref = model.part.CreateReferenceFromBRepName(label, s.current_feature)
        # Force CATIA resolution now; creating a Reference alone can be lazy.
        measure = model.doc.GetWorkbench("SPAWorkbench").GetMeasurable(ref)
        geometry_type = int(measure.GeometryName)
        if not geometry_type:
            raise ValueError("CATIA did not resolve the selection")
        resolved.append(ref)
        labels.append({"brep": label, "geometry_type": geometry_type})
    s.selected = resolved
    s.selected_revision = s.revision
    return {**status(model), "resolved": labels}


def fillet_impl(model, radius):
    return finish_impl(model, "fillet", radius)


def finish_impl(model, kind, first, second=None):
    s = state(model)
    if not s.selected or s.selected_revision != s.revision:
        raise ValueError("Call selection successfully on the current geometry before finishing")
    refs = list(s.selected)
    s.clear()
    created = {}
    def build(m, track):
        empty = m.part.CreateReferenceFromName("")
        if kind == "fillet":
            feature = track(m.part.ShapeFactory.AddNewSolidEdgeFilletWithConstantRadius(empty, 1, first))
            for ref in refs:
                feature.AddObjectToFillet(ref)
                feature.EdgePropagation = 1
        else:
            feature = track(m.part.ShapeFactory.AddNewChamfer(empty, 0, 0, 0, first, second))
            for ref in refs:
                feature.AddElementToChamfer(ref)
                feature.Mode = 0
                feature.Propagation = 0
                feature.Orientation = 0
        created["feature"] = feature
        return feature
    model.run(build)
    s.current_feature = created["feature"]
    s.revision += 1
    return status(model)


def register(server):
    # The original module imports matplotlib/TkAgg; load it on the main thread,
    # before the STA worker starts, rather than during a selection callback.
    original()
    async def call(ctx, model_id, fn):
        return await ctx.request_context.lifespan_context.call(lambda session: fn(session.get(model_id)))

    @server.tool()
    async def model_state(ctx: Context, model_id: str | None = None) -> dict:
        """Bind the active blank CATPart once; later inspect per-model current state. No selection matching."""
        def inspect(session):
            model = session.bind(session.app.ActiveDocument) if model_id is None else session.get(model_id)
            return status(model)
        return await ctx.request_context.lifespan_context.call(inspect)

    @server.tool()
    async def circle(ctx: Context, model_id: str, center: Point2, radius: Positive, frame: SketchFrame = SketchFrame()) -> dict:
        """Create the first real sketch and its circle (mm). Store the current sketch in this model."""
        return await call(ctx, model_id, lambda m: circle_impl(m, center, radius, frame))

    @server.tool()
    async def extrude(ctx: Context, model_id: str, length: Positive, second_length: Nonnegative = 0, reversed: bool = False) -> dict:
        """Pad the model's current circle sketch using offset limits (mm)."""
        return await call(ctx, model_id, lambda m: extrude_impl(m, length, second_length, reversed))

    @server.tool()
    async def selection(ctx: Context, model_id: str, targets: list[SelectSpec]) -> dict:
        """Resolve WHUCAD Select trees against the current feature and store targets. No object_id/ref/match."""
        return await call(ctx, model_id, lambda m: selection_impl(m, targets))

    @server.tool()
    async def fillet(ctx: Context, model_id: str, radius: Positive) -> dict:
        """Fillet the stored selection (mm). Success advances the current feature; selection is consumed."""
        return await call(ctx, model_id, lambda m: fillet_impl(m, radius))

    @server.tool()
    async def chamfer(ctx: Context, model_id: str, length1: Positive, length2: Positive) -> dict:
        """Two-length chamfer (mm), tangency propagation and no reverse, following original WHUCAD. Consumes selection."""
        return await call(ctx, model_id, lambda m: finish_impl(m, "chamfer", length1, length2))
