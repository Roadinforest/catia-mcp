# CATIA MCP

用于 WHUCAD 工具调用评测的 CATIA V5 MCP 服务。当前版本提供 MCP 握手、传输和 Windows COM 连接。客户端调用 `tools/list` 会收到 `{"tools":[]}`。

## 安装

在仓库根目录用 Python 3.11 或更高版本执行以下 PowerShell 命令。实际访问 CATIA 需要在装有 CATIA V5、且已注册 `CATIA.Application` 的 Windows 用户会话中运行。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

## 启动服务

本地 MCP 客户端可以通过 stdio 启动服务。将客户端配置中的 `command` 替换为本仓库虚拟环境里 Python 的绝对路径。

```json
{
  "mcpServers": {
    "catia": {
      "command": "C:\\work\\catia-mcp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "catia_mcp"]
    }
  }
}
```

使用 Streamable HTTP 时，在 PowerShell 中启动服务，然后让客户端连接 `http://127.0.0.1:8000/mcp`。可通过 `--port` 指定端口。

```powershell
.\.venv\Scripts\python.exe -m catia_mcp streamable-http
```

## 验证 CATIA 连接

MCP 握手及枚举工具均可在 CATIA 关闭时完成。实际访问 COM 时，连接器优先连接当前用户已运行的 CATIA 实例。若没有活动实例，则请求 Windows 通过 COM 激活 CATIA。下面的只读检查会输出 CATIA 的应用名称。

```powershell
.\.venv\Scripts\python.exe -c "import asyncio; from catia_mcp.connection import run_in_catia; print(asyncio.run(run_in_catia(lambda app: app.Name)))"
```

`src/catia_mcp/server.py` 创建 FastMCP 服务，并将 `run_in_catia` 放入请求的 `lifespan_context`。`src/catia_mcp/connection.py` 串行执行同一服务进程内的 CAD 操作。一个操作中的 COM 访问在同一工作线程完成，回调返回字符串、数值等普通数据。

服务与连接分层参考了 [tongriyaotxt/catia-mcp](https://github.com/tongriyaotxt/catia-mcp)。协议实现使用 [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)。
