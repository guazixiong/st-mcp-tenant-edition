"""Index analyzer for ShenTong Database."""

import logging
from typing import Dict, List, Any, Optional
from st_mcp.sql.sql_driver import SqlDriver

logger = logging.getLogger(__name__)


class IndexAnalyzer:
    """Tool to analyze indexes and recommend optimizations for ShenTong Database."""
    
    def __init__(self, sql_driver: SqlDriver):
        self.sql_driver = sql_driver
    
    async def analyze_indexes(self) -> Dict[str, Any]:
        """Analyze all indexes in the database."""
        try:
            # Get all indexes
            indexes = await self.get_all_indexes()
            
            # Check for potential issues
            duplicate_indexes = await self.find_duplicate_indexes()
            unused_indexes = await self.find_unused_indexes()
            missing_indexes = await self.suggest_missing_indexes()
            
            return {
                "total_indexes": len(indexes),
                "indexes": indexes,
                "issues": {
                    "duplicate_indexes": duplicate_indexes,
                    "unused_indexes": unused_indexes,
                    "missing_indexes": missing_indexes
                },
                "recommendations": self._generate_recommendations(duplicate_indexes, unused_indexes, missing_indexes)
            }
            
        except Exception as e:
            logger.error(f"Error analyzing indexes: {e}")
            return {
                "error": str(e),
                "message": "Failed to analyze indexes"
            }
    
    async def analyze_query_indexes(self, queries: List[str], max_index_size_mb: int = 10000) -> Dict[str, Any]:
        """Analyze a list of queries and recommend optimal indexes."""
        try:
            recommendations = []
            
            for sql in queries:
                # Analyze each query for potential index opportunities
                query_recommendations = await self._analyze_single_query_for_indexes(sql)
                if query_recommendations:
                    recommendations.extend(query_recommendations)
            
            # Deduplicate recommendations
            unique_recommendations = self._deduplicate_recommendations(recommendations)
            
            return {
                "query_count": len(queries),
                "recommendations": unique_recommendations,
                "total_recommendations": len(unique_recommendations)
            }
            
        except Exception as e:
            logger.error(f"Error analyzing query indexes: {e}")
            return {
                "error": str(e),
                "message": "Failed to analyze query indexes"
            }
    
    async def get_all_indexes(self) -> List[Dict[str, Any]]:
        """Get all indexes in the database."""
        try:
            # Query to get all indexes in ShenTong
            query = """
            SELECT 
                owner as schema_name,
                table_name,
                index_name,
                uniqueness,
                status
            FROM 
                dba_indexes
            WHERE 
                owner NOT IN ('SYS', 'SYSTEM', 'DBSYS')
            ORDER BY 
                owner, table_name, index_name
            """
            
            rows = await self.sql_driver.execute_query(query)
            
            indexes = []
            if rows:
                for row in rows:
                    # Get index columns for this index
                    columns = await self._get_index_columns(row.cells.get("schema_name"), 
                                                          row.cells.get("index_name"))
                    
                    indexes.append({
                        "schema_name": row.cells.get("schema_name"),
                        "table_name": row.cells.get("table_name"),
                        "index_name": row.cells.get("index_name"),
                        "unique": row.cells.get("uniqueness") == "UNIQUE",
                        "status": row.cells.get("status"),
                        "columns": columns
                    })
            
            return indexes
            
        except Exception as e:
            logger.error(f"Error getting all indexes: {e}")
            # Fallback to a simpler query if dba_indexes is not available
            try:
                simple_query = """
                SELECT 
                    owner as schema_name,
                    table_name,
                    index_name
                FROM 
                    user_indexes
                ORDER BY 
                    owner, table_name, index_name
                """
                rows = await self.sql_driver.execute_query(simple_query)
                
                simple_indexes = []
                if rows:
                    for row in rows:
                        simple_indexes.append({
                            "schema_name": row.cells.get("schema_name"),
                            "table_name": row.cells.get("table_name"),
                            "index_name": row.cells.get("index_name"),
                            "columns": []
                        })
                
                return simple_indexes
            except Exception as inner_e:
                logger.error(f"Fallback query also failed: {inner_e}")
                return []
    
    async def _get_index_columns(self, schema_name: str, index_name: str) -> List[str]:
        """Get the columns of a specific index."""
        try:
            query = """
            SELECT 
                column_name
            FROM 
                dba_ind_columns
            WHERE 
                index_owner = :schema_name
                AND index_name = :index_name
            ORDER BY 
                column_position
            """
            
            # Replace placeholders for ShenTong
            query = query.replace(":schema_name", f"'{schema_name}'")
            query = query.replace(":index_name", f"'{index_name}'")
            
            rows = await self.sql_driver.execute_query(query)
            
            columns = []
            if rows:
                for row in rows:
                    columns.append(row.cells.get("column_name"))
            
            return columns
            
        except Exception:
            # Return empty list if we can't get the columns
            return []
    
    async def find_duplicate_indexes(self) -> List[Dict[str, Any]]:
        """Find potentially duplicate indexes."""
        try:
            # Get all indexes with their columns
            all_indexes = await self.get_all_indexes()
            
            # Group indexes by table and column sets
            table_index_map = {}
            duplicates = []
            
            for idx in all_indexes:
                key = f"{idx['schema_name']}.{idx['table_name']}"
                if key not in table_index_map:
                    table_index_map[key] = []
                table_index_map[key].append(idx)
            
            # Check for duplicates within each table's indexes
            for table_key, indexes in table_index_map.items():
                for i, idx1 in enumerate(indexes):
                    for idx2 in indexes[i+1:]:
                        # Check if indexes have the same columns in the same order
                        if idx1['columns'] == idx2['columns'] and idx1['unique'] == idx2['unique']:
                            duplicates.append({
                                "schema_name": idx1['schema_name'],
                                "table_name": idx1['table_name'],
                                "index1": idx1['index_name'],
                                "index2": idx2['index_name'],
                                "columns": idx1['columns']
                            })
            
            return duplicates
            
        except Exception as e:
            logger.error(f"Error finding duplicate indexes: {e}")
            return []
    
    async def find_unused_indexes(self) -> List[Dict[str, Any]]:
        """Find potentially unused indexes."""
        try:
            # Note: ShenTong might not track index usage statistics directly
            # This is a best-effort implementation
            
            # Try to get some usage information if available
            query = """
            SELECT 
                owner as schema_name,
                table_name,
                index_name
            FROM 
                user_indexes
            WHERE 
                1=0  -- Placeholder, as usage tracking might not be available
            """
            
            # Return a message about limited tracking capabilities
            return [{
                "message": "ShenTong may not track index usage statistics by default. Consider enabling auditing to monitor index usage."
            }]
            
        except Exception as e:
            logger.error(f"Error finding unused indexes: {e}")
            return []
    
    async def suggest_missing_indexes(self) -> List[Dict[str, Any]]:
        """Suggest potentially missing indexes based on table statistics."""
        try:
            # Get tables with large row counts that might benefit from indexes
            query = """
            SELECT 
                owner as schema_name,
                table_name,
                num_rows
            FROM 
                user_tables
            WHERE 
                num_rows > 10000
            ORDER BY 
                num_rows DESC
            FETCH FIRST 20 ROWS ONLY
            """
            
            rows = await self.sql_driver.execute_query(query)
            
            suggestions = []
            if rows:
                for row in rows:
                    # For large tables, suggest checking for indexes on frequently queried columns
                    suggestions.append({
                        "schema_name": row.cells.get("schema_name"),
                        "table_name": row.cells.get("table_name"),
                        "row_count": row.cells.get("num_rows"),
                        "suggestion": f"Table has {row.cells.get('num_rows')} rows. Consider creating indexes on frequently queried columns."
                    })
            
            return suggestions
            
        except Exception as e:
            logger.error(f"Error suggesting missing indexes: {e}")
            return []
    
    async def _analyze_single_query_for_indexes(self, sql: str) -> List[Dict[str, Any]]:
        """Analyze a single query for potential index opportunities."""
        try:
            # Simple parsing to find potential columns for indexing
            # This is a basic implementation that can be enhanced
            sql_lower = sql.lower()
            
            # Look for WHERE clauses
            if "where" in sql_lower:
                where_clause = sql_lower.split("where")[1]
                
                # Extract potential columns from WHERE clause
                # This is a very simplistic approach
                potential_columns = []
                
                # Check for common comparison operators
                operators = ["=", ">", "<", ">=", "<=", "like", "in"]
                for op in operators:
                    if op in where_clause:
                        parts = where_clause.split(op)
                        for part in parts:
                            # Try to find column names (simplistic approach)
                            tokens = part.strip().split()
                            if tokens and len(tokens[0]) > 0 and tokens[0].isalnum():
                                potential_columns.append(tokens[0])
                
                # Deduplicate
                potential_columns = list(set(potential_columns))
                
                # Create suggestions based on potential columns
                suggestions = []
                for col in potential_columns[:5]:  # Limit to top 5 to avoid too many suggestions
                    suggestions.append({
                        "query": sql[:100] + ("..." if len(sql) > 100 else ""),
                        "column": col,
                        "suggestion": f"Consider indexing column {col} for this query"
                    })
                
                return suggestions
            
            return []
            
        except Exception as e:
            logger.error(f"Error analyzing query for indexes: {e}")
            return []
    
    def _deduplicate_recommendations(self, recommendations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Remove duplicate index recommendations."""
        seen = set()
        unique = []
        
        for rec in recommendations:
            # Create a key based on query snippet and column
            key = f"{rec.get('query', '')}|{rec.get('column', '')}"
            if key not in seen:
                seen.add(key)
                unique.append(rec)
        
        return unique
    
    def _generate_recommendations(self, duplicates: List[Dict[str, Any]], 
                                 unused: List[Dict[str, Any]], 
                                 missing: List[Dict[str, Any]]) -> List[str]:
        """Generate human-readable recommendations based on analysis."""
        recommendations = []
        
        if duplicates:
            recommendations.append(f"Found {len(duplicates)} potential duplicate index pairs. Consider removing redundant indexes to reduce overhead.")
        
        if unused:
            recommendations.append("Consider enabling index usage tracking to identify unused indexes that can be safely removed.")
        
        if missing:
            recommendations.append(f"Found {len(missing)} tables that might benefit from additional indexing. Review the detailed suggestions.")
        
        if not recommendations:
            recommendations.append("No major index issues detected. Regular index maintenance is still recommended.")
        
        return recommendations