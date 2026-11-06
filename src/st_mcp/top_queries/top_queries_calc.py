"""Calculator for identifying top resource-consuming queries in ShenTong Database."""

import logging
from typing import Dict, List, Any
from st_mcp.sql.sql_driver import SqlDriver

logger = logging.getLogger(__name__)


class TopQueriesCalc:
    """Tool to identify and analyze top resource-consuming queries."""
    
    def __init__(self, sql_driver: SqlDriver):
        self.sql_driver = sql_driver
    
    async def get_top_resource_queries(self) -> List[Dict[str, Any]]:
        """Get the most resource-intensive queries based on available metrics."""
        try:
            # ShenTong might not have pg_stat_statements exactly, but we can use what's available
            
            # Try to get long-running queries first
            long_running_query = """
            SELECT 
                sid,
                serial#,
                username,
                machine,
                program,
                sql_text,
                last_call_et as execution_time_seconds,
                status
            FROM 
                v$session
            WHERE 
                status = 'ACTIVE'
                AND username IS NOT NULL
                AND sql_text IS NOT NULL
            ORDER BY 
                last_call_et DESC
            FETCH FIRST 10 ROWS ONLY
            """
            
            long_running_rows = await self.sql_driver.execute_query(long_running_query)
            
            result = []
            
            if long_running_rows:
                for row in long_running_rows:
                    result.append({
                        "type": "long_running",
                        "session_id": row.cells.get("sid"),
                        "serial": row.cells.get("serial#"),
                        "username": row.cells.get("username"),
                        "machine": row.cells.get("machine"),
                        "program": row.cells.get("program"),
                        "sql_text": row.cells.get("sql_text"),
                        "execution_time_seconds": row.cells.get("execution_time_seconds"),
                        "status": row.cells.get("status")
                    })
            
            # Also try to get some historical query information if available
            # Note: This might require additional auditing configuration in ShenTong
            
            # If we don't have any active long-running queries, provide a message
            if not result:
                result.append({
                    "message": "No active long-running queries detected. Consider enabling auditing for historical query analysis."
                })
            
            return result
            
        except Exception as e:
            logger.error(f"Error getting top resource queries: {e}")
            return [{
                "error": str(e),
                "message": "Failed to retrieve top resource queries"
            }]
    
    async def get_top_queries_by_time(self, limit: int = 10, sort_by: str = "mean") -> List[Dict[str, Any]]:
        """Get the top queries by execution time.
        
        Args:
            limit: Maximum number of queries to return
            sort_by: Sorting method: 'mean' for average time or 'total' for total time
        """
        try:
            # Note: ShenTong might not have built-in query performance statistics like PostgreSQL
            # This is a best-effort implementation based on available views
            
            # Try to get information from v$sqlarea or similar if available
            order_by_clause = "avg_elapsed_time DESC" if sort_by == "mean" else "total_elapsed_time DESC"
            query = f"""
            SELECT 
                sql_text,
                executions,
                elapsed_time/executions as avg_elapsed_time,
                elapsed_time as total_elapsed_time,
                disk_reads,
                buffer_gets
            FROM 
                v$sqlarea
            WHERE 
                executions > 0
            ORDER BY 
                {order_by_clause}
            FETCH FIRST :limit ROWS ONLY
            """
            
            try:
                # Try with v$sqlarea first
                rows = await self.sql_driver.execute_query(query.replace(":limit", str(limit)))
                
                result = []
                if rows:
                    for row in rows:
                        result.append({
                            "sql_text": row.cells.get("sql_text"),
                            "executions": row.cells.get("executions"),
                            "avg_elapsed_time": row.cells.get("avg_elapsed_time"),
                            "total_elapsed_time": row.cells.get("total_elapsed_time"),
                            "disk_reads": row.cells.get("disk_reads"),
                            "buffer_gets": row.cells.get("buffer_gets")
                        })
                
                if result:
                    return result
            except Exception:
                # If v$sqlarea is not available, try another approach
                pass
            
            # Fallback: Use active sessions again
            active_query = """
            SELECT 
                sid,
                username,
                sql_text,
                last_call_et as execution_time_seconds
            FROM 
                v$session
            WHERE 
                status = 'ACTIVE'
                AND username IS NOT NULL
                AND sql_text IS NOT NULL
            ORDER BY 
                last_call_et DESC
            FETCH FIRST :limit ROWS ONLY
            """
            
            rows = await self.sql_driver.execute_query(active_query.replace(":limit", str(limit)))
            
            result = []
            if rows:
                for row in rows:
                    result.append({
                        "type": "active_query",
                        "session_id": row.cells.get("sid"),
                        "username": row.cells.get("username"),
                        "sql_text": row.cells.get("sql_text"),
                        "execution_time_seconds": row.cells.get("execution_time_seconds")
                    })
            
            # If still no results, provide a message
            if not result:
                result.append({
                    "message": f"No query performance statistics available. Top {limit} {sort_by} time queries could not be retrieved."
                })
            
            return result
            
        except Exception as e:
            logger.error(f"Error getting top queries by time: {e}")
            return [{
                "error": str(e),
                "message": f"Failed to retrieve top {limit} {sort_by} time queries"
            }]