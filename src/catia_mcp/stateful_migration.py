"""Staged full-command adapter. Not promoted until branch/geometry gates pass.

Uses the original low-level CATIA emitters deliberately: comparison tests the
MCP serialization, context, curve grouping and history, not an independent kernel.
"""
from dataclasses import dataclass, field
from copy import deepcopy
from types import SimpleNamespace
from typing import Literal
import contextlib
import sys

import numpy as np
from mcp.server.fastmcp import Context
from pydantic import Field, FiniteFloat
from .profiles import Input, Point2, Point3, Positive, cross
from .stateful_tools import SketchFrame, original
from .converter_locale import compatible_converter
from .converter_semantics import extended_converter


class Selection(Input):
    select_type: Literal["Wire", "Face", "Edge", "Multiply_Face", "Sub_Face"]
    body_type: Literal["None", "OriginElements", "Sketch", "Pad", "Shaft", "Pocket", "Add", "Remove", "Intersect", "Shell", "Chamfer", "EdgeFillet", "Mirror", "Hole"] = "None"
    body_no: int = Field(default=0, ge=0)
    no: int = Field(default=0, ge=0)
    operation_list: list["Selection"] = Field(default_factory=list)
    no_shared_included: list["Selection"] = Field(default_factory=list)
    all_oriented_included: dict[Literal["Limits1", "Limits2"], list["Selection"]] = Field(default_factory=dict)
    all_partially_included: list["Selection"] | None = None

    def native(self):
        return original().Select(self.select_type,self.body_type,self.body_no,self.no,
            [x.native() for x in self.operation_list], [x.native() for x in self.no_shared_included],
            {k:[x.native() for x in v] for k,v in self.all_oriented_included.items()},
            None if self.all_partially_included is None else [x.native() for x in self.all_partially_included])


Limit = Literal["OffsetLimit", "UpToNextLimit", "UpToLastLimit", "UpToPlaneLimit", "UpToSurfaceLimit", "UpThruNextLimit"]
Boolean = Literal["AddFeatureOperation", "CutFeatureOperation", "IntersectFeatureOperation"]


class PhysicalProfile:
    """Profile already denormalized by the compiler or supplied in mm by a caller."""
    def __init__(self, loops):
        self.children = [SimpleNamespace(children=deepcopy(loop)) for loop in loops]

    def denormalize(self, *args, **kwargs):
        return self


@dataclass
class History:
    current: object = None
    loops: list = field(default_factory=list)
    selected: list = field(default_factory=list)
    revision: int = 0
    sketch_offset: int = 0
    holes: dict = field(default_factory=dict)
    poisoned: bool = False
    aliases: dict = field(default_factory=dict)
    logical_counts: dict = field(default_factory=dict)
    hole_replacements: dict = field(default_factory=dict)
    profile_count: int = 0
    sketch_bindings: dict = field(default_factory=dict)


def history(m):
    if not hasattr(m, "migration_history"):
        if m.part.Bodies.Count != 1 or m.body.Shapes.Count or m.body.Sketches.Count:
            raise ValueError("Bind a blank CATPart; existing history cannot be inferred")
        m.migration_history = History()
    h = m.migration_history
    if h.poisoned:
        raise RuntimeError("Previous rollback failed: rebind a new blank document")
    return h


def status(m):
    h=history(m)
    return dict(model_id=m.id, revision=h.revision,
                current_feature=None if h.current is None else h.current.Name,
                pending_loops=len(h.loops), selected_count=len(h.selected))


def append(m, curve):
    h=history(m)
    if not h.loops:
        h.loops.append([])
    h.loops[-1].append(curve)
    return status(m)


def plane(frame):
    return SimpleNamespace(origin=np.asarray(frame.origin), x_axis=np.asarray(frame.x_direction),
                           y_axis=np.asarray(frame.y_direction), normal=np.asarray(cross(frame.x_direction,frame.y_direction)))


