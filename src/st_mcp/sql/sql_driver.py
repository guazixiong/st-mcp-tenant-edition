"""SQL driver adapter for ShenTong Database connections using SQLAlchemy."""

import logging
import re
from typing import Optional
import asyncio

from st_mcp.sql.connstr import ConnectionSpec
from st_mcp.sql.connstr import ConnectionSpecError
from st_mcp.sql.connstr import resolve_spec

logger = logging.getLogger(__name__)

# Assume STPython is available, but handle missing driver gracefully
STPython = None
connect = None
try:
    # Try to import STPython driver
    import STPython
    from STPython import connect
except ImportError:
    try:
        # Try alternative name
        import oscar as STPython
        from oscar import connect
    except ImportError:
        # Driver not found, we'll handle this gracefully later
        logger.warning("ShenTong database driver not found. Some features may not work properly.")


# QueryResult类已移除，我们现在直接返回字典列表


# "<scheme>://<credentials>@<host>" — greedy, but fenced off by '/' so the
# group still ends at the LAST '@' of the authority, and a password containing
# '@' is masked whole rather than only up to its first separator.
_CREDENTIALS_IN_URL = re.compile(r"(\w+://)([^/?#]*)@")


def _mask_credentials(credentials: str) -> str:
    """Keep the user name, hide the secret, whatever separator was used."""
    for separator in (":", "/"):
        if separator in credentials:
            user, _, _password = credentials.partition(separator)
            return f"{user}{separator}****"
    return "****"


def obfuscate_password(text: str | None, secret: Optional[str] = None) -> str | None:
    """
    Remove the password from any text that may carry connection information.

    ``secret`` is the exact password value; when it is known it is replaced
    literally, which is the only form that survives passwords built from
    ``@``, ``/``, ``:``, ``#`` or ``%``. Without it the URL and DSN shaped
    matches below are used as a best effort.
    """
    if text is None:
        return None

    if not text:
        return text

    masked = text
    if secret:
        masked = masked.replace(secret, "****")

    # shentong://user:password@host:port/db — and the same without a scheme
    masked = _CREDENTIALS_IN_URL.sub(
        lambda m: f"{m.group(1)}{_mask_credentials(m.group(2))}@", masked
    )

    # Match connection string parameters (password=xxx)
    param_pattern = re.compile(r'(password|pwd)(\s*=\s*)([^\s&;"\']+)', re.IGNORECASE)
    masked = param_pattern.sub(r"\1\2****", masked)

    # Match password in DSN format with single quotes
    dsn_single_quote = re.compile(r"(password\s*=\s*')([^']+)(')", re.IGNORECASE)
    masked = dsn_single_quote.sub(r"\1****\3", masked)

    # Match password in DSN format with double quotes
    dsn_double_quote = re.compile(r'(password\s*=\s*")([^"]+)(")', re.IGNORECASE)
    masked = dsn_double_quote.sub(r"\1****\3", masked)

    return masked


