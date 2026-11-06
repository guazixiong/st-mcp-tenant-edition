# ruff: noqa: B008
import argparse
import asyncio
import logging
import os
import signal
import sys
from enum import Enum
from typing import Any
from typing import List
from typing import Literal
from typing import Union

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mcp.types as types
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from pydantic import validate_call

from st_mcp import tenancy
from st_mcp.sql import ConnectionSpecError
from st_mcp.sql import DbConnPool
from st_mcp.sql import SafeSqlDriver
from st_mcp.sql import SqlDriver
from st_mcp.sql import obfuscate_password as _obfuscate_password
from st_mcp.sql import resolve_spec
from st_mcp.explain.explain_plan_tool import ExplainPlanTool
from st_mcp.database_health.database_health_tool import DatabaseHealthTool
from st_mcp.top_queries.top_queries_calc import TopQueriesCalc
from st_mcp.index_analysis.index_analyzer import IndexAnalyzer

# Initialize FastMCP with default settings
mcp = FastMCP("st-mcp")

ResponseType = List[types.TextContent | types.ImageContent | types.EmbeddedResource]

logger = logging.getLogger(__name__)


class AccessMode(str, Enum):
    """SQL access modes for the server."""

    UNRESTRICTED = "unrestricted"  # Unrestricted access
    RESTRICTED = "restricted"  # Read-only with safety features


# 全局连接池实例
db_connection = DbConnPool()


def obfuscate_password(connection_string: str) -> str:
    """Hide the password in any text, using the live password when it is known.

    Replacing the exact value is the only form that works for passwords made
    of ``@``, ``/``, ``:``, ``#`` or ``%``; pattern matching alone either
    truncates at the first separator or misses the tail entirely.
    """
    tenant = tenancy.current_tenant.get()
    if tenant is not None:
        secret = tenant.password
    else:
        secret = db_connection.spec.password if db_connection.spec else None
    return _obfuscate_password(connection_string, secret)


current_access_mode = AccessMode.UNRESTRICTED
shutdown_in_progress = False

# Tool instances
explain_tool = None
health_tool = None
top_queries_tool = None
index_analyzer = None


def request_tenant():
    """Tenant for the current tool call, or None in single-database mode."""
    tenant = tenancy.current_tenant.get()
    if tenant is not None:
        return tenant
    try:
        from mcp.server.lowlevel.server import request_ctx

        context = request_ctx.get()
    except LookupError:
        return None
    return tenancy.tenant_from_request(getattr(context, "request", None))


def effective_access_mode() -> AccessMode:
    tenant = request_tenant()
    if tenant is not None:
        return AccessMode(tenant.access_mode)
    return current_access_mode


async def get_sql_driver() -> Union[SqlDriver, SafeSqlDriver]:
    """Get the appropriate SQL driver based on the current access mode."""
    tenant = request_tenant()
    if tenant is not None:
        base_driver = SqlDriver(conn=await tenancy.get_pool(tenant))
    else:
        base_driver = SqlDriver(conn=db_connection)

    if effective_access_mode() == AccessMode.RESTRICTED:
        logger.debug("Using SafeSqlDriver with restrictions (RESTRICTED mode)")
        return SafeSqlDriver(sql_driver=base_driver, timeout=30)  # 30 second timeout
    else:
        logger.debug("Using unrestricted SqlDriver (UNRESTRICTED mode)")
        return base_driver


async def get_specialized_tools():
    """Explain/health/top-queries/index-analyzer for the calling tenant."""
    tenant = request_tenant()
    if tenant is None:
        return explain_tool, health_tool, top_queries_tool, index_analyzer
    try:
        return await tenancy.get_tools(tenant)
    except Exception as e:
        logger.error(f"Error initializing tenant tools: {obfuscate_password(str(e))}")
        return None, None, None, None


def format_text_response(text: Any) -> ResponseType:
    """Format a text response."""
    return [types.TextContent(type="text", text=str(text))]


def format_error_response(error: str) -> ResponseType:
    """Format an error response."""
    return format_text_response(f"Error: {error}")


