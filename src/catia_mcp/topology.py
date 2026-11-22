"""Resolve short IDs within the current result of one CATPart main body."""

import re
from collections import defaultdict
from contextlib import contextmanager
from hashlib import sha256
from math import hypot

from .profiles import cross, new_id

PLANES = {"XY": "PlaneXY", "YZ": "PlaneYZ", "ZX": "PlaneZX"}
GEOMETRY_TYPES = {
    3: "surface",
    4: "cylinder",
    5: "sphere",
    6: "cone",
    7: "plane",
    8: "curve",
    9: "circle",
    10: "line",
}


@contextmanager
def selection_buffer(document):
    selection = document.Selection
    previous = [selection.Item2(i).Value for i in range(1, selection.Count2 + 1)]
    try:
        yield selection
    finally:
        selection.Clear()
        for item in previous:
            selection.Add(item)


def components(app, measure, method, size):
    # CATIA's output SAFEARRAY is reliably available through its VB evaluator.
    code = f"Function ReadValues(m)\nDim v({size - 1})\nm.{method} v\nReadValues=v\nEnd Function"
    return tuple(app.SystemService.Evaluate(code, 0, "ReadValues", [measure]))


def describe(app, workbench, ref, kind, *, enriched=False):
    import pythoncom

    measurable = workbench.GetMeasurable(ref)
    info = {"geometry_type": GEOMETRY_TYPES.get(int(measurable.GeometryName), "other")}
    if kind == "face":
        info["area_mm2"] = measurable.Area * 1_000_000
        info["center_mm"] = components(app, measurable, "GetCOG", 3)
    else:
        info["length_mm"] = measurable.Length
        points = components(app, measurable, "GetPointsOnCurve", 9)
        info.update(start_mm=points[:3], midpoint_mm=points[3:6], end_mm=points[6:])
    if not enriched:
        return info
    if kind == "face":
        info["center_kind"] = "area_centroid"
    try:
        if kind == "edge" and info["geometry_type"] == "circle":
            info["circle_center_mm"] = components(app, measurable, "GetCenter", 3)
            info["radius_mm"] = measurable.Radius
        elif kind == "face" and info["geometry_type"] == "plane":
            plane = components(app, measurable, "GetPlane", 9)
            normal = cross(plane[3:6], plane[6:])
            length = hypot(*normal)
            if length:
                info["normal_unit"] = [v / length for v in normal]
    except (pythoncom.com_error, AttributeError, TypeError):
        # The legacy geometry remains useful when SPA cannot give a specialised measurement.
        pass
    return info


def boundary_faces(name, kind):
    """Yield direct face descriptors, skipping nested feature history and limit clauses."""
    prefix = "Selection_RSur:(" if kind == "face" else "Selection_REdge:(Edge:("
    if not name.startswith(prefix):
        return
    start = len(prefix)
    while name.startswith("Face:(", start):
        depth = 1
        for i in range(start + len("Face:("), len(name)):
            if name[i] == "(":
                depth += 1
            elif name[i] == ")":
                depth -= 1
                if depth == 0:
                    yield name[start:i + 1]
                    start = i + 2  # Skip the semicolon preceding the next descriptor.
                    break
        else:
            return


def incidence(records):
    # CATIA includes its bounding faces' Generic Naming descriptors in an edge reference.
    # Match whole descriptors in the same result context. Ambiguous names stay unresolved.
    faces = defaultdict(list)
    links = {key: [] for key in records}
    for key, (kind, name, _) in records.items():
        if kind == "face":
            for descriptor in boundary_faces(name, kind):
                faces[name.rsplit(";", 3)[-3], descriptor].append(key)
    for key, (kind, name, _) in records.items():
        if kind == "edge":
            for descriptor in dict.fromkeys(boundary_faces(name, kind)):
                matches = faces.get((name.rsplit(";", 3)[-3], descriptor), [])
                if len(matches) == 1:
                    face = matches[0]
                    links[key].append(face)
                    links[face].append(key)
    return links