class DbConnPool:
    """Database connection manager using STPython driver."""

    def __init__(
        self,
        connection_url: Optional[str] = None,
        spec: Optional[ConnectionSpec] = None,
    ):
        self.connection_url = connection_url
        self.spec = spec
        self.pool_size = 5
        self.connection_pool = []
        self._is_valid = False
        self._last_error = None

    @property
    def url(self) -> str:
        return self.connection_url or ""

    def _open(self, spec: ConnectionSpec):
        """Open a connection, passing every field as its own argument.

        STPython inherits cx_Oracle's ``connect(user, password, dsn)``. Giving
        it the combined ``user/password@host:port/db`` string instead makes the
        extension split that string itself, which loses any password holding
        ``/`` or ``@``, so the split is always done here instead.
        """
        dsn = spec.dsn()
        try:
            return connect(user=spec.user, password=spec.password, dsn=dsn)
        except TypeError as kwargs_error:
            # A build that only accepts positional arguments still gets the
            # same separated fields.
            try:
                return connect(spec.user, spec.password, dsn)
            except Exception:
                raise kwargs_error

    async def _create_connection(self):
        """创建一个新的数据库连接"""
        if connect is None:
            error_msg = "ShenTong database driver not found. Cannot create connection."
            logger.error(error_msg)
            raise ImportError(error_msg)
        if self.spec is None:
            raise ConnectionSpecError(
                "No connection specification has been resolved yet; call pool_connect() first."
            )

        spec = self.spec
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(None, self._open, spec)
        except Exception as e:
            error_msg = (
                f"Failed to create connection to {spec.redacted()}: "
                f"{obfuscate_password(str(e), spec.password)}"
            )
            logger.error(error_msg)
            self._last_error = error_msg
            raise Exception(error_msg) from e

    async def get_connection(self):
        """获取一个数据库连接"""
        if not self._is_valid:
            await self.pool_connect()

        if self.connection_pool:
            return self.connection_pool.pop()
        return await self._create_connection()

    async def release_connection(self, conn):
        """释放数据库连接回池"""
        if len(self.connection_pool) < self.pool_size:
            self.connection_pool.append(conn)
        else:
            await asyncio.to_thread(conn.close)

    async def pool_connect(
        self,
        connection_url: Optional[str] = None,
        spec: Optional[ConnectionSpec] = None,
    ) -> None:
        """Initialize connection pool with STPython driver.

        Args:
            connection_url: Database connection string in one of the formats
                 accepted by :func:`st_mcp.sql.connstr.parse_connection_uri`,
                 e.g. ``oscar://user:password@host:port/database`` or
                 ``user/password@host:port/database``.
            spec: A pre-resolved ConnectionSpec; preferred over the URL.

        Unset fields fall back to the ``ST_HOST`` / ``ST_PORT`` / ``ST_USER`` /
        ``ST_PASSWORD`` / ``ST_DB`` environment variables.
        """
        if spec is not None:
            self.spec = spec
        if connection_url is not None:
            self.connection_url = connection_url

        if self.spec is None:
            if not self.connection_url:
                self._is_valid = False
                self._last_error = "Database connection URL or DSN not provided"
                raise ValueError(self._last_error)
            try:
                self.spec = resolve_spec(self.connection_url)
            except ConnectionSpecError as e:
                self._is_valid = False
                self._last_error = str(e)
                raise

        # Close any existing connections before creating new ones
        await self.close()

        try:
            # Test the connection
            conn = await self._create_connection()
            # 执行一个简单的SQL来验证连接是否成功
            loop = asyncio.get_event_loop()
            cursor = await loop.run_in_executor(None, conn.cursor)
            await loop.run_in_executor(None, cursor.execute, "SELECT 1 FROM dual")
            await loop.run_in_executor(None, cursor.close)
            await self.release_connection(conn)

            self._is_valid = True
            logger.info(f"Successfully connected to ShenTong database at {self.spec.redacted()}")

        except Exception as e:
            self._is_valid = False
            self._last_error = obfuscate_password(str(e), self.spec.password)
            logger.error(f"Failed to connect to database: {self._last_error}")
            raise

    async def close(self) -> None:
        """Close the database connection pool."""
        try:
            # Close all connections in the pool
            loop = asyncio.get_event_loop()
            for conn in self.connection_pool:
                await loop.run_in_executor(None, conn.close)
            self.connection_pool = []
            self._is_valid = False
        except Exception as e:
            logger.error(f"Error closing database connections: {e}")


