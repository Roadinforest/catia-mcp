"""Explicit extensions where the original converter ignores paper parameters.

These branches require independent geometry tests, not original-output equality.
"""
from functools import lru_cache
import inspect
from types import SimpleNamespace
from .stateful_tools import original
from .converter_locale import compatible_converter


def sketch_support(part,op,context,offset,holes,fallback):
    target=getattr(op,'support_target',None)
    if target is None:return fallback
    if target.body_type=='OriginElements':
        return part.CreateReferenceFromObject([part.OriginElements.PlaneXY,part.OriginElements.PlaneYZ,part.OriginElements.PlaneZX][target.no-1])
    name=original().parse_BrepName(target,True,sketch_offset=offset,hole_sketch_map=holes)
    return part.CreateReferenceFromBRepName('RSur:('+name+';WithTemporaryBody;WithoutBuildError;WithSelectingFeatureSupport;MFBRepVersion_CXR15)',context)


def second_limit(part,feature,op,context,offset,holes):
    mode=original().EXTENT_TYPE.index(op.extent_type2)
    feature.SecondLimit.LimitMode=mode
    if mode in (3,4):
        index=int(op.extent_type1 in ('UpToPlaneLimit','UpToSurfaceLimit'))
        if len(op.select_list)<=index:raise ValueError('Select second-side limiting face after first-side face')
        target=op.select_list[index]
        if target.body_type=='OriginElements':
            face=[part.OriginElements.PlaneXY,part.OriginElements.PlaneYZ,part.OriginElements.PlaneZX][target.no-1]
            reference=part.CreateReferenceFromObject(face)
        else:
            name=original().parse_BrepName(target,True,sketch_offset=offset,hole_sketch_map=holes)
            # A compound planar face resolves as RSur; FSur may not accept its
            # multi-source generic naming expression as a limiting element.
            prefix='FSur' if mode==3 and target.select_type!='Multiply_Face' else 'RSur'
            reference=part.CreateReferenceFromBRepName(prefix+':('+name+';WithTemporaryBody;WithoutBuildError;WithSelectingFeatureSupport;MFBRepVersion_CXR15)',context)
        feature.SecondLimit.LimitingElement=reference


def hole_limit(part,hole,op,context,offset,holes):
    mode=original().EXTENT_TYPE.index(op.bottom_mode)
    hole.BottomLimit.LimitMode=mode
    if mode:
        # Installed CATIA type library: catTrimmedHoleBottom == 2. A surface
        # termination must follow the surface, not a flat tangent-depth bottom.
        hole.BottomType=2
    if mode in (3,4):
        if not op.select_list:raise ValueError('Select a limiting face after hole support')
        target=op.select_list[0]
        if target.body_type=='OriginElements':
            face=[part.OriginElements.PlaneXY,part.OriginElements.PlaneYZ,part.OriginElements.PlaneZX][target.no-1]
            reference=part.CreateReferenceFromObject(face)
        else:
            # The limiting face existed BEFORE this hole's three internal sketches.
            name=original().parse_BrepName(target,True,sketch_offset=offset-3,hole_sketch_map=holes)
            prefix='FSur' if mode==3 else 'RSur'
            reference=part.CreateReferenceFromBRepName(prefix+':('+name+';WithTemporaryBody;WithoutBuildError;WithSelectingFeatureSupport;MFBRepVersion_CXR15)',context)
        hole.BottomLimit.LimitingElement=reference


@lru_cache(maxsize=1)
def extended_converter():
    base=compatible_converter();namespace=dict(base.emitter_namespace)
    namespace.update(_apply_second_limit=second_limit,_apply_hole_limit=hole_limit,_sketch_support=sketch_support)
    # Start from the original text and apply exactly the same locale changes.
    for name in ('create_Body_CATIA','create_pocket_CATIA'):
        text=inspect.getsource(getattr(original(),name))
        text=text.replace('body = part.bodies.Item(1)','body = part.MainBody')
        if name=='create_pocket_CATIA':
            text=text.replace('sketch = body.sketches.add(reference5)',
                'sketch = body.sketches.add(_sketch_support(part, extrude_op, cur_body_ref, sketch_offset, hole_sketch_map, reference5))')
        for old,new in [('geometricElements1.Item("AbsoluteAxis")','geometricElements1.Item(1)'),
                        ('axis2D1.GetItem("HDirection")','axis2D1.HorizontalReference'),
                        ('axis2D1.GetItem("VDirection")','axis2D1.VerticalReference')]:
            text=text.replace(old,new)
        old='length.Value = extrude_op.extent_two'
        assert text.count(old)==1
        text=text.replace(old,old+'\n        _apply_second_limit(part, pad, extrude_op, cur_body_ref, sketch_offset, hole_sketch_map)')
        exec(compile(text,original().__file__+' [second-limit semantic extension]','exec'),namespace)
    text=inspect.getsource(original().create_Feature_on_select)
    text=text.replace('parse_BrepName(op.plane_ref, True, sketch_offset=sketch_offset, hole_sketch_map=hole_sketch_map)',
                      'parse_BrepName(op.plane_ref, True, sketch_offset=sketch_offset-3, hole_sketch_map=hole_sketch_map)')
    old='limit2.LimitMode = 0 # catOffsetLimit'
    assert text.count(old)==1
    text=text.replace(old,'_apply_hole_limit(part, hole1, op, cur_body_ref, sketch_offset, hole_sketch_map)')
    text=text.replace('hole_sketch_map[hole1.name] = body.sketches.Count',
        'hole_sketch_map["Hole." + hole1.Name.rsplit(".", 1)[1]] = int(body.sketches.Item(body.sketches.Count).Name.rsplit(".", 1)[1])')
    exec(compile(text,original().__file__+' [hole-limit semantic extension]','exec'),namespace)
    return SimpleNamespace(**namespace)