class Targets:
    def __init__(self, model):
        self.model = model
        self.revision = new_id("rev")
        self.groups = {}

    def changed(self):
        self.revision = new_id("rev")
        self.groups.clear()

    def scan(self, kinds=("face", "edge")):
        self.model.doc.Activate()
        result = {}
        with selection_buffer(self.model.doc) as selection:
            for kind in kinds:
                selection.Clear()
                selection.Add(self.model.body)
                selection.Search("Topology.CGM" + kind.title() + ",sel")
                for i in range(1, selection.Count2 + 1):
                    ref = selection.Item2(i).Reference
                    name = ref.DisplayName
                    if not name.startswith(("Selection_REdge:", "Selection_RSur:")):
                        # Body search also returns wires belonging to consumed sketches.
                        continue
                    digest = sha256((self.revision + name).encode()).hexdigest()[:20]
                    result[f"{kind}_{digest}"] = (kind, name, ref)
        return result

    def discover(self, kind, mirrored=False, include_brep=False):
        import pythoncom

        kinds = ("face", "edge") if kind == "both" else (kind,)
        candidates = []
        doc = self.model.doc
        workbench = doc.GetWorkbench("SPAWorkbench")
        records = self.scan(kinds) if mirrored else self.scan()
        links = {} if mirrored else incidence(records)
        for key, (typ, name, ref) in records.items():
            if typ not in kinds:
                continue
            if mirrored and not re.search(r"Brp:\(Mirror\.\d+", name):
                continue
            entry = {"object_id": key, "kind": typ}
            if include_brep:
                entry["brep_name"] = name
            try:
                entry.update(describe(self.model.app, workbench, ref, typ, enriched=not mirrored))
            except (pythoncom.com_error, AttributeError, TypeError):
                # Some boundary types cannot be measured by SPA; keep their identity.
                entry["brep_name"] = name
            if not mirrored:
                field = "boundary_edge_ids" if typ == "face" else "adjacent_face_ids"
                entry[field] = links[key]
            candidates.append(entry)
        group = new_id("group")
        self.groups[group] = ({row["object_id"] for row in candidates}, [])
        return {"model_id": self.model.id, "group_id": group, "candidates": candidates}

    def select(self, key, group_id):
        self.resolve([key], ("face", "edge", "plane"), expand=False)
        if group_id is not None:
            allowed, selected = self.group(group_id)
            if key not in allowed:
                raise ValueError("Object was not a candidate in this group.")
            if key not in selected:
                selected.append(key)
        return {"model_id": self.model.id, "object_id": key, "group_id": group_id}

    def group(self, key):
        if key not in self.groups:
            raise ValueError("Unknown or stale group_id. Run topo again.")
        return self.groups[key]

    def resolve(self, keys, allowed, *, expand=True):
        flat = []
        for key in keys:
            if expand and key.startswith("group_"):
                members = self.group(key)[1]
                if not members:
                    raise ValueError("Select a candidate into this group before using it.")
                flat.extend(members)
            else:
                flat.append(key)
        entries = self.scan() if any(key not in PLANES for key in flat) else {}
        refs = []
        for key in dict.fromkeys(flat):
            if key in PLANES:
                typ = "plane"
                origin = self.model.part.OriginElements
                ref = self.model.part.CreateReferenceFromObject(getattr(origin, PLANES[key]))
            elif key in entries:
                typ, _, ref = entries[key]
            else:
                raise ValueError(f"Unknown or stale object ID {key!r}. Run topo on this model.")
            if typ not in allowed:
                raise ValueError(f"Expected {'/'.join(allowed)}, got {typ} for {key}.")
            refs.append(ref)
        return refs

    def one(self, key, *kinds):
        refs = self.resolve([key], kinds)
        if len(refs) != 1:
            raise ValueError("This parameter needs exactly one object.")
        return refs[0]