def select(m, targets):
    h=history(m); h.selected=[]
    natives=[x.native() for x in targets]
    def translate(node):
        key=(node.body_type,node.body_no)
        replacement=h.hole_replacements.get(node.body_no) if node.body_type=='Hole' else None
        if replacement:
            if node.no not in (1,3):
                raise ValueError('Surface-limited hole selection supports bottom (1) and wall (3)')
            wall=node.no==3
            node.body_type='Pocket';node.body_no=replacement['pocket']
            node.no=0 if wall else 2
            node.operation_list=([original().Select('Wire','Sketch',replacement['sketch']-h.sketch_offset,1,[],[],{},None)] if wall else [])
            if wall:node.operation_list[0]._resolved_sketch=True
        elif key in h.aliases:node.body_type,node.body_no=h.aliases[key]
        if node.body_type=='Sketch' and not getattr(node,'_resolved_sketch',False) and node.body_no in h.sketch_bindings:
            # Offsets belong to the time a sketch was created, not the time it
            # is selected. Compensate for the original parser's global offset.
            node.body_no=h.sketch_bindings[node.body_no]-h.sketch_offset
        children=[*node.operation_list,*node.no_shared_included,*(node.all_partially_included or [])]
        for values in node.all_oriented_included.values():children.extend(values)
        for child in children:translate(child)
    for node in natives:translate(node)
    labels=[]
    for x in natives:
        if x.body_type == "OriginElements":
            if x.no not in (1,2,3): raise ValueError("Origin element must be 1, 2 or 3")
            labels.append("OriginElements:"+str(x.no))
        else:
            label=original().parse_BrepName(x,True,sketch_offset=h.sketch_offset,hole_sketch_map=h.holes)
            if h.current is not None and not h.loops and x.select_type in ('Face','Edge'):
                prefix='RSur' if x.select_type=='Face' else 'REdge'
                ref=m.part.CreateReferenceFromBRepName(prefix+':('+label+';WithTemporaryBody;WithoutBuildError;WithSelectingFeatureSupport;MFBRepVersion_CXR15)',h.current)
                # SPA returns Unknown for valid Small Scale boundaries in V5R21.
                # Resolve with the shape factory instead; do not accept a merely
                # constructed Reference, nor a reference of the wrong dimension.
                geometry=m.part.HybridShapeFactory.GetGeometricalFeatureType(ref)
                expected=(5,6) if x.select_type=='Face' else (2,3,4)
                if geometry not in expected:
                    raise ValueError('Selection does not resolve to the requested geometry type: '+label)
            labels.append(label)
    # Reference contexts can be the next sketch (revolve) or offset-adjusted
    # hole support. Resolve those when that context exists, as the converter does.
    h.selected=natives
    return {**status(m),"descriptions":labels}


