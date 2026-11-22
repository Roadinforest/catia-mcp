"""Tool-facing dimensions and immutable sketch profiles, expressed in millimetres."""

import math
from threading import Lock
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

Point2 = tuple[FiniteFloat, FiniteFloat]
Point3 = tuple[FiniteFloat, FiniteFloat, FiniteFloat]
Positive = Annotated[FiniteFloat, Field(gt=0)]
Nonnegative = Annotated[FiniteFloat, Field(ge=0)]
Angle = Annotated[FiniteFloat, Field(ge=0, le=360)]
SecondAngle = Annotated[FiniteFloat, Field(ge=-360, le=360)]
Sweep = Annotated[FiniteFloat, Field(gt=-360, lt=360)]
Ids = Annotated[list[str], Field(min_length=1)]
BooleanMode = Literal["add", "cut", "intersect"]


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Frame(Input):
    """Absolute origin (mm) and perpendicular unit directions of sketch X and Y."""

    origin: Point3 = (0, 0, 0)
    x_direction: Point3 = (1, 0, 0)
    y_direction: Point3 = (0, 1, 0)
    support_id: str | None = Field(
        default=None,
        description="Optional supporting planar face or XY/YZ/ZX. Supply its matching origin and axes.",
    )

    @model_validator(mode="after")
    def orthonormal(self):
        x, y = self.x_direction, self.y_direction
        if (
            any(abs(math.hypot(*v) - 1) > 1e-7 for v in (x, y))
            or abs(sum(a * b for a, b in zip(x, y))) > 1e-7
        ):
            raise ValueError("Sketch axes must be perpendicular unit vectors.")
        return self


class Limit(Input):
    """CATIA termination. A plane/surface limit needs a face ID or base plane."""

    mode: Literal["distance", "next", "last", "plane", "surface", "through_next"] = "distance"
    target_id: str | None = None

    @model_validator(mode="after")
    def requires_target(self):
        if bool(self.target_id) != (self.mode in ("plane", "surface")):
            raise ValueError("Supply target_id exactly for plane and surface limits.")
        return self


def new_id(kind):
    return f"{kind}_{uuid4().hex}"


class Profiles:
    def __init__(self):
        self.entries = {}
        self.lock = Lock()

    def read(self, sketch_id):
        try:
            return self.entries[sketch_id]
        except KeyError:
            raise ValueError("Unknown sketch_id. Use the ID returned by a sketch tool.") from None

    def append(self, sketch_id, kind, *args):
        with self.lock:
            curves = self.read(sketch_id) if sketch_id is not None else ()
            if kind == "scp":
                if not curves or curves[-1][0] != "spline":
                    raise ValueError(
                        "scp needs the sketch_id returned by spline or its latest scp."
                    )
                points = curves[-1][1:]
                if args[0] == points[-1]:
                    raise ValueError("Consecutive spline points must differ.")
                curves = curves[:-1] + (("spline", *points, *args),)
            else:
                if curves and curves[-1][0] == "spline" and len(curves[-1]) < 3:
                    raise ValueError("Add a spline point with scp first.")
                if kind in ("line", "arc") and args[0] == args[1]:
                    raise ValueError("Start and end must differ.")
                if kind == "arc" and args[2] == 0:
                    raise ValueError("Arc sweep must be nonzero.")
                curves += ((kind, *args),)
            key = new_id("sketch")
            self.entries[key] = curves
            return {"sketch_id": key, "curve_count": len(curves)}

    def ready(self, sketch_id):
        curves = self.read(sketch_id)
        if any(row[0] == "spline" and len(row) < 3 for row in curves):
            raise ValueError("Add another point with scp before creating a solid.")
        return curves


def arc_geometry(start, end, sweep):
    """Translate directed endpoint and sweep into CATIA's positive arc limits."""
    x, y = start
    dx, dy = end[0] - x, end[1] - y
    offset = 0.5 / math.tan(math.radians(sweep) / 2)
    cx, cy = x + dx / 2 - dy * offset, y + dy / 2 + dx * offset
    first = math.atan2(y - cy, x - cx)
    last = first + math.radians(sweep)
    return cx, cy, math.hypot(x - cx, y - cy), min(first, last), max(first, last)
