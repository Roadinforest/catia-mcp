# WHUCAD-MCP：序列还原服务

本仓库首先服务于 **WHUCAD-MCP version 程序序列的还原**：按照转换后的指令、参数和选择关系，在 CATIA V5 中重建零件。毕设中的自然语言理解、工具规划和评测功能在这个基线上另行扩展。

当前发行：**v0.2.0**。推荐协议：**whucad-migration（20 个工具）**。程序 JSON 版本：**3**。目录名 mcp-data-v2、Git 发行版本和 JSON 格式版本是不同的编号。

## 安装与启动

需要 Windows、64 位 Python 3.11–3.13、可用的 CATIA V5 与许可。依赖已锁定；安装会创建本目录自己的 `.venv`。

```powershell
.\setup.ps1
# Python 不在 PATH 时：
.\setup.ps1 -Python 'C:\Path\To\python.exe'
.\start-mcp.ps1
# HTTP 模式：
.\start-mcp.ps1 streamable-http --port 8000
```

标准输入输出模式供 MCP 客户端连接；HTTP 地址为 http://127.0.0.1:8000/mcp。服务启动和工具枚举不需要创建 CATIA 文档。

公共入口固定新版协议。直接执行 `python -m catia_mcp` 仍然维持历史 legacy 默认值；开发时要使用新版，应显式添加 `--protocol whucad-migration`。

## 校验和还原一个程序

```powershell
.\run.ps1 validate .\examples\00000000.json
.\run.ps1 execute .\examples\00000000.json --output .\runs\first-case
# 还原完整 v2 数据集中的某个程序：
.\run.ps1 execute 'D:\Path\To\mcp-data-v2\programs\0000\00000000.json' --output .\runs\another-case
```

`validate` 仅检查参数结构，不代表建模通过。`execute` 通过真实 stdio MCP 服务在默认 CATIA 环境创建新文档，生成 CATPart、STEP 和调用记录，并检查保存后重新打开。输出目录必须不存在。执行器只关闭它创建的文档；`--keep-open` 可保留结果。

手动使用 MCP 工具时，先在 CATIA 准备空白 CATPart，再用 `model_state` 绑定；JSON 执行器自动完成这一步。程序内不写会话 model_id，由执行器注入。

## 新版协议的关键约定

- `sol` 开始轮廓；绘图工具把曲线加入当前草图。
- `selection` 接收 WHUCAD 对象来源树，服务保留当前选择。
- `topo`、`mirror_topo` 开始选择声明，**不返回旧版的面/边几何候选清单**。
- 加工工具使用当前草图或当前选择，不接收旧版 sketch_id、edge_id 或 $ref/$match。
- 长度使用 mm；arc 的 start_angle/end_angle/mid_angle 使用弧度；revolve/groove 的角度遵循原 WHUCAD 解码约定。详见 tools.json，不能通用套用“所有角度都是度”。
- 参数来自 WHUCAD 归一化后的模型，不能凭这些数值恢复量化之前的原始绝对尺寸。

## 代码组织与后续开发

| 路径 | 用途 |
|---|---|
| src/catia_mcp/stateful_migration.py | 新版 20 工具及状态管理 |
| src/catia_mcp/converter_semantics.py | WHUCAD 建模语义兼容 |
| vendor/whucad_converter | 原 converter 运行源码与来源哈希 |
| scripts/execute_mcp_json.py | 单个程序的校验和真实 MCP 执行 |
| whucad_conversion/migration.py | H5 向量到新版指令的编译库 |
| tools.json | 新版工具参数定义快照 |
| examples/00000000.json | 单个还原示例 |
| tests | 接口、迁移和执行器回归测试 |
| docs/releases | release 范围、备注及验证记录 |

旧版工具与早期状态式原型保留用于兼容和回归。题库、模型 API 调用、实验成绩、全量数据集、环境文件和输出模型不属于这个发行。

后续毕设工作目录为 `毕设/MCP`，从发行标签创建 `thesis-development` 分支。WHUCAD 还原基线由标签冻结；新增功能在该分支继续开发，不移动基线标签。

[发行备注](docs/releases/v0.2.0.md) · [验证记录](docs/releases/v0.2.0-validation.md)

## 来源

服务与连接基础设施最初参考 tongriyaotxt/catia-mcp；原 WHUCAD 源码按 vendor 中的 provenance.json 保留出处与哈希。本发行不包含 CATIA 软件、许可证或全量 WHUCAD 数据集。