@mcp.tool(
    name="list_schemas",
    description="Lists all non-system schemas in the database.",
)
async def list_schemas() -> ResponseType:
    """List all non-system schemas in the database."""
    try:
        # 获取SQL驱动
        sql_driver = await get_sql_driver()
        
        # 查询所有非系统用户的模式 - 使用神通数据库正确的系统表和列名
        query = """
        SELECT DISTINCT owner 
        FROM all_tables 
        WHERE owner NOT IN ('SYS', 'SYSTEM', 'DBSYS', 'OUTLN', 'SYSDBA', 'TRADE', 'PUBLIC')
        UNION
        SELECT DISTINCT owner 
        FROM all_objects 
        WHERE owner NOT IN ('SYS', 'SYSTEM', 'DBSYS', 'OUTLN', 'SYSDBA', 'TRADE', 'PUBLIC')
        ORDER BY 1
        """
        
        result = await sql_driver.execute_query(query)
        
        # 格式化结果
        schemas = []
        for row in result:
            if 'owner' in row and row['owner']:
                schemas.append(row['owner'])
        
        # 去重并排序
        schemas = sorted(list(set(schemas)))
        
        formatted_result = "\n".join([f"- {schema}" for schema in schemas])
        
        if schemas:
            return format_text_response(f"找到 {len(schemas)} 个非系统模式:\n\n{formatted_result}")
        else:
            # 如果没有找到非系统模式，尝试返回当前连接的用户作为默认模式
            try:
                # 获取当前用户
                current_user_query = "SELECT USER FROM DUAL"
                user_result = await sql_driver.execute_query(current_user_query)
                if user_result and len(user_result) > 0 and 'USER' in user_result[0]:
                    current_user = user_result[0]['USER']
                    return format_text_response(f"找到 1 个模式:\n\n- {current_user}")
            except:
                pass
            
            return format_text_response("没有找到非系统模式")
            
    except Exception as e:
        logger.error(f"Error listing schemas: {obfuscate_password(str(e))}")
        return format_error_response(f"查询模式失败: {str(e)}")


@mcp.tool(
    name="list_objects",
    description="Lists all tables, views, and other objects in a specific schema.",
)
async def list_objects(
    schema: str = Field(description="The schema name to list objects from.", json_schema_extra={"required": True})
) -> ResponseType:
    """List all objects in a specific schema."""
    try:
        # 获取SQL驱动
        sql_driver = await get_sql_driver()
        
        # 查询指定模式下的所有对象 - 使用参数化查询
        query = """
        SELECT 
            object_name, 
            object_type 
        FROM all_objects 
        WHERE owner = :owner 
        ORDER BY object_type, object_name
        """
        
        result = await sql_driver.execute_query(query, params={"owner": schema})
        
        # 按类型分组对象
        objects_by_type = {}
        for row in result:
            obj_type = row.get('object_type', 'UNKNOWN')
            obj_name = row.get('object_name', 'UNKNOWN')
            
            if obj_type not in objects_by_type:
                objects_by_type[obj_type] = []
            objects_by_type[obj_type].append(obj_name)
        
        # 格式化结果
        formatted_parts = []
        total_objects = 0
        for obj_type, obj_names in sorted(objects_by_type.items()):
            count = len(obj_names)
            total_objects += count
            formatted_parts.append(f"\n## {obj_type} ({count})")
            for obj_name in sorted(obj_names):
                formatted_parts.append(f"- {obj_name}")
        
        if formatted_parts:
            return format_text_response(f"模式 '{schema}' 中的对象 ({total_objects} 个):\n{''.join(formatted_parts)}")
        else:
            return format_text_response(f"模式 '{schema}' 中没有找到对象")
            
    except Exception as e:
        logger.error(f"Error listing objects in schema {schema}: {obfuscate_password(str(e))}")
        return format_error_response(f"查询对象失败: {str(e)}")


