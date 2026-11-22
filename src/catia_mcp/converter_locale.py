"""Language-neutral axis access for the original Windows converter.

No original file is edited. Only the three documented Axis2D access expressions
are replaced in copies of the two sketch emitters. Geometry code stays identical.
"""
from functools import lru_cache
import inspect
from types import SimpleNamespace, FunctionType
from .stateful_tools import original


@lru_cache(maxsize=1)
def compatible_converter():
    source=original();namespace=dict(vars(source))
    replacements={
        'geometricElements1.Item("AbsoluteAxis")':'geometricElements1.Item(1)',
        'axis2D1.GetItem("HDirection")':'axis2D1.HorizontalReference',
        'axis2D1.GetItem("VDirection")':'axis2D1.VerticalReference',
    }
    for name in ('create_Body_CATIA','create_pocket_CATIA'):
        text=inspect.getsource(getattr(source,name))
        for old,new in replacements.items():
            if text.count(old)!=1:raise RuntimeError('Unexpected converter version for locale adapter: '+old)
            text=text.replace(old,new)
        exec(compile(text,source.__file__+' [Axis2D locale adapter]','exec'),namespace)
    # Keep a shared namespace so a verifier can observe feature boundaries.
    main=source.create_CAD_CATIA
    namespace['create_CAD_CATIA']=FunctionType(main.__code__,namespace,main.__name__,main.__defaults__)
    result=SimpleNamespace(**namespace)
    result.emitter_namespace=namespace
    return result
