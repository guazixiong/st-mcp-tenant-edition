"""Data classes for representing explain plan artifacts."""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class ExplainPlanArtifact:
    """Represents an execution plan for a SQL query."""
    
    query: str
    plan_text: str
    plan_details: Dict[str, Any]
    
    def to_text(self) -> str:
        """Convert the execution plan to a human-readable text format."""
        return f"Query: {self.query}\n\nExecution Plan:\n{self.plan_text}"


@dataclass
class ErrorResult:
    """Represents an error that occurred during plan generation."""
    
    error_message: str
    query: Optional[str] = None
    
    def to_text(self) -> str:
        """Convert the error to a human-readable text format."""
        if self.query:
            return f"Error executing query: {self.error_message}\n\nQuery: {self.query}"
        return f"Error: {self.error_message}"