def execute(m, op):
    h=history(m); source=extended_converter(); p=m.part
    kind=type(op).__name__
    if h.current is None and kind not in ("Extrude","Revolve"):
        raise ValueError("First solid must be Extrude or Revolve")
    m.doc.Activate()
    old_current=p.InWorkObject
    body_count=p.Bodies.Count
    # Capture all collection lengths before any COM changes, including sketches
    # and planes created before a feature's Update may fail.
    collections=[]
    for i in range(1,body_count+1):
        b=p.Bodies.Item(i)
        for collection in (b.Shapes,b.Sketches,b.HybridShapes):
            collections.append((collection,collection.Count))
    offset=h.sketch_offset; holes=deepcopy(h.holes); profile_tip=None; physical_type=None; actual_boolean_type=None; hole_replacement=None
    phase='start'
    try:
        p.InWorkObject=m.body
        with contextlib.redirect_stdout(sys.stderr):
            if kind in ("Extrude","Revolve"):
                auto = kind=="Extrude" and h.current is not None and any(
                    x in ('UpToNextLimit','UpToLastLimit','UpThruNextLimit') for x in (op.extent_type1,op.extent_type2))
                if auto and op.operation=='CutFeatureOperation':
                    cut=source.Pocket.__new__(source.Pocket);cut.__dict__.update(deepcopy(vars(op)))
                    cut.isInverse=not op.isInverse;cut.limit_mode=0
                    tip=source.create_pocket_CATIA(p,cut,cur_body_ref=h.current,sketch_offset=offset,hole_sketch_map=holes)
                    physical_type='Pocket'
                elif auto and op.operation=='IntersectFeatureOperation':
                    # A intersect T = A minus (A minus T). Keep a linked copy
                    # of the pre-cut feature, then cut in A where automatic
                    # limits have real context. Preserve a feature history.
                    phase='linked pre-cut extract';base_body=m.body
                    copy_body=p.Bodies.Add();p.InWorkObject=copy_body
                    # Extract the exact historical feature, NOT a clipboard body
                    # result which follows the body's later tip and creates a cycle.
                    extract=p.HybridShapeFactory.AddNewExtract(p.CreateReferenceFromObject(h.current))
                    copy_body.InsertHybridShape(extract);p.Update()
                    p.ShapeFactory.AddNewCloseSurface(p.CreateReferenceFromObject(extract));p.Update()
                    phase='context pocket';p.InWorkObject=base_body
                    cut=source.Pocket.__new__(source.Pocket);cut.__dict__.update(deepcopy(vars(op)))
                    cut.isInverse=not op.isInverse;cut.limit_mode=0
                    profile_tip=source.create_pocket_CATIA(p,cut,cur_body_ref=h.current,sketch_offset=offset,hole_sketch_map=holes)
                    physical_type='Pocket';phase='difference of linked bodies';p.InWorkObject=copy_body
                    tip=p.ShapeFactory.AddNewRemove(base_body);p.MainBody=copy_body;p.Update()
                    actual_boolean_type='Remove'
                else:
                    tip=source.create_Body_CATIA(p,op,cur_body_ref=h.current,is_first=h.current is None or auto,
                                                sketch_offset=offset,hole_sketch_map=holes)
                    physical_type='Pad' if kind=='Extrude' else 'Shaft'
                if profile_tip is None:profile_tip=tip
                if h.current is not None and not auto:
                    body=p.Bodies.Item(p.Bodies.Count); p.InWorkObject=p.Bodies.Item(1)
                    factory=p.ShapeFactory
                    tip={"AddFeatureOperation":factory.AddNewAdd,"CutFeatureOperation":factory.AddNewRemove,
                         "IntersectFeatureOperation":factory.AddNewIntersect}[op.operation](body)
                    p.Update()
            elif kind=='Hole' and op.bottom_mode=='UpToSurfaceLimit':
                # Native Hole's trimmed bottom produces an imprecise boundary
                # in STEP for curved limits. A circular Pocket preserves the
                # requested simple-hole geometry and explicit surface limit.
                if not op.select_list:raise ValueError('Select a limiting face after hole support')
                cut=source.Pocket.__new__(source.Pocket)
                cut.__dict__.update(extent_one=op.depth,extent_two=0,isSymmetric=False,isInverse=False,
                    extent_type1=op.bottom_mode,extent_type2='OffsetLimit',limit_mode=0,
                    sketch_name='',sketch_plane=op.sketch_plane,sketch_pos=op.sketch_plane.origin,
                    sketch_size=1.0,sketch_profile=PhysicalProfile([[source.Circle(op.point_pos,op.radius)]]),
                    select_list=deepcopy(op.select_list),support_target=deepcopy(op.plane_ref))
                tip=source.create_pocket_CATIA(p,cut,cur_body_ref=h.current,sketch_offset=offset,hole_sketch_map=holes)
                actual_sketch=m.body.Sketches.Item(m.body.Sketches.Count)
                hole_replacement=dict(pocket=int(tip.Name.rsplit('.',1)[1]),sketch=int(actual_sketch.Name.rsplit('.',1)[1]))
                offset+=1
            elif kind in ("Pocket","Groove"):
                tip=source.create_pocket_CATIA(p,op,cur_body_ref=h.current,sketch_offset=offset,hole_sketch_map=holes)
            else:
                tip,offset=source.create_Feature_on_select(m.doc,p,op,h.current,m.app,offset,m.body,holes)
        p.Update()
    except Exception as exc:
        h.selected=[]
        try:
            selection=m.doc.Selection; selection.Clear()
            for collection,count in collections:
                for i in range(count+1,collection.Count+1): selection.Add(collection.Item(i))
            for i in range(body_count+1,p.Bodies.Count+1):selection.Add(p.Bodies.Item(i))
            if selection.Count:selection.Delete()
            selection.Clear();p.InWorkObject=old_current;p.Update()
        except Exception:
            h.poisoned=True
        raise RuntimeError(f'{kind} failed at {phase}: {exc}; rollback_poisoned={h.poisoned}') from exc
    def remember(logical,physical,obj):
        number=h.logical_counts.get(logical,0)+1;h.logical_counts[logical]=number
        h.aliases[(logical,number)]=(physical,int(obj.Name.rsplit('.',1)[1]))
    if kind in ('Extrude','Revolve'):
        remember('Pad' if kind=='Extrude' else 'Shaft',physical_type,profile_tip)
        if h.current is not None:
            logical={'AddFeatureOperation':'Add','CutFeatureOperation':'Remove','IntersectFeatureOperation':'Intersect'}[op.operation]
            remember(logical,actual_boolean_type or (physical_type if auto else logical),tip)
    else:
        logical={'Fillet':'EdgeFillet'}.get(kind,kind)
        remember(logical,'Pocket' if hole_replacement else logical,tip)
        if hole_replacement:h.hole_replacements[h.logical_counts['Hole']]=hole_replacement
    if kind in ('Extrude','Revolve','Pocket','Groove'):
        h.profile_count+=1;h.sketch_bindings[h.profile_count]=h.profile_count+h.sketch_offset
    h.current=tip;h.sketch_offset=offset;h.holes=holes
    h.loops=[];h.selected=[];h.revision+=1;m.targets.changed()
    return status(m)


