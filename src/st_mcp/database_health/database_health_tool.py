"""Database health monitoring tool for ShenTong Database."""

import logging
from typing import Dict, List, Any
from st_mcp.sql.sql_driver import SqlDriver

logger = logging.getLogger(__name__)


class DatabaseHealthTool:
    """Tool to perform various database health checks."""
    
    def __init__(self, sql_driver: SqlDriver):
        self.sql_driver = sql_driver
    
    async def health(self, health_type: str = "all") -> Dict[str, Any]:
        """Run health checks based on the specified health type."""
        results = {}
        
        # Parse health types (supports comma-separated list)
        health_types = [t.strip().lower() for t in health_type.split(",")]
        
        # Run appropriate health checks
        if "all" in health_types:
            results = await self._run_all_health_checks()
        else:
            if "index" in health_types:
                results["index_health"] = await self._check_index_health()
            if "connection" in health_types:
                results["connection_health"] = await self._check_connection_health()
            if "vacuum" in health_types:
                results["vacuum_health"] = await self._check_vacuum_health()
            if "sequence" in health_types:
                results["sequence_health"] = await self._check_sequence_health()
            if "replication" in health_types:
                results["replication_health"] = await self._check_replication_health()
            if "buffer" in health_types:
                results["buffer_health"] = await self._check_buffer_health()
            if "constraint" in health_types:
                results["constraint_health"] = await self._check_constraint_health()
        
        return results
    
    async def _run_all_health_checks(self) -> Dict[str, Any]:
        """Run all available health checks."""
        return {
            "index_health": await self._check_index_health(),
            "connection_health": await self._check_connection_health(),
            "vacuum_health": await self._check_vacuum_health(),
            "sequence_health": await self._check_sequence_health(),
            "replication_health": await self._check_replication_health(),
            "buffer_health": await self._check_buffer_health(),
            "constraint_health": await self._check_constraint_health()
        }
    
    async def _check_index_health(self) -> Dict[str, Any]:
        """Check index health, including invalid or unused indexes."""
        try:
            # Get index information
            query = """
            SELECT 
                owner, 
                index_name, 
                table_name, 
                status
            FROM 
                all_indexes
            WHERE 
                owner NOT IN ('SYS', 'SYSTEM')
            """
            
            rows = await self.sql_driver.execute_query(query)
            
            indexes = []
            invalid_indexes = []
            
            if rows:
                for row in rows:
                    index_info = {
                        "owner": row.cells.get("owner"),
                        "index_name": row.cells.get("index_name"),
                        "table_name": row.cells.get("table_name"),
                        "status": row.cells.get("status")
                    }
                    indexes.append(index_info)
                    
                    # Check for invalid indexes
                    if row.cells.get("status") != 'VALID':
                        invalid_indexes.append(index_info)
            
            return {
                "total_indexes": len(indexes),
                "invalid_indexes": invalid_indexes,
                "invalid_index_count": len(invalid_indexes),
                "status": "warning" if invalid_indexes else "healthy"
            }
            
        except Exception as e:
            logger.error(f"Error checking index health: {e}")
            return {
                "error": str(e),
                "status": "error"
            }
    
    async def _check_connection_health(self) -> Dict[str, Any]:
        """Check database connection health."""
        try:
            # Get session information
            query = """
            SELECT 
                status, 
                COUNT(*) as count
            FROM 
                v$session
            WHERE 
                username IS NOT NULL
            GROUP BY 
                status
            """
            
            rows = await self.sql_driver.execute_query(query)
            
            status_counts = {}
            total_sessions = 0
            active_sessions = 0
            
            if rows:
                for row in rows:
                    status = row.cells.get("status")
                    count = int(row.cells.get("count", 0))
                    status_counts[status] = count
                    total_sessions += count
                    if status == 'ACTIVE':
                        active_sessions += count
            
            return {
                "total_sessions": total_sessions,
                "active_sessions": active_sessions,
                "status_distribution": status_counts,
                "status": "healthy" if total_sessions < 1000 else "warning"  # Arbitrary threshold
            }
            
        except Exception as e:
            logger.error(f"Error checking connection health: {e}")
            return {
                "error": str(e),
                "status": "error"
            }
    
    async def _check_vacuum_health(self) -> Dict[str, Any]:
        """Check vacuum/statistics health."""
        try:
            # Get tables with outdated statistics
            query = """
            SELECT 
                owner, 
                table_name, 
                last_analyzed
            FROM 
                all_tables
            WHERE 
                owner NOT IN ('SYS', 'SYSTEM')
                AND last_analyzed IS NOT NULL
            ORDER BY 
                last_analyzed ASC
            FETCH FIRST 50 ROWS ONLY
            """
            
            rows = await self.sql_driver.execute_query(query)
            
            tables_needing_analysis = []
            
            if rows:
                # For simplicity, we'll just return the oldest analyzed tables
                tables_needing_analysis = [
                    {
                        "owner": row.cells.get("owner"),
                        "table_name": row.cells.get("table_name"),
                        "last_analyzed": row.cells.get("last_analyzed")
                    }
                    for row in rows[:10]  # Show top 10 oldest
                ]
            
            return {
                "tables_needing_analysis": tables_needing_analysis,
                "status": "info"
            }
            
        except Exception as e:
            logger.error(f"Error checking vacuum health: {e}")
            return {
                "error": str(e),
                "status": "error"
            }
    
    async def _check_sequence_health(self) -> Dict[str, Any]:
        """Check sequence health and potential overflow issues."""
        try:
            # Get sequence information
            query = """
            SELECT 
                sequence_owner, 
                sequence_name, 
                max_value, 
                last_number,
                (last_number / max_value * 100) as usage_percentage
            FROM 
                all_sequences
            WHERE 
                sequence_owner NOT IN ('SYS', 'SYSTEM')
                AND max_value > 0
            ORDER BY 
                usage_percentage DESC
            FETCH FIRST 50 ROWS ONLY
            """
            
            rows = await self.sql_driver.execute_query(query)
            
            sequences = []
            sequences_near_limit = []
            
            if rows:
                for row in rows:
                    usage_pct = float(row.cells.get("usage_percentage", 0))
                    seq_info = {
                        "sequence_owner": row.cells.get("sequence_owner"),
                        "sequence_name": row.cells.get("sequence_name"),
                        "max_value": row.cells.get("max_value"),
                        "last_number": row.cells.get("last_number"),
                        "usage_percentage": usage_pct
                    }
                    sequences.append(seq_info)
                    
                    # Flag sequences that are more than 80% used
                    if usage_pct > 80:
                        sequences_near_limit.append(seq_info)
            
            return {
                "total_sequences": len(sequences),
                "sequences_near_limit": sequences_near_limit,
                "status": "warning" if sequences_near_limit else "healthy"
            }
            
        except Exception as e:
            logger.error(f"Error checking sequence health: {e}")
            return {
                "error": str(e),
                "status": "error"
            }
    
    async def _check_replication_health(self) -> Dict[str, Any]:
        """Check replication health (if applicable)."""
        try:
            # ShenTong specific replication check
            # This is a placeholder as the exact replication views depend on ShenTong version
            return {
                "replication_status": "info",
                "message": "Replication health check not fully implemented for ShenTong",
                "status": "info"
            }
            
        except Exception as e:
            logger.error(f"Error checking replication health: {e}")
            return {
                "error": str(e),
                "status": "error"
            }
    
    async def _check_buffer_health(self) -> Dict[str, Any]:
        """Check buffer cache statistics."""
        try:
            # Get buffer usage statistics if available
            # This is a placeholder as the exact views depend on ShenTong version
            return {
                "buffer_status": "info",
                "message": "Buffer cache statistics not fully implemented for ShenTong",
                "status": "info"
            }
            
        except Exception as e:
            logger.error(f"Error checking buffer health: {e}")
            return {
                "error": str(e),
                "status": "error"
            }
    
    async def _check_constraint_health(self) -> Dict[str, Any]:
        """Check constraint health and validity."""
        try:
            # Get constraint information
            query = """
            SELECT 
                owner, 
                constraint_name, 
                table_name, 
                constraint_type, 
                status
            FROM 
                all_constraints
            WHERE 
                owner NOT IN ('SYS', 'SYSTEM')
            """
            
            rows = await self.sql_driver.execute_query(query)
            
            constraints = []
            invalid_constraints = []
            
            if rows:
                for row in rows:
                    constraint_info = {
                        "owner": row.cells.get("owner"),
                        "constraint_name": row.cells.get("constraint_name"),
                        "table_name": row.cells.get("table_name"),
                        "constraint_type": row.cells.get("constraint_type"),
                        "status": row.cells.get("status")
                    }
                    constraints.append(constraint_info)
                    
                    # Check for invalid constraints
                    if row.cells.get("status") != 'ENABLED':
                        invalid_constraints.append(constraint_info)
            
            return {
                "total_constraints": len(constraints),
                "invalid_constraints": invalid_constraints,
                "invalid_constraint_count": len(invalid_constraints),
                "status": "warning" if invalid_constraints else "healthy"
            }
            
        except Exception as e:
            logger.error(f"Error checking constraint health: {e}")
            return {
                "error": str(e),
                "status": "error"
            }