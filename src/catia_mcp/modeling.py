"""CATIA Part Design operations. All COM objects in this module stay on one STA."""

import math

from .profiles import Frame, arc_geometry, cross, new_id
from .topology import Targets, components, selection_buffer


class Model:
    def __init__(self, app, doc):
        self.app, self.doc = app, doc
        self.part = doc.Part
        self.id = new_id("model")
        self.targets = Targets(self)

    @property
    def body(self):
        return self.part.MainBody

    def run(self, callback):
        import pythoncom

        self.doc.Activate()
        part = self.part
        previous = part.InWorkObject
        created = []

        def track(obj):
            created.append(obj)
            return obj

        try:
            part.InWorkObject = self.body
            feature = callback(self, track)
            part.Update()
            name = feature.Name
        except Exception as exc:
            try:
                if created:
                    with selection_buffer(self.doc) as selection:
                        selection.Clear()
                        for obj in created:
                            selection.Add(obj)
                        selection.Delete()
                part.InWorkObject = previous
                if created:
                    part.Update()
            except (pythoncom.com_error, AttributeError) as cleanup:
                self.targets.changed()
                raise RuntimeError(
                    f"CATIA operation failed ({exc}); rollback failed ({cleanup}). Inspect the part."
                ) from exc
            raise
        self.targets.changed()
        return {"model_id": self.id, "feature_name": name, "document": self.doc.Name}


class Session:
    def __init__(self, app):
        self.app = app
        self.models = {}

    def bind(self, doc):
        for model in self.models.values():
            if model.doc == doc:
                return model
        model = Model(self.app, doc)
        self.models[model.id] = model
        return model

    def get(self, model_id):
        if model_id not in self.models:
            raise ValueError("Unknown model_id. Use the ID returned by topo, extrude or revolve.")
        return self.models[model_id]

    def inspect(self, model_id, kind, mirrored=False, include_brep=False):
        model = self.bind(self.app.ActiveDocument) if model_id is None else self.get(model_id)
        model.doc.Activate()
        return model.targets.discover(kind, mirrored, include_brep)

    def change(self, model_id, operation):
        if model_id is not None:
            return self.get(model_id).run(operation)
        doc = self.app.Documents.Add("Part")
        model = None
        try:
            model = self.bind(doc)
            return model.run(operation)
        except Exception:
            if model is not None:
                self.models.pop(model.id, None)
            alerts = self.app.DisplayFileAlerts
            try:
                self.app.DisplayFileAlerts = False
                doc.Close()
            finally:
                self.app.DisplayFileAlerts = alerts
            raise


def draw_sketch(model, body, curves, frame, track, local_axis=None):
    part = model.part
    if frame.support_id:
        support = model.targets.one(frame.support_id, "face", "plane")
        measure = model.doc.GetWorkbench("SPAWorkbench").GetMeasurable(support)
        plane = components(model.app, measure, "GetPlane", 9)
        normal = cross(plane[3:6], plane[6:9])
        delta = tuple(a - b for a, b in zip(frame.origin, plane[:3]))
        if any(
            abs(sum(a * b for a, b in zip(v, normal))) > 1e-6
            for v in (delta, frame.x_direction, frame.y_direction)
        ):
            raise ValueError(
                "Frame origin and both sketch directions must lie on the support plane."
            )
    elif frame == Frame():
        support = part.OriginElements.PlaneXY
    else:
        factory = part.HybridShapeFactory
        origin = frame.origin
        positions = [
            origin,
            *(
                tuple(a + b for a, b in zip(origin, direction))
                for direction in (frame.x_direction, frame.y_direction)
            ),
        ]
        points = [part.CreateReferenceFromObject(factory.AddNewPointCoord(*p)) for p in positions]
        plane = factory.AddNewPlane3Points(*points)
        body.InsertHybridShape(plane)
        track(plane)
        support = part.CreateReferenceFromObject(plane)
    sketch = track(body.Sketches.Add(support))
    sketch.SetAbsoluteAxisData((*frame.origin, *frame.x_direction, *frame.y_direction))
    part.InWorkObject = sketch
    factory = sketch.OpenEdition()
    axis_ref = None
    try:
        for kind, *args in curves:
            if kind == "line":
                factory.CreateLine(*args[0], *args[1])
            elif kind == "circle":
                factory.CreateClosedCircle(*args[0], args[1])
            elif kind == "arc":
                factory.CreateCircle(*arc_geometry(*args))
            else:
                points = tuple(factory.CreateControlPoint(*p) for p in args)
                factory.CreateSpline(points)
        if local_axis:
            end = (1.0, 0.0) if local_axis == "horizontal" else (0.0, 1.0)
            line = factory.CreateLine(0.0, 0.0, *end)
            line.Construction = True
            sketch.CenterLine = line
            axis_ref = part.CreateReferenceFromObject(line)
    finally:
        sketch.CloseEdition()
    part.UpdateObject(sketch)
    return sketch, axis_ref


