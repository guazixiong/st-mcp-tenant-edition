@echo off
setlocal
cd /d "%~dp0"

if not defined ST_TENANT_REGISTRY set "ST_TENANT_REGISTRY=%CD%\tenants\tenants.json"
if not exist "%ST_TENANT_REGISTRY%" (
  echo Tenant registry not found: %ST_TENANT_REGISTRY%
  echo Create tenants with admin.bat first.
  exit /b 1
)
if not defined ST_HOST set "ST_HOST=127.0.0.1"
if not defined ST_PORT set "ST_PORT=2003"
if not defined ST_USER set "ST_USER=SYSDBA"
if not defined ST_DB set "ST_DB=OSRDB"
if not defined SSE_HOST set "SSE_HOST=0.0.0.0"
if not defined SSE_PORT set "SSE_PORT=8100"
if not defined MCP_PATH set "MCP_PATH=/mcp"

"env\Scripts\st-mcp.exe" --transport streamable-http --sse-host %SSE_HOST% --sse-port %SSE_PORT% --mcp-path %MCP_PATH%