@mcp.tool(description="Show detailed information about a database object")
async def get_object_details(
    schema_name: str = Field(description="Schema name"),
    object_name: str = Field(description="Object name"),
    object_type: str = Field(description="Object type: 'table', 'view', 'sequence', or 'procedure'", default="table"),
) -> ResponseType:
    """Get detailed information about a database object."""
    try:
        sql_driver = await get_sql_driver()

        if object_type in ("table", "view"):
            # Get columns
            col_query = "all_tab_columns" if object_type == "table" else "all_tab_columns"
            col_rows = await SafeSqlDriver.execute_param_query(
                sql_driver,
                """
                SELECT column_name, data_type, nullable, data_default
                FROM all_tab_columns
                WHERE owner = {} AND table_name = {}
                ORDER BY column_id
                """,
                [schema_name, object_name],
            )
            columns = (
                [
                    {
                        "column": r.cells["column_name"],
                        "data_type": r.cells["data_type"],
                        "is_nullable": "YES" if r.cells["nullable"] == 'Y' else "NO",
                        "default": r.cells["data_default"],
                    }
                    for r in col_rows
                ]
                if col_rows
                else []
            )

            # Get constraints
            con_rows = await SafeSqlDriver.execute_param_query(
                sql_driver,
                """
                SELECT tc.constraint_name, tc.constraint_type, kcu.column_name
                FROM all_constraints tc
                LEFT JOIN all_cons_columns kcu
                  ON tc.constraint_name = kcu.constraint_name
                 AND tc.owner = kcu.owner
                WHERE tc.owner = {} AND tc.table_name = {}
                """,
                [schema_name, object_name],
            )

            constraints = {}
            if con_rows:
                for row in con_rows:
                    cname = row.cells["constraint_name"]
                    ctype = row.cells["constraint_type"]
                    col = row.cells["column_name"]

                    if cname not in constraints:
                        constraints[cname] = {"type": ctype, "columns": []}
                    if col:
                        constraints[cname]["columns"].append(col)

            constraints_list = [{"name": name, **data} for name, data in constraints.items()]

            result = {
                "basic": {"schema": schema_name, "name": object_name, "type": object_type},
                "columns": columns,
                "constraints": constraints_list,
            }

        elif object_type == "sequence":
            rows = await SafeSqlDriver.execute_param_query(
                sql_driver,
                """
                SELECT sequence_owner, sequence_name, min_value, max_value, increment_by, last_number
                FROM all_sequences
                WHERE sequence_owner = {} AND sequence_name = {}
                """,
                [schema_name, object_name],
            )

            if rows and rows[0]:
                row = rows[0]
                result = {
                    "schema": row.cells["sequence_owner"],
                    "name": row.cells["sequence_name"],
                    "min_value": row.cells["min_value"],
                    "max_value": row.cells["max_value"],
                    "increment_by": row.cells["increment_by"],
                    "last_number": row.cells["last_number"],
                }
            else:
                result = {}

        elif object_type == "procedure":
            # Get procedure details
            proc_rows = await SafeSqlDriver.execute_param_query(
                sql_driver,
                """
                SELECT object_type, created, last_ddl_time, status
                FROM all_objects
                WHERE owner = {} AND object_name = {}
                """,
                [schema_name, object_name],
            )

            # Get parameters
            param_rows = await SafeSqlDriver.execute_param_query(
                sql_driver,
                """
                SELECT argument_name, in_out, data_type
                FROM all_arguments
                WHERE owner = {} AND object_name = {}
                ORDER BY position
                """,
                [schema_name, object_name],
            )

            params = [
                {"name": r.cells["argument_name"], "direction": r.cells["in_out"], "type": r.cells["data_type"]}
                for r in param_rows
            ] if param_rows else []

            if proc_rows and proc_rows[0]:
                row = proc_rows[0]
                result = {
                    "schema": schema_name,
                    "name": object_name,
                    "type": row.cells["object_type"],
                    "created": row.cells["created"],
                    "last_modified": row.cells["last_ddl_time"],
                    "status": row.cells["status"],
                    "parameters": params
                }
            else:
                result = {}

        return format_text_response(result)
    except Exception as e:
        logger.error(f"Error getting object details: {e}")
        return format_error_response(str(e))