LIMIT_MODES = {"distance": 0, "next": 1, "last": 2, "plane": 3, "surface": 4, "through_next": 5}


def set_limit(limit, spec, length, reference):
    limit.LimitMode = LIMIT_MODES[spec.mode]
    if reference is not None:
        limit.LimitingElement = reference
    if spec.mode == "distance":
        limit.Dimension.Value = length


def profile_feature(
    model,
    track,
    kind,
    curves,
    frame,
    operation,
    first,
    second,
    first_limit=None,
    second_limit=None,
    axis="vertical",
    reverse=False,
):
    part = model.part
    body = model.body
    factory = part.ShapeFactory
    # Resolve all targets against the unmodified main body.
    axis_ref = None
    if axis not in ("horizontal", "vertical"):
        axis_ref = model.targets.one(axis, "edge")
    limits = [
        model.targets.one(s.target_id, "face", "plane") if s and s.target_id else None
        for s in (first_limit, second_limit)
    ]
    boolean = operation in ("cut", "intersect")
    if (boolean or kind in ("pocket", "groove")) and body.Shapes.Count == 0:
        raise ValueError("This operation needs an existing solid.")
    if boolean:
        body = track(part.Bodies.Add())
        part.InWorkObject = body
    local_axis = axis if kind in ("revolve", "groove") and axis_ref is None else None
    sketch, sketch_axis = draw_sketch(model, body, curves, frame, track, local_axis)
    if kind in ("extrude", "pocket"):
        create = factory.AddNewPad if kind == "extrude" else factory.AddNewPocket
        result = track(create(sketch, first))
        if reverse:
            result.DirectionOrientation = 1
        set_limit(result.FirstLimit, first_limit, first, limits[0])
        set_limit(result.SecondLimit, second_limit, second, limits[1])
    else:
        create = factory.AddNewShaft if kind == "revolve" else factory.AddNewGroove
        result = track(create(sketch))
        result.RevoluteAxis = sketch_axis if local_axis else axis_ref
        result.FirstAngle.Value = first
        result.SecondAngle.Value = second
    if boolean:
        part.UpdateObject(result)
        part.InWorkObject = model.body
        combine = factory.AddNewRemove if operation == "cut" else factory.AddNewIntersect
        result = track(combine(body))
    return result


def feature_support(model, ref):
    name = ref.DisplayName
    if not name.startswith("Selection_RSur:"):
        return ref
    face = name.removeprefix("Selection_").rsplit(";", 3)[0]
    support = face + ";WithTemporaryBody;WithoutBuildError;WithSelectingFeatureSupport;MFBRepVersion_CXR15)"
    tip = model.body.Shapes.Item(model.body.Shapes.Count)
    return model.part.CreateReferenceFromBRepName(support, tip)


