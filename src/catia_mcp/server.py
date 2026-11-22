import json
from contextlib import asynccontextmanager

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import CallToolResult, TextContent
from pydantic import PositiveInt

from .connection import CadWorker
from .scripts import execute_script
from .tools import register


@asynccontextmanager
async def lifespan(server: FastMCP):
    worker = CadWorker()
    try:
        yield worker
    finally:
        await worker.close()


def create_server(*, port: int = 8000, with_script: bool = False, protocol: str = "legacy") -> FastMCP:
    server = FastMCP(
        "catia-mcp",
        host="127.0.0.1",
        port=port,
        json_response=True,
        lifespan=lifespan,
    )
    if protocol == "whucad-migration":
        from .stateful_migration import register as register_migration
        register_migration(server)
    elif protocol == "whucad-stateful":
        from .stateful_tools import register as register_stateful
        register_stateful(server)
    elif protocol == "legacy":
        register(server)
    else:
        raise ValueError(f"Unknown protocol: {protocol}")
    if with_script:
        if protocol != "legacy":
            raise ValueError("Stateful prototype does not allow scripts that bypass its state.")
        server.tool()(run_catia_script)
    return server


async def run_catia_script(
    ctx: Context, code: str, model_id: str | None = None, timeout_seconds: PositiveInt = 60
) -> CallToolResult:
    """执行完整的多行 Python 脚本。catia 是当前 CATIA.Application COM 对象，可 import 模块。
    传 model_id 可先激活目标零件。用 print 返回测量值或其他结果。返回 stdout、stderr、
    exit_code、timed_out。每次使用新的 Python 进程，CATIA 编辑会保留，包括报错前已完成的编辑。
    脚本运行后重新调用 topo 获取面、边和选择组 ID。
    """
    worker = ctx.request_context.lifespan_context
    result = await worker.call(lambda s: execute_script(s, code, timeout_seconds, model_id))
    return CallToolResult(
        isError=result["exit_code"] != 0,
        content=[TextContent(type="text", text=json.dumps(result, ensure_ascii=False))],
        structuredContent=result,
    )
