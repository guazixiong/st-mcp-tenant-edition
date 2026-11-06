# NOTICE

ST-MCP — Model Context Protocol server for ShenTong (Oscar) databases
Copyright 2026 ST-MCP contributors

## 关于源码来源（发布前请先读这一条）

本仓库 `src/` 下的 Python 代码是在**厂商交付的 `ST-mcp.zip`（`st_mcp/`，2025-10-23 版）**
基础上改造而来的（改造记录见 `PATCH-NOTES.md` 第一～三节）。
如果你拿到的这份代码来自商业合同、随库交付或保密协议，**公开推送前请确认你有权再分发**；
没有确认之前不要把 `src/` push 到任何公开仓库。

## 本仓库不包含的东西

- 神通数据库安装介质、license —— 商业软件，需向厂商取得（见 `vendor/PUT-FILES-HERE.md`）
- `STPython` Python 驱动 wheel —— 厂商闭源，随介质分发
- `libaci.so` —— 厂商闭源共享库（见 `aci/README.md`）
- `python:3.11-slim` 基础镜像归档、任何 `.whl` 二进制（见 `.gitignore`）
- 现场 IP、主机名、口令、客户标识（提交前由 `.secrets-scan.sh` 把关）

## 第三方组件

运行时依赖的开源组件及其许可证由各自上游持有，版本冻结在 `wheels/requirements.txt`：
`mcp`（MIT）、`uvicorn`（BSD-3）、`starlette`（BSD-3）、`sse-starlette`（MIT）、
`pydantic` / `pydantic-core`（MIT）、`httpx` / `httpcore`（BSD-3）、`cryptography`
（Apache-2.0 / BSD）、`pyjwt`（MIT）、`jsonschema`（MIT）等。
`src/pyproject.toml` 的 `license` 字段与本文件保持一致（MIT）。