def edge_finish(model, track, kind, targets, first, second=None):
    refs = model.targets.resolve(targets, ("edge", "face"))
    factory = model.part.ShapeFactory
    refs = [feature_support(model, ref) for ref in refs]
    if kind == "fillet" and any(ref.DisplayName.startswith("RSur:") for ref in refs):
        empty = model.part.CreateReferenceFromName("")
        result = track(factory.AddNewSolidEdgeFilletWithConstantRadius(empty, 1, first))
        for ref in refs:
            result.AddObjectToFillet(ref)
    elif kind == "fillet":
        result = track(factory.AddNewSolidEdgeFilletWithConstantRadius(refs[0], 1, first))
        for ref in refs[1:]:
            result.AddObjectToFillet(ref)
    else:
        # A face obtained by Selection.Search is a picking reference. CATIA's
        # chamfer accepts the same Face expression as a feature support reference.
        empty = model.part.CreateReferenceFromName("")
        result = track(factory.AddNewChamfer(empty, 0, 0, 0, first, second))
        for ref in refs:
            result.AddElementToChamfer(ref)
    return result


def make_shell(model, track, targets, inside, outside):
    refs = model.targets.resolve(targets, ("face",))
    result = track(model.part.ShapeFactory.AddNewShell(refs[0], inside, outside))
    for ref in refs[1:]:
        result.AddFaceToRemove(ref)
    return result


def make_hole(model, track, face_id, center, radius, depth, limit):
    support = model.targets.one(face_id, "face")
    end = model.targets.one(limit.target_id, "face", "plane") if limit.target_id else None
    result = track(model.part.ShapeFactory.AddNewHoleFromPoint(*center, support, depth))
    result.Type = 0
    result.BottomType = 0
    result.Diameter.Value = 2 * radius
    set_limit(result.BottomLimit, limit, depth, end)
    return result


def make_draft(model, track, targets, neutral_id, angle, direction, pull_from_neutral=False):
    refs = model.targets.resolve(targets, ("face",))
    neutral = model.targets.one(neutral_id, "face")
    if direction is None:
        measurable = model.doc.GetWorkbench("SPAWorkbench").GetMeasurable(neutral)
        plane = components(model.app, measurable, "GetPlane", 9)
        direction = cross(plane[3:6], plane[6:9])
    if math.hypot(*direction) == 0:
        raise ValueError("Pull direction must be nonzero.")
    if pull_from_neutral:
        # V5 treats a neutral plane used as a direction reference differently from
        # a free vector. Resolve every support before creating the Draft feature.
        tip = model.body.Shapes.Item(model.body.Shapes.Count)
        face_refs = [feature_support(model, face) for face in refs]
        face_name = neutral.DisplayName.removeprefix("Selection_").rsplit(";", 3)[0]
        pulling_name = face_name.replace("RSur:(", "FSur:(", 1) + ";WithTemporaryBody;WithoutBuildError;WithInitialFeatureSupport;MFBRepVersion_CXR15)"
        pulling = model.part.CreateReferenceFromBRepName(pulling_name, tip)
        neutral = feature_support(model, neutral)
        empty = model.part.CreateReferenceFromName("")
        feature = track(model.part.ShapeFactory.AddNewDraft(empty, empty, 0, empty, 0, 0, 1, 0, 5.0, 0))
        domain = feature.DraftDomains.Item(1)
        for face in face_refs:
            domain.AddFaceToDraft(face)
        domain.SetPullingDirection(*direction)
        domain.PullingDirectionElement = pulling
        domain.NeutralElement = neutral
        domain.DraftAngle.Value = angle
        return feature
    result = track(
        model.part.ShapeFactory.AddNewDraft(refs[0], neutral, 0, neutral, *direction, 0, angle, 0)
    )
    domain = result.DraftDomains.Item(1)
    for ref in refs[1:]:
        domain.AddFaceToDraft(ref)
    return result
