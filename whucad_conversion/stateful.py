"""Strict, single-sample compiler for the experimental stateful minimum chain.

No dataset walker; unsupported histories fail rather than being partially converted.
"""
import copy
from catia_mcp.stateful_tools import original, SelectSpec


def select_spec(node):
    if node.no_shared_included or node.all_oriented_included or node.all_partially_included:
        raise ValueError("Advanced selection markers are not implemented in the minimum prototype")
    return SelectSpec(select_type=node.select_type, body_type=node.body_type,
                      body_no=int(node.body_no), no=int(node.no),
                      operation_list=[select_spec(x) for x in node.operation_list])


def compile_vec(vector):
    seq = original().Macro_Seq.from_vector(vector, True, 256)
    return compile_seq(seq)


def compile_seq(seq):
    ops = seq.extrude_operation
    if len(ops) != 2 or type(ops[0]).__name__ != "Extrude" or type(ops[1]).__name__ not in ("Fillet", "Chamfer"):
        raise ValueError("Minimum compiler requires exactly Extrude -> Fillet/Chamfer")
    pad, finish = ops
    if type(finish).__name__ == "Chamfer" and (finish.mode, finish.propagation, finish.orientation) != (
            "catTwoLengthChamfer", "catTangencyChamfer", "catNoReverseChamfer"):
        raise ValueError("Unsupported chamfer mode; do not silently change its meaning")
    if pad.operation != "AddFeatureOperation" or pad.extent_type1 != "OffsetLimit" or pad.extent_type2 != "OffsetLimit":
        raise ValueError("Minimum compiler supports first additive offset pad only")
    profile = copy.deepcopy(pad.sketch_profile)
    profile.denormalize(pad.sketch_size, size=256)
    if len(profile.children) != 1 or len(profile.children[0].children) != 1:
        raise ValueError("Minimum compiler requires one circular loop")
    curve = profile.children[0].children[0]
    if type(curve).__name__ != "Circle":
        raise ValueError("Minimum compiler requires Circle")
    point = lambda x: [float(v) for v in x]
    return {"protocol": "whucad-stateful", "version": 2, "calls": [
        {"tool": "circle", "arguments": {"center": point(curve.center), "radius": float(curve.radius),
         "frame": {"origin": point(pad.sketch_pos), "x_direction": point(pad.sketch_plane.x_axis),
                   "y_direction": point(pad.sketch_plane.y_axis)}}},
        {"tool": "extrude", "arguments": {"length": float(pad.extent_one),
         "second_length": float(pad.extent_two), "reversed": bool(pad.isInverse)}},
        {"tool": "selection", "arguments": {"targets": [select_spec(x).model_dump() for x in finish.select_list]}},
        ({"tool": "fillet", "arguments": {"radius": float(finish.radius)}}
         if type(finish).__name__ == "Fillet" else
         {"tool": "chamfer", "arguments": {"length1": float(finish.length1), "length2": float(finish.angle_or_length2)}})]}


def arguments(call, model_id):
    """Inject the session binding, never resolve result-path expressions."""
    def check(value):
        if isinstance(value, dict):
            if any(k in value for k in ("$ref", "$match", "model_id")):
                raise ValueError("Stateful programs cannot contain ref/match/model_id bindings")
            for x in value.values():
                check(x)
        elif isinstance(value, list):
            for x in value:
                check(x)
    check(call["arguments"])
    return {"model_id": model_id, **call["arguments"]}