class SqlDriver:
    """SQL driver for executing queries against ShenTong database."""

    def __init__(self, conn: DbConnPool):
        self.conn = conn

    async def get_connection(self):
        """从连接池获取数据库连接"""
        return await self.conn.get_connection()
    
    async def release_connection(self, conn):
        """释放数据库连接回连接池"""
        await self.conn.release_connection(conn)

    async def execute_query(self, query, params=None):
        """执行SQL查询并返回结果，参考test_osr.py中的实现方式"""
        if connect is None:
            raise ImportError("ShenTong database driver not found. Cannot execute query.")
        
        conn = None
        try:
            conn = await self.get_connection()
            
            # 使用asyncio执行器运行同步的查询操作
            result = await asyncio.to_thread(
                self._execute_query_sync, conn, query, params
            )
            return result
            
        except Exception as e:
            logger.error(f"Query execution failed: {obfuscate_password(str(e))}")
            raise
        finally:
            if conn:
                await self.release_connection(conn)
    
    def _execute_query_sync(self, conn, query, params):
        """同步执行SQL查询"""
        cursor = conn.cursor()
        try:
            cursor.execute(query, params or ())
            
            # 获取列名
            columns = [desc[0] for desc in cursor.description]
            
            # 获取所有结果并转换为字典列表
            result = [dict(zip(columns, row)) for row in cursor.fetchall()]
            return result
        finally:
            cursor.close()

    async def execute_update(self, query, params=None):
        """执行更新类SQL语句（INSERT、UPDATE、DELETE）"""
        if connect is None:
            raise ImportError("ShenTong database driver not found. Cannot execute update.")
        
        conn = None
        try:
            conn = await self.get_connection()
            
            # 使用asyncio执行器运行同步的更新操作
            result = await asyncio.to_thread(
                self._execute_update_sync, conn, query, params
            )
            return result
            
        except Exception as e:
            error_msg = f"Update execution failed: {obfuscate_password(str(e))}"
            logger.error(error_msg)
            raise Exception(error_msg) from e
        finally:
            if conn:
                await self.release_connection(conn)
    
    def _execute_update_sync(self, conn, query, params):
        """同步执行SQL更新操作"""
        cursor = conn.cursor()
        try:
            affected_rows = cursor.execute(query, params or ())
            conn.commit()
            return affected_rows
        except Exception as e:
            conn.rollback()
            raise


class SafeSqlDriver(SqlDriver):
    """安全的SQL驱动，增加了额外的安全检查和超时控制"""
    
    def __init__(self, sql_driver: SqlDriver, timeout: int = 30):
        self.sql_driver = sql_driver
        self.timeout = timeout
        self.conn = sql_driver.conn  # 确保conn属性可用
    
    async def get_connection(self):
        """从连接池获取数据库连接"""
        return await self.sql_driver.get_connection()
    
    async def release_connection(self, conn):
        """释放数据库连接回连接池"""
        await self.sql_driver.release_connection(conn)
    
    async def execute_query(self, query, params=None):
        """执行SQL查询，增加超时控制"""
        # 安全检查：只允许SELECT语句
        query_upper = query.strip().upper()
        if not query_upper.startswith("SELECT"):
            raise ValueError("Only SELECT queries are allowed in restricted mode")
        
        # 使用超时控制执行查询
        try:
            return await asyncio.wait_for(
                self.sql_driver.execute_query(query, params),
                timeout=self.timeout
            )
        except asyncio.TimeoutError:
            raise TimeoutError(f"Query execution timed out after {self.timeout} seconds")
    
    @staticmethod
    async def execute_param_query(sql_driver, query_template, params):
        """执行带参数的SQL查询，适用于需要参数化的查询"""
        # 简单的参数替换实现
        # 注意：这是一个基础实现，实际使用时需要更复杂的参数化处理
        formatted_query = query_template
        for i, param in enumerate(params):
            placeholder = "{}"  # 使用简单的{}作为占位符
            # 确保字符串参数被正确引号包围
            if isinstance(param, str):
                # 转义单引号
                escaped_param = param.replace("'", "''")
                formatted_param = f"'{escaped_param}'"
            else:
                formatted_param = str(param)
            # 替换第一个占位符
            formatted_query = formatted_query.replace(placeholder, formatted_param, 1)
        
        return await sql_driver.execute_query(formatted_query)