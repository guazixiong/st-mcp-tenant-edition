"""Safe SQL execution for read-only queries."""

import logging
import re
import asyncio
from typing import Any, List, Dict

from .sql_driver import SqlDriver

logger = logging.getLogger(__name__)

# Regular expression to detect potentially dangerous SQL commands
DANGEROUS_SQL_PATTERN = re.compile(
    r'\b(?:INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|RENAME|GRANT|REVOKE|MERGE|EXEC|EXECUTE)\b',
    re.IGNORECASE
)


class SafeSqlDriver:
    """SQL driver that only allows read-only queries."""

    def __init__(self, sql_driver: SqlDriver, timeout: int = 30):
        self.sql_driver = sql_driver
        self.timeout = timeout

    async def execute_query(self, query: str) -> List[Dict[str, Any]]:
        """Execute a query after checking if it's safe."""
        # Check if query is potentially dangerous
        if self._is_dangerous_query(query):
            raise ValueError("This query is not allowed in restricted mode (contains potentially dangerous operations)")

        # Execute with timeout to prevent long-running queries
        try:
            return await asyncio.wait_for(
                self.sql_driver.execute_query(query),
                timeout=self.timeout
            )
        except asyncio.TimeoutError:
            raise TimeoutError(f"Query execution timed out after {self.timeout} seconds")

    def _is_dangerous_query(self, query: str) -> bool:
        """Check if a query contains potentially dangerous operations."""
        # Remove comments to avoid false positives
        query_no_comments = re.sub(r'--.*$|/\*.*?\*/', '', query, flags=re.MULTILINE | re.DOTALL)
        # Check for dangerous patterns
        return bool(DANGEROUS_SQL_PATTERN.search(query_no_comments))

    @staticmethod
    async def execute_param_query(
        sql_driver: SqlDriver,
        query_template: str,
        params: List[Any]
    ) -> List[Dict[str, Any]]:
        """Execute a parameterized query safely."""
        # For ShenTong, use :1, :2, :3 style parameters
        query = query_template
        for i, param in enumerate(params, 1):
            # Replace {} placeholders with :i style parameters
            query = query.replace('{}', f':{i}', 1)
        
        # Execute the parameterized query
        return await sql_driver.execute_query(query)