def profile_operation(m, kind, frame, values):
    h=history(m)
    if not h.loops or any(not loop for loop in h.loops):raise ValueError("Complete a sketch first")
    for loop in h.loops:
        for curve in loop:
            if isinstance(curve,original().Spline) and len(curve.point_list)<2:raise ValueError("Spline needs at least two points")
    op=getattr(original(),kind).__new__(getattr(original(),kind))
    op.__dict__.update(values,sketch_name="",sketch_plane=plane(frame),sketch_pos=np.asarray(frame.origin),
                       sketch_size=1.0,sketch_profile=PhysicalProfile(h.loops),select_list=deepcopy(h.selected))
    if kind in ("Revolve","Groove") and not h.selected:raise ValueError("Select the rotation axis first")
    if kind in ("Extrude","Pocket"):
        if op.extent_type1 in ("UpToPlaneLimit","UpToSurfaceLimit") and not h.selected:raise ValueError("Select a limiting face first")
    return execute(m,op)


def finish(m, kind, values):
    h=history(m)
    if h.loops:raise ValueError("Complete the pending sketch operation before finishing")
    if not h.selected:raise ValueError("Select targets before this operation")
    op=getattr(original(),kind).__new__(getattr(original(),kind))
    op.__dict__.update(values,select_list=deepcopy(h.selected))
    if kind=="Draft":
        if len(h.selected)<2:raise ValueError("Select neutral face first, then draft faces")
        op.Neutral=op.select_list.pop(0)
    if kind=="Hole":
        op.plane_ref=op.select_list.pop(0)
    return execute(m,op)


