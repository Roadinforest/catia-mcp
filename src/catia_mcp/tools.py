"""The eighteen entries in WHUCAD Table 1, exposed as typed MCP tools."""

from typing import Annotated, Literal

from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field

from .modeling import edge_finish, make_draft, make_hole, make_shell, profile_feature
from .profiles import (
    Angle,
    SecondAngle,
    BooleanMode,
    Frame,
    Ids,
    Limit,
    Nonnegative,
    Point2,
    Point3,
    Positive,
    Sweep,
)

Kind = Literal["face", "edge", "both"]
DraftAngle = Annotated[float, Field(gt=-90, lt=90, allow_inf_nan=False)]
Target = str | Ids
XY_FRAME = Frame()
DISTANCE_LIMIT = Limit()


def as_list(value):
    return [value] if isinstance(value, str) else value


def initial_length(value, limit):
    if value is not None:
        return value
    if limit.mode == "distance":
        raise ValueError("A distance limit needs an explicit length or depth in mm.")
    # CATIA needs a seed dimension at creation, before assigning the geometric limit.
    return 1.0


def register(server: FastMCP):
    async def change(ctx, model_id, func):
        worker = ctx.request_context.lifespan_context
        return await worker.call(lambda session: session.change(model_id, func))

    async def sweep(ctx, kind, sketch_id, model_id, frame, mode, first, second, **options):
        curves = ctx.request_context.lifespan_context.profiles.ready(sketch_id)
        if kind in ("revolve", "groove") and not 0 < first + second <= 360:
            raise ValueError("The total revolution angle must be positive and at most 360 degrees.")
        return await change(
            ctx,
            model_id,
            lambda m, t: profile_feature(m, t, kind, curves, frame, mode, first, second, **options),
        )

    @server.tool()
    def line(ctx: Context, start: Point2, end: Point2, sketch_id: str | None = None) -> dict:
        """画直线段。起点和终点为草图局部毫米坐标。省略 sketch_id 开始新草图，继续绘制请传上一步返回的 sketch_id。"""
        return ctx.request_context.lifespan_context.profiles.append(sketch_id, "line", start, end)

    @server.tool()
    def arc(
        ctx: Context, start: Point2, end: Point2, sweep_deg: Sweep, sketch_id: str | None = None
    ) -> dict:
        """画圆弧。给局部起点、终点和非零圆心角（度），逆时针为正。返回追加该弧的新 sketch_id。"""
        return ctx.request_context.lifespan_context.profiles.append(
            sketch_id, "arc", start, end, sweep_deg
        )

    @server.tool()
    def circle(
        ctx: Context, center: Point2, radius_mm: Positive, sketch_id: str | None = None
    ) -> dict:
        """画整圆。给局部圆心与毫米半径。可在已有草图内添加独立的圆形轮廓，返回新 sketch_id。"""
        return ctx.request_context.lifespan_context.profiles.append(
            sketch_id, "circle", center, radius_mm
        )

    @server.tool()
    def spline(ctx: Context, start: Point2, sketch_id: str | None = None) -> dict:
        """在给定局部起点开始一条样条曲线。拿返回的 sketch_id 给 scp 依次补点。"""
        return ctx.request_context.lifespan_context.profiles.append(sketch_id, "spline", start)

    @server.tool()
    def scp(ctx: Context, sketch_id: str, point: Point2) -> dict:
        """给末尾的样条追加一个有序控制点。point 为局部毫米坐标，后续调用使用最新的 sketch_id。"""
        return ctx.request_context.lifespan_context.profiles.append(sketch_id, "scp", point)

    @server.tool()
    async def extrude(
        ctx: Context,
        sketch_id: str,
        length_mm: Positive | None = None,
        model_id: str | None = None,
        second_mm: Nonnegative = 0,
        operation: BooleanMode = "add",
        frame: Frame = XY_FRAME,
        first_limit: Limit = DISTANCE_LIMIT,
        second_limit: Limit = DISTANCE_LIMIT,
        reverse: bool = False,
    ) -> dict:
        """沿草图法向拉伸闭合轮廓。省略 model_id 新建零件，否则对其主实体加、减或求交。分别设置两侧限位和反向。"""
        return await sweep(
            ctx,
            "extrude",
            sketch_id,
            model_id,
            frame,
            operation,
            initial_length(length_mm, first_limit),
            second_mm,
            first_limit=first_limit,
            second_limit=second_limit,
            reverse=reverse,
        )

    @server.tool()
    async def pocket(
        ctx: Context,
        model_id: str,
        sketch_id: str,
        depth_mm: Positive | None = None,
        second_mm: Nonnegative = 0,
        frame: Frame = XY_FRAME,
        first_limit: Limit = DISTANCE_LIMIT,
        second_limit: Limit = DISTANCE_LIMIT,
        reverse: bool = False,
    ) -> dict:
        """用闭合草图在已有实体上拉伸切除。深度用毫米；默认沿草图法向的反方向切入，可反向或指定两侧限位。"""
        return await sweep(
            ctx,
            "pocket",
            sketch_id,
            model_id,
            frame,
            "add",
            initial_length(depth_mm, first_limit),
            second_mm,
            first_limit=first_limit,
            second_limit=second_limit,
            reverse=reverse,
        )

    @server.tool()
    async def revolve(
        ctx: Context,
        sketch_id: str,
        angle_deg: Angle,
        axis: str = "vertical",
        model_id: str | None = None,
        second_angle_deg: SecondAngle = 0,
        frame: Frame = XY_FRAME,
        operation: BooleanMode = "add",
    ) -> dict:
        """绕草图水平轴 horizontal、竖轴 vertical 或一条直边 ID 旋转生成实体。两侧角度总和至多 360 度。"""
        return await sweep(
            ctx,
            "revolve",
            sketch_id,
            model_id,
            frame,
            operation,
            angle_deg,
            second_angle_deg,
            axis=axis,
        )

    @server.tool()
    async def groove(
        ctx: Context,
        model_id: str,
        sketch_id: str,
        angle_deg: Angle,
        axis: str = "vertical",
        second_angle_deg: SecondAngle = 0,
        frame: Frame = XY_FRAME,
    ) -> dict:
        """绕 horizontal、vertical 或已有直边 ID，按角度旋转草图并切除材料。"""
        return await sweep(
            ctx, "groove", sketch_id, model_id, frame, "add", angle_deg, second_angle_deg, axis=axis
        )

    @server.tool()
    async def topo(ctx: Context, model_id: str | None = None, kind: Kind = "both", include_brep: bool = False) -> dict:
        """枚举当前主实体的面或边，返回 ID 及可测位置。include_brep 可附每个候选的 CATIA 来源表达式。省略 model_id 时绑定当前 CATPart。"""
        return await ctx.request_context.lifespan_context.call(lambda s: s.inspect(model_id, kind, include_brep=include_brep))

    @server.tool()
    async def mirror_topo(ctx: Context, model_id: str, kind: Kind = "both", include_brep: bool = False) -> dict:
        """枚举当前主实体中来源含 Mirror 的子对象。include_brep 可附 CATIA 来源表达式。先用 mirror 创建镜像。"""
        return await ctx.request_context.lifespan_context.call(
            lambda s: s.inspect(model_id, kind, True, include_brep)
        )

    @server.tool()
    async def selection(
        ctx: Context, model_id: str, object_id: str, group_id: str | None = None
    ) -> dict:
        """按 ID 确认一条边、一个面或 XY/YZ/ZX 基准面。可把候选加入 topo 或 mirror_topo 返回的组，供后续加工。"""
        return await ctx.request_context.lifespan_context.call(
            lambda s: s.get(model_id).targets.select(object_id, group_id)
        )

    @server.tool()
    async def fillet(ctx: Context, model_id: str, edge_id: Target, radius_mm: Positive) -> dict:
        """把指定边磨圆。edge_id 接受单个 ID、多个 ID 或已选组，亦支持面的边界。半径用毫米。"""
        return await change(
            ctx, model_id, lambda m, t: edge_finish(m, t, "fillet", as_list(edge_id), radius_mm)
        )

    @server.tool()
    async def chamfer(
        ctx: Context, model_id: str, edge_id: Target, length1_mm: Positive, length2_mm: Positive
    ) -> dict:
        """对指定边或面的边界做双长度倒角，两侧退让长度用毫米。可传单个 ID、多个 ID 或已选组。"""
        return await change(
            ctx,
            model_id,
            lambda m, t: edge_finish(m, t, "chamfer", as_list(edge_id), length1_mm, length2_mm),
        )

    @server.tool()
    async def shell(
        ctx: Context,
        model_id: str,
        opening_face_id: Target,
        inside_mm: Nonnegative,
        outside_mm: Nonnegative = 0,
    ) -> dict:
        """抽壳。移除选中的开口面（单个、多个或组），分别指定向内和向外的壁厚毫米数。"""
        if not (inside_mm or outside_mm):
            raise ValueError("At least one wall thickness must be positive.")
        return await change(
            ctx,
            model_id,
            lambda m, t: make_shell(m, t, as_list(opening_face_id), inside_mm, outside_mm),
        )

    @server.tool()
    async def hole(
        ctx: Context,
        model_id: str,
        face_id: str,
        center_mm: Point3,
        radius_mm: Positive,
        depth_mm: Positive | None = None,
        limit: Limit = DISTANCE_LIMIT,
    ) -> dict:
        """在支撑面上打直孔。给面 ID、支撑面上孔心的三维绝对坐标、半径和深度（毫米），可指定孔底限位。"""
        depth = initial_length(depth_mm, limit)
        return await change(
            ctx,
            model_id,
            lambda m, t: make_hole(m, t, face_id, center_mm, radius_mm, depth, limit),
        )

    @server.tool()
    async def mirror(ctx: Context, model_id: str, plane_id: str) -> dict:
        """以指定平面镜像当前主实体。plane_id 可为 XY、YZ、ZX 或选中的平面面 ID。"""
        return await change(
            ctx,
            model_id,
            lambda m, t: t(
                m.part.ShapeFactory.AddNewMirror(m.targets.one(plane_id, "face", "plane"))
            ),
        )

    @server.tool()
    async def draft(
        ctx: Context,
        model_id: str,
        face_id: Target,
        neutral_face_id: str,
        angle_deg: DraftAngle,
        direction: Point3 | None = None,
        pull_from_neutral: bool = False,
    ) -> dict:
        """使选定面拔模倾斜。指定中性面和角度，可给非零三维方向。pull_from_neutral 同时将中性面设为 CATIA 的拔模方向参考。"""
        if angle_deg == 0:
            raise ValueError("Draft angle must be nonzero.")
        return await change(
            ctx,
            model_id,
            lambda m, t: make_draft(m, t, as_list(face_id), neutral_face_id, angle_deg, direction, pull_from_neutral),
        )