@mcp.tool(description="Execute a SQL query")
async def execute_sql(
    query: str = Field(description="SQL query to execute"),
) -> ResponseType:
    """Execute a SQL query and return the results."""
    try:
        sql_driver = await get_sql_driver()
        
        # Check if it's a SELECT query
        is_select = query.strip().upper().startswith("SELECT")
        
        if is_select:
            rows = await sql_driver.execute_query(query)
            # 修复结果处理逻辑，支持dict类型的行数据
            result = []
            if rows:
                for i, row in enumerate(rows):
                    row_data = {"row": i+1}
                    # 检查row是否为dict类型
                    if isinstance(row, dict):
                        row_data.update(row)
                    # 检查row是否有cells属性（旧格式）
                    elif hasattr(row, 'cells'):
                        row_data.update(row.cells)
                    # 其他情况，尝试直接使用row
                    else:
                        row_data["data"] = str(row)
                    result.append(row_data)
            
            return format_text_response({
                "result": result,
                "row_count": len(result)
            })
        else:
            # For non-SELECT queries, only allowed in unrestricted mode
            if effective_access_mode() == AccessMode.RESTRICTED:
                return format_error_response("Data modification queries are not allowed in restricted mode")
            
            affected_rows = await sql_driver.execute_update(query)
            return format_text_response({
                "status": "success",
                "affected_rows": affected_rows
            })
    except Exception as e:
        logger.error(f"Error executing SQL: {e}")
        return format_error_response(str(e))


@mcp.tool(description="Get database performance metrics")
async def get_performance_metrics() -> ResponseType:
    """Get performance metrics for the ShenTong database."""
    try:
        sql_driver = await get_sql_driver()
        
        # Get session information
        session_rows = await sql_driver.execute_query(
            """
            SELECT sid, username, machine, program, status, logon_time
            FROM v$session
            WHERE username IS NOT NULL
            ORDER BY logon_time DESC
            """
        )
        
        sessions = [row.cells for row in session_rows] if session_rows else []
        
        # Get table statistics
        table_rows = await sql_driver.execute_query(
            """
            SELECT owner, table_name, num_rows, blocks
            FROM all_tables
            WHERE num_rows > 0
            ORDER BY num_rows DESC
            FETCH FIRST 10 ROWS ONLY
            """
        )
        
        tables = [row.cells for row in table_rows] if table_rows else []
        
        metrics = {
            "active_sessions": len([s for s in sessions if s.get("status") == "ACTIVE"]),
            "total_sessions": len(sessions),
            "top_tables_by_rows": tables
        }
        
        return format_text_response(metrics)
    except Exception as e:
        logger.error(f"Error getting performance metrics: {e}")
        return format_error_response(str(e))


@mcp.tool(description="Explain SQL query execution plan")
async def explain_query(
    sql: str = Field(description="SQL query to explain"),
    analyze: bool = Field(default=False, description="When True, actually runs the query to show real execution statistics instead of estimates"),
    hypothetical_indexes: List[dict] = Field(default_factory=list, description="A list of hypothetical indexes to simulate")
) -> ResponseType:
    """Explain a SQL query execution plan."""
    try:
        explain_tool, _, _, _ = await get_specialized_tools()
        if not explain_tool:
            return format_error_response("Explain tool not initialized")
            
        if hypothetical_indexes:
            # Use hypothetical indexes analysis
            plan = await explain_tool.explain_with_hypothetical_indexes(sql, hypothetical_indexes)
        elif analyze:
            # Use analyze mode
            plan = await explain_tool.explain_analyze(sql)
        else:
            # Regular explain
            plan = await explain_tool.explain(sql)
        
        return format_text_response({"plan": plan.to_dict()})
    except Exception as e:
        logger.error(f"Error explaining query: {e}")
        return format_error_response(str(e))