def register(server):
    source=original()
    async def call(ctx,model_id,fn):
        return await ctx.request_context.lifespan_context.call(lambda s:fn(s.get(model_id)))

    @server.tool()
    async def model_state(ctx:Context,model_id:str|None=None)->dict:
        """Bind active blank CATPart, or inspect a previously bound model."""
        return await ctx.request_context.lifespan_context.call(lambda s:status(s.bind(s.app.ActiveDocument) if model_id is None else s.get(model_id)))

    @server.tool()
    async def sol(ctx:Context,model_id:str)->dict:
        """Start the next ordered sketch loop. Curves use local millimetres."""
        def start(m):
            h=history(m)
            if h.loops and not h.loops[-1]:raise ValueError("Previous loop is empty")
            h.loops.append([]);return status(m)
        return await call(ctx,model_id,start)

    @server.tool()
    async def line(ctx:Context,model_id:str,start:Point2,end:Point2)->dict:
        """Append a line to the current loop; no sketch ID is needed."""
        return await call(ctx,model_id,lambda m:append(m,source.Line(np.asarray(start),np.asarray(end))))

    @server.tool()
    async def arc(ctx:Context,model_id:str,center:Point2,radius:Positive,start_angle:FiniteFloat,end_angle:FiniteFloat,mid_angle:FiniteFloat)->dict:
        """Append decoded WHUCAD arc geometry. Angles are radians; midpoint identifies the intended arc."""
        return await call(ctx,model_id,lambda m:append(m,source.Arc(np.asarray(center),radius,start_angle,end_angle,mid_angle)))

    @server.tool()
    async def circle(ctx:Context,model_id:str,center:Point2,radius:Positive)->dict:
        """Append a circle in local sketch millimetres; frame belongs to the later solid operation."""
        return await call(ctx,model_id,lambda m:append(m,source.Circle(np.asarray(center),radius)))

    @server.tool()
    async def spline(ctx:Context,model_id:str,start:Point2)->dict:
        """Begin a spline; follow with ordered scp calls."""
        return await call(ctx,model_id,lambda m:append(m,source.Spline([np.asarray(start)])))

    @server.tool()
    async def scp(ctx:Context,model_id:str,point:Point2)->dict:
        """Append one control point to the current spline."""
        def add(m):
            h=history(m)
            if not h.loops or not h.loops[-1] or not isinstance(h.loops[-1][-1],source.Spline):raise ValueError("scp requires an active spline")
            points=list(h.loops[-1][-1].point_list)+[np.asarray(point)]
            h.loops[-1][-1]=source.Spline(points);return status(m)
        return await call(ctx,model_id,add)

    @server.tool()
    async def topo(ctx:Context,model_id:str)->dict:
        """Begin a selection declaration (WHUCAD marker). Clear pending targets; does not query geometry."""
        return await call(ctx,model_id,lambda m:select(m,[]))

    @server.tool()
    async def mirror_topo(ctx:Context,model_id:str)->dict:
        """Begin a mirror selection declaration; mirror provenance is explicit in the following selection tree."""
        return await call(ctx,model_id,lambda m:select(m,[]))

    @server.tool()
    async def selection(ctx:Context,model_id:str,targets:list[Selection])->dict:
        """Parse ordered WHUCAD target trees including advanced selection markers. Replaces current selection."""
        return await call(ctx,model_id,lambda m:select(m,targets))

    @server.tool()
    async def extrude(ctx:Context,model_id:str,length:FiniteFloat,frame:SketchFrame=SketchFrame(),second_length:FiniteFloat=0,
                      operation:Boolean="AddFeatureOperation",first_limit:Limit="OffsetLimit",second_limit:Limit="OffsetLimit",reversed:bool=False)->dict:
        """Extrude pending sketch; later extrusions use original separate-body Boolean semantics."""
        return await call(ctx,model_id,lambda m:profile_operation(m,"Extrude",frame,dict(extent_one=length,extent_two=second_length,
            isSymmetric=False,isInverse=reversed,operation=operation,extent_type1=first_limit,extent_type2=second_limit)))

    @server.tool()
    async def pocket(ctx:Context,model_id:str,depth:FiniteFloat,frame:SketchFrame=SketchFrame(),second_depth:FiniteFloat=0,
                     first_limit:Limit="OffsetLimit",second_limit:Limit="OffsetLimit",reversed:bool=False)->dict:
        """Cut the current body using the pending sketch and stored limiting face, if needed."""
        return await call(ctx,model_id,lambda m:profile_operation(m,"Pocket",frame,dict(extent_one=depth,extent_two=second_depth,
            isSymmetric=False,isInverse=reversed,extent_type1=first_limit,extent_type2=second_limit,limit_mode=0)))

    @server.tool()
    async def revolve(ctx:Context,model_id:str,angle_one:FiniteFloat,angle_two:FiniteFloat,frame:SketchFrame=SketchFrame(),operation:Boolean="AddFeatureOperation",reversed:Literal[False]=False)->dict:
        """Revolve around stored axis. Angles use original decoded WHUCAD degrees, including its second-angle convention."""
        return await call(ctx,model_id,lambda m:profile_operation(m,"Revolve",frame,dict(angle_one=angle_one,angle_two=angle_two,isInverse=reversed,operation=operation)))

    @server.tool()
    async def groove(ctx:Context,model_id:str,angle_one:FiniteFloat,angle_two:FiniteFloat,frame:SketchFrame=SketchFrame(),reversed:Literal[False]=False)->dict:
        """Rotational cut using original decoded angle convention and stored axis."""
        return await call(ctx,model_id,lambda m:profile_operation(m,"Groove",frame,dict(angle_one=angle_one,angle_two=angle_two,isInverse=reversed)))

    @server.tool()
    async def fillet(ctx:Context,model_id:str,radius:Positive)->dict:
        """Fillet the stored selection, following original tangency propagation."""
        return await call(ctx,model_id,lambda m:finish(m,"Fillet",dict(radius=radius,edgePropagation=1)))

    @server.tool()
    async def chamfer(ctx:Context,model_id:str,length1:Positive,length2:Positive)->dict:
        """Original two-length chamfer, tangency propagation, no reverse."""
        return await call(ctx,model_id,lambda m:finish(m,"Chamfer",dict(length1=length1,angle_or_length2=length2,mode="catTwoLengthChamfer",propagation="catTangencyChamfer",orientation="catNoReverseChamfer")))

    @server.tool()
    async def shell(ctx:Context,model_id:str,inside:FiniteFloat,outside:FiniteFloat=0)->dict:
        """Shell the body, removing the stored faces; lengths in mm."""
        return await call(ctx,model_id,lambda m:finish(m,"Shell",dict(thickness=inside,second_thickness=outside)))

    @server.tool()
    async def mirror(ctx:Context,model_id:str)->dict:
        """Mirror using the selected reference plane."""
        return await call(ctx,model_id,lambda m:finish(m,"Mirror",{}))

    @server.tool()
    async def draft(ctx:Context,model_id:str,angle:FiniteFloat,direction:Point3=(0,0,1))->dict:
        """Select neutral face then target faces. Original converter derives final pulling direction from neutral face."""
        return await call(ctx,model_id,lambda m:finish(m,"Draft",dict(DraftAngle=angle,dir=np.asarray(direction),Parting=None,NeutralMode=0,Mode=0,MultiselectionMode=0)))

    @server.tool()
    async def hole(ctx:Context,model_id:str,center:Point2,radius:Positive,depth:Positive,frame:SketchFrame=SketchFrame(),bottom_mode:Limit="OffsetLimit")->dict:
        """Local hole center in given frame; select support first, then limiting face for plane/surface mode."""
        return await call(ctx,model_id,lambda m:finish(m,"Hole",dict(point_pos=np.asarray(center),radius=radius,depth=depth,bottom_mode=bottom_mode,sketch_plane=plane(frame))))
