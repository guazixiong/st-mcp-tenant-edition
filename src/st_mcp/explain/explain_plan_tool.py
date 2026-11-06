"""Tool for explaining SQL query execution plans in ShenTong Database."""

import logging
from typing import Any, Dict, List, Optional

from .artifacts import ExplainPlanArtifact, ErrorResult
from ..sql.sql_driver import SqlDriver

logger = logging.getLogger(__name__)


class ExplainPlanTool:
    """Tool to generate and analyze execution plans for SQL queries."""
    
    def __init__(self, sql_driver: SqlDriver):
        self.sql_driver = sql_driver
    
    async def explain(self, sql: str) -> ExplainPlanArtifact | ErrorResult:
        """Generate an execution plan for a SQL query without executing it."""
        try:
            # ShenTong's EXPLAIN syntax
            explain_query = f"EXPLAIN PLAN FOR {sql}"
            
            # Execute the EXPLAIN command
            await self.sql_driver.execute_query(explain_query)
            
            # Get the plan from PLAN_TABLE
            plan_query = """
            SELECT statement_id, operation, options, object_name, cost, cardinality 
            FROM plan_table 
            ORDER BY id
            """
            
            rows = await self.sql_driver.execute_query(plan_query)
            
            # Format the plan
            plan_text = []
            plan_details = []
            
            if rows:
                for row in rows:
                    operation = row.get('operation', '').strip()
                    options = row.get('options', '').strip()
                    object_name = row.get('object_name', '').strip()
                    cost = row.get('cost', '')
                    cardinality = row.get('cardinality', '')
                    
                    # Format each row of the plan
                    line = f"{operation}"
                    if options:
                        line += f" {options}"
                    if object_name:
                        line += f" {object_name}"
                    if cost or cardinality:
                        stats = []
                        if cost:
                            stats.append(f"Cost={cost}")
                        if cardinality:
                            stats.append(f"Rows={cardinality}")
                        line += f" ({', '.join(stats)})"
                    
                    plan_text.append(line)
                    plan_details.append(dict(row))
            else:
                plan_text.append("No execution plan available")
            
            return ExplainPlanArtifact(
                query=sql,
                plan_text='\n'.join(plan_text),
                plan_details={"plan_rows": plan_details}
            )
            
        except Exception as e:
            logger.error(f"Error generating explain plan: {e}")
            return ErrorResult(
                error_message=str(e),
                query=sql
            )
    
    async def explain_analyze(self, sql: str) -> ExplainPlanArtifact | ErrorResult:
        """Generate an execution plan and execute the query to get actual statistics."""
        try:
            # ShenTong might not support EXPLAIN ANALYZE directly
            # We'll execute the query and get statistics if available
            
            # First get the plan
            plan_result = await self.explain(sql)
            
            if isinstance(plan_result, ErrorResult):
                return plan_result
            
            # Then execute the query to get actual statistics
            # Note: This might not be exactly EXPLAIN ANALYZE as in PostgreSQL
            try:
                result = await self.sql_driver.execute_query(sql)
                
                # Enhance the plan with execution statistics
                enhanced_plan_text = plan_result.plan_text + f"\n\nActual execution completed. Retrieved {len(result) if result else 0} rows."
                
                return ExplainPlanArtifact(
                    query=sql,
                    plan_text=enhanced_plan_text,
                    plan_details={
                        **plan_result.plan_details,
                        "actual_row_count": len(result) if result else 0,
                        "execution_status": "completed"
                    }
                )
            except Exception as exec_e:
                # If execution fails, return the original plan with a note
                error_plan_text = plan_result.plan_text + f"\n\nNote: Query execution failed: {str(exec_e)}"
                return ExplainPlanArtifact(
                    query=sql,
                    plan_text=error_plan_text,
                    plan_details={
                        **plan_result.plan_details,
                        "execution_status": "failed",
                        "execution_error": str(exec_e)
                    }
                )
                
        except Exception as e:
            logger.error(f"Error in explain analyze: {e}")
            return ErrorResult(
                error_message=str(e),
                query=sql
            )
    
    async def explain_with_hypothetical_indexes(self, sql: str, hypothetical_indexes: List[Dict[str, Any]]) -> ExplainPlanArtifact | ErrorResult:
        """Generate an execution plan with hypothetical indexes."""
        try:
            # ShenTong doesn't have a direct equivalent to PostgreSQL's HypoPG
            # We'll explain the query and note that hypothetical indexes aren't supported
            
            # First get the regular plan
            plan_result = await self.explain(sql)
            
            if isinstance(plan_result, ErrorResult):
                return plan_result
            
            # Add a note about hypothetical indexes
            index_info = "\n\nNote: Hypothetical indexes are not directly supported in ShenTong.\n"
            index_info += "Suggested indexes to consider:\n"
            
            for idx in hypothetical_indexes:
                table = idx.get('table', 'unknown_table')
                columns = ', '.join(idx.get('columns', []))
                using = idx.get('using', 'btree')
                index_info += f"- {using} index on {table}({columns})\n"
            
            return ExplainPlanArtifact(
                query=sql,
                plan_text=plan_result.plan_text + index_info,
                plan_details={
                    **plan_result.plan_details,
                    "hypothetical_indexes": hypothetical_indexes,
                    "hypothetical_support": "limited"
                }
            )
            
        except Exception as e:
            logger.error(f"Error with hypothetical indexes: {e}")
            return ErrorResult(
                error_message=str(e),
                query=sql
            )