@mcp.tool(description="Analyze database health")
async def analyze_db_health(
    health_type: str = Field(default="all", description="Health check type: all, buffer, connection, constraint, index, replication, sequence, vacuum")
) -> ResponseType:
    """Analyze database health."""
    try:
        _, health_tool, _, _ = await get_specialized_tools()
        if not health_tool:
            return format_error_response("Health tool not initialized")
            
        # Convert health_type to lowercase for comparison
        health_type = health_type.lower()
        
        # Get health check results
        health_results = await health_tool.check_health(health_type)
        
        return format_text_response({"health": health_results})
    except Exception as e:
        logger.error(f"Error analyzing database health: {e}")
        return format_error_response(str(e))


@mcp.tool(description="Get top resource-consuming queries")
async def get_top_queries(
    sort_by: str = Field(default="resources", description="Ranking criteria: 'total_time', 'mean_time', or 'resources'") ,
    limit: int = Field(default=10, description="Number of queries to return")
) -> ResponseType:
    """Get top resource-consuming queries."""
    try:
        _, _, top_queries_tool, _ = await get_specialized_tools()
        if not top_queries_tool:
            return format_error_response("Top queries tool not initialized")
            
        if sort_by == "resources":
            # Get resource-intensive queries
            queries = await top_queries_tool.get_top_resource_queries()
        elif sort_by == "total_time":
            # Get queries with highest total execution time
            queries = await top_queries_tool.get_top_queries_by_time(limit=limit, sort_by="total")
        elif sort_by == "mean_time":
            # Get queries with highest mean execution time
            queries = await top_queries_tool.get_top_queries_by_time(limit=limit, sort_by="mean")
        else:
            # Default to resource-intensive queries
            queries = await top_queries_tool.get_top_resource_queries()
        
        # Ensure we don't exceed the limit
        if len(queries) > limit:
            queries = queries[:limit]
        
        return format_text_response({"queries": queries})
    except Exception as e:
        logger.error(f"Error getting top queries: {e}")
        return format_error_response(str(e))


@mcp.tool(description="Analyze database workload and recommend indexes")
async def analyze_workload_indexes(
    max_index_size_mb: int = Field(default=10000, description="Max index size in MB"),
    method: str = Field(default="dta", description="Method to use for analysis")
) -> ResponseType:
    """Analyze database workload and recommend indexes."""
    try:
        _, _, _, index_analyzer = await get_specialized_tools()
        if not index_analyzer:
            return format_error_response("Index analyzer tool not initialized")
            
        # Analyze all indexes in the database
        analysis = await index_analyzer.analyze_indexes()
        
        return format_text_response({"analysis": analysis})
    except Exception as e:
        logger.error(f"Error analyzing workload indexes: {e}")
        return format_error_response(str(e))


@mcp.tool(description="Analyze specific queries and recommend indexes")
async def analyze_query_indexes(
    queries: List[str] = Field(description="List of Query strings to analyze"),
    max_index_size_mb: int = Field(default=10000, description="Max index size in MB"),
    method: str = Field(default="dta", description="Method to use for analysis")
) -> ResponseType:
    """Analyze specific queries and recommend indexes."""
    try:
        _, _, _, index_analyzer = await get_specialized_tools()
        if not index_analyzer:
            return format_error_response("Index analyzer tool not initialized")
            
        # Analyze the provided queries for index opportunities
        analysis = await index_analyzer.analyze_query_indexes(queries, max_index_size_mb)
        
        return format_text_response({"analysis": analysis})
    except Exception as e:
        logger.error(f"Error analyzing query indexes: {e}")
        return format_error_response(str(e))


