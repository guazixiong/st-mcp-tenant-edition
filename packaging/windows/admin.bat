@echo off
setlocal
cd /d "%~dp0"

set "ST_MCP_PKG=%CD%"
if not defined ST_TENANT_REGISTRY_FILE set "ST_TENANT_REGISTRY_FILE=%CD%\tenants\tenants.json"
if not defined ADMIN_HOST set "ADMIN_HOST=127.0.0.1"
if not defined ADMIN_PORT set "ADMIN_PORT=8001"

set "REMOTE_ARG="
if "%ADMIN_ALLOW_REMOTE%"=="1" set "REMOTE_ARG=--allow-remote"

"env\python.exe" "tools\st-mcp-admin.py" --host %ADMIN_HOST% --port %ADMIN_PORT% --auth-file "secrets\admin.env" %REMOTE_ARG%
