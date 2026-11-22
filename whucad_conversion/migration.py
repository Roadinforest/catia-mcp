"""Full history -> named stateful tools, without result-path or match expressions."""
from copy import deepcopy
import numpy as np
from catia_mcp.stateful_migration import Selection, original


def selection(x):
    return Selection(select_type=x.select_type,body_type=x.body_type,body_no=int(x.body_no),no=int(x.no),
        operation_list=[selection(y) for y in x.operation_list],
        no_shared_included=[selection(y) for y in x.no_shared_included],
        all_oriented_included={k:[selection(y) for y in v] for k,v in x.all_oriented_included.items()},
        all_partially_included=None if x.all_partially_included is None else [selection(y) for y in x.all_partially_included])


def point(v):return [float(x) for x in v]


def frame(plane,origin=None):
    return dict(origin=point(plane.origin if origin is None else origin),x_direction=point(plane.x_axis),y_direction=point(plane.y_axis))


def compile_vec(vector, original_padding_compat=False):
    # -1 is padding, not Python's last-enum index. Never reinterpret missing
    # source fields as IntersectFeatureOperation or UpThruNextLimit.
    first_solid=True;normalizations=[]
    for index,row in enumerate(vector):
        command=int(row[0])
        if command in (7,9) and any(int(row[k]) not in range(6) for k in (15,16)):
            for k in (15,16):
                if int(row[k]) in range(6):continue
                if original_padding_compat and int(row[k])==-1:
                    normalizations.append(dict(row=index+1,column=k,field='limit',raw=-1,value=original().EXTENT_TYPE[-1],reason='explicit original-decoder negative-index compatibility'))
                else:raise ValueError(f"source_missing_or_invalid_limit at row {index+1}")
        if command in (7,8) and int(row[19]) not in range(3):
            if first_solid and int(row[19])==-1:
                # Original create_CAD_CATIA unconditionally creates its FIRST
                # body without applying any Boolean operation. Padding here is
                # unused context, not an instruction to intersect.
                normalizations.append(dict(row=index+1,field='first_boolean',reason='unused on first solid',value='AddFeatureOperation'))
            elif original_padding_compat and int(row[19])==-1:
                normalizations.append(dict(row=index+1,column=19,field='boolean',raw=-1,value=original().BOOLEAN_OPERATIONS[-1],reason='explicit original-decoder negative-index compatibility'))
            else:raise ValueError(f"source_missing_or_invalid_boolean at row {index+1}")
        if 7<=command<=16:first_solid=False
    seq=original().Macro_Seq.from_vector(vector,True,256)
    calls=[]
    def call(tool,**args):calls.append(dict(tool=tool,arguments=args))
    for i,op in enumerate(seq.extrude_operation):
        kind=type(op).__name__;tool=kind.lower()
        if hasattr(op,"sketch_profile"):
            profile=deepcopy(op.sketch_profile);profile.denormalize(op.sketch_size,size=256)
            for loop in profile.children:
                call("sol")
                for curve in loop.children:
                    c=type(curve).__name__
                    if c=="Circle":call("circle",center=point(curve.center),radius=float(curve.radius))
                    elif c=="Line":call("line",start=point(curve.start_point),end=point(curve.end_point))
                    elif c=="Arc":call("arc",center=point(curve.center),radius=float(curve.radius),start_angle=float(curve.start_arc),end_angle=float(curve.end_arc),mid_angle=float(curve.mid_arc))
                    elif c=="Spline":
                        call("spline",start=point(curve.point_list[0]))
                        for p in curve.point_list[1:]:call("scp",point=point(p))
                    else:raise ValueError("Unknown curve "+c)
            targets=op.select_list or []
        elif kind=="Draft":targets=[op.Neutral,*op.select_list]
        elif kind=="Hole":targets=[op.plane_ref,*(op.select_list or [])]
        else:targets=op.select_list
        if targets:
            call("topo")
            call("selection",targets=[selection(x).model_dump() for x in targets])
        if kind in ("Extrude","Pocket"):
            args=dict(frame=frame(op.sketch_plane,op.sketch_pos),first_limit=op.extent_type1,second_limit=op.extent_type2,reversed=bool(op.isInverse))
            if kind=="Extrude":args.update(length=float(op.extent_one),second_length=float(op.extent_two),operation=op.operation)
            else:args.update(depth=float(op.extent_one),second_depth=float(op.extent_two))
        elif kind in ("Revolve","Groove"):
            args=dict(frame=frame(op.sketch_plane,op.sketch_pos),angle_one=float(op.angle_one),angle_two=float(op.angle_two),reversed=bool(op.isInverse))
            if kind=="Revolve":args["operation"]=op.operation
        elif kind=="Fillet":args=dict(radius=float(op.radius))
        elif kind=="Chamfer":
            if op.mode!="catTwoLengthChamfer" or op.orientation!="catNoReverseChamfer" or op.propagation!="catTangencyChamfer":raise ValueError("Original converter ignores these chamfer modes")
            args=dict(length1=float(op.length1),length2=float(op.angle_or_length2))
        elif kind=="Shell":args=dict(inside=float(op.thickness),outside=float(op.second_thickness))
        elif kind=="Mirror":args={}
        elif kind=="Draft":args=dict(angle=float(op.DraftAngle),direction=point(op.dir))
        elif kind=="Hole":args=dict(frame=frame(op.sketch_plane),center=point(op.point_pos),radius=float(op.radius),depth=float(op.depth),bottom_mode=op.bottom_mode)
        else:raise ValueError("Unknown operation "+kind)
        if i==0 and kind in ('Extrude','Revolve'):args['operation']='AddFeatureOperation'
        call(tool,**args)
        calls[-1]["feature_index"]=i+1
    return dict(protocol="whucad-migration",version=3,calls=calls,normalizations=normalizations)