async def main():
    """Main entry point for the ST-MCP server."""
    parser = argparse.ArgumentParser(description="ShenTong Database MCP Server")
    parser.add_argument("--database-url", type=str, help="Database connection URL")
    parser.add_argument("--host", type=str, help="Database host (overrides the URL host)")
    parser.add_argument("--port", type=int, help="Database port, default 2003")
    parser.add_argument("--user", type=str, help="Database user, default SYSDBA")
    parser.add_argument(
        "--password",
        type=str,
        help="Database password. Prefer ST_PASSWORD or an env_file: a command line "
        "password is visible in ps output and shell history.",
    )
    parser.add_argument("--database", type=str, help="Database name, default OSRDB")
    parser.add_argument(
        "--url-encoded",
        action="store_true",
        help="Percent-decode the URL before splitting it. Leave off to take every "
        "character literally, which is what a special-symbol password needs.",
    )
    parser.add_argument(
        "--test-connection",
        action="store_true",
        help="Connect, run SELECT 1 FROM dual, report the resolved settings "
        "(password never printed) and exit. Used by the deployment script.",
    )
    parser.add_argument("--access-mode", type=str, choices=["restricted", "unrestricted"], default="unrestricted",
                        help="Database access mode (default: unrestricted)")
    parser.add_argument(
        "--transport",
        type=str,
        choices=["stdio", "sse", "streamable-http"],
        default="sse",
        help="Transport protocol (default: sse)",
    )
    parser.add_argument("--sse-host", type=str, default="localhost", help="HTTP server host (default: localhost)")
    parser.add_argument("--sse-port", type=int, default=8000, help="HTTP server port (default: 8000)")
    parser.add_argument(
        "--mcp-path",
        type=str,
        default="/mcp",
        help="Endpoint path for streamable-http (default: /mcp)",
    )
    parser.add_argument("--log-level", type=str, default="INFO", help="Logging level (default: INFO)")
    parser.add_argument(
        "--tenant-registry",
        type=str,
        default=None,
        help="Multi-tenant registry JSON (default: ST_TENANT_REGISTRY). With a registry "
        "one process on one port serves every tenant; the request token picks the account.",
    )
    
    args = parser.parse_args()
    
    # Configure logging
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    
    # Set access mode
    global current_access_mode
    current_access_mode = AccessMode(args.access_mode)
    
    logger.info(f"Starting ShenTong MCP Server in {current_access_mode.upper()} mode")

    # A registry switches the process to multi-tenant mode: no shared connection,
    # no shared tools, every request resolves its own database account.
    registry_path = args.tenant_registry or os.environ.get(tenancy.REGISTRY_ENV)
    tenant_mode = bool(registry_path)
    if tenant_mode:
        os.environ[tenancy.REGISTRY_ENV] = registry_path
        loaded = tenancy.init_registry(registry_path)
        logger.info(f"Multi-tenant mode: registry {registry_path} ({len(loaded.all()) if loaded else 0} tenant(s))")

    # Get database URL from environment variable or command line
    database_url = os.environ.get("DATABASE_URI") or args.database_url

    spec = None
    if not tenant_mode:
        try:
            spec = resolve_spec(
                uri=database_url,
                host=args.host,
                port=args.port,
                user=args.user,
                password=args.password,
                database=args.database,
                url_encoded=True if args.url_encoded else None,
            )
        except ConnectionSpecError as e:
            logger.error(f"Invalid connection settings: {e}")
            sys.exit(2)

        logger.info(f"Connecting as {spec.redacted()}")

    if args.test_connection:
        if spec is None:
            print("CONNECT_FAILED multi-tenant mode has no shared connection")
            sys.exit(1)
        try:
            await db_connection.pool_connect(spec=spec)
            print("CONNECT_OK " + spec.redacted())
            sys.exit(0)
        except Exception as e:
            print("CONNECT_FAILED " + obfuscate_password(str(e)))
            sys.exit(1)
    
    # Register tools
    mcp.add_tool(list_schemas)
    mcp.add_tool(list_objects)
    mcp.add_tool(get_object_details)
    mcp.add_tool(get_performance_metrics)
    
    # Add execute_sql tool with appropriate description
    if tenant_mode:
        mcp.add_tool(execute_sql, description="Execute a SQL query (read-only for restricted tenants)")
    elif current_access_mode == AccessMode.UNRESTRICTED:
        mcp.add_tool(execute_sql, description="Execute any SQL query")
    else:
        mcp.add_tool(execute_sql, description="Execute a read-only SQL query")
    
    # Add index analysis tools
    mcp.add_tool(analyze_workload_indexes)
    mcp.add_tool(analyze_query_indexes)
    
    # Initialize database connection pool
    if tenant_mode:
        logger.info("Tenant pools are created on first use; each token gets its own database account")
    else:
        try:
            await db_connection.pool_connect(spec=spec)
            logger.info("Successfully connected to database and initialized connection pool")
            
            # Initialize specialized tools
            global explain_tool, health_tool, top_queries_tool, index_analyzer
            sql_driver = SqlDriver(conn=db_connection)
            explain_tool = ExplainPlanTool(sql_driver)
            health_tool = DatabaseHealthTool(sql_driver)
            top_queries_tool = TopQueriesCalc(sql_driver)
            index_analyzer = IndexAnalyzer(sql_driver)
            logger.info("Initialized specialized tools: explain, health check, top queries, index analyzer")
        except Exception as e:
            logger.warning(
                f"Could not connect to database: {obfuscate_password(str(e))}",
            )
            logger.warning(
                "The MCP server will start but database operations will fail until a valid connection is established.",
            )
    
    # Set up proper shutdown handling
    try:
        loop = asyncio.get_running_loop()
        signals = (signal.SIGTERM, signal.SIGINT)
        for s in signals:
            loop.add_signal_handler(s, lambda s=s: asyncio.create_task(shutdown(s)))
    except NotImplementedError:
        # Windows doesn't support signals properly
        logger.warning("Signal handling not supported on Windows")
        pass
    
    # Run the server with the selected transport (always async)
    if args.transport == "stdio":
        if tenant_mode:
            logger.error("stdio carries no HTTP headers, so it cannot select a tenant; use streamable-http")
            sys.exit(2)
        await mcp.run_stdio_async()
    else:
        # Update FastMCP settings based on command line arguments
        mcp.settings.host = args.sse_host
        mcp.settings.port = args.sse_port
        if args.transport == "streamable-http":
            if not args.mcp_path.startswith("/"):
                args.mcp_path = "/" + args.mcp_path
            mcp.settings.streamable_http_path = args.mcp_path
            # FastMCP enabled Host/Origin pinning at construction time because it defaults to a
            # loopback host; left on, it answers 421 to any client that reaches this server by
            # address rather than by one of the allowlisted loopback names.
            if args.sse_host not in ("127.0.0.1", "localhost", "::1"):
                mcp.settings.transport_security = None
            logger.info(f"Streamable HTTP endpoint: http://{args.sse_host}:{args.sse_port}{args.mcp_path}")
            if tenant_mode:
                await run_tenant_http(args)
            else:
                await mcp.run_streamable_http_async()
        else:
            if tenant_mode:
                logger.error("multi-tenant mode supports only streamable-http")
                sys.exit(2)
            await mcp.run_sse_async()


async def run_tenant_http(args) -> None:
    """Serve the MCP app behind the tenant middleware on one port."""
    import uvicorn

    tenant_registry = tenancy.registry()
    app = tenancy.TenantMiddleware(mcp.streamable_http_app(), tenant_registry, args.mcp_path)
    config = uvicorn.Config(
        app,
        host=args.sse_host,
        port=args.sse_port,
        log_level=mcp.settings.log_level.lower(),
        # The token may live in the URL, so the access log would print it.
        access_log=False,
    )
    logger.info(
        "Multi-tenant requests pick their account with 'Authorization: Bearer <token>'; "
        f"{len(tenant_registry.all())} tenant(s) registered"
    )
    await uvicorn.Server(config).serve()


async def shutdown(sig=None):
    """Clean shutdown of the server."""
    global shutdown_in_progress

    if shutdown_in_progress:
        logger.warning("Forcing immediate exit")
        # Use sys.exit instead of os._exit to allow for proper cleanup
        sys.exit(1)

    shutdown_in_progress = True

    if sig:
        logger.info(f"Received exit signal {sig.name}")

    # Close database connections
    try:
        await db_connection.close()
        await tenancy.close_all()
        logger.info("Closed database connections")
    except Exception as e:
        logger.error(f"Error closing database connections: {e}")

    # Exit with appropriate status code
    sys.exit(128 + sig if sig is not None else 0)