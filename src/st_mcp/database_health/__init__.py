"""Database health monitoring module for ShenTong Database."""

from enum import Enum
from .database_health_tool import DatabaseHealthTool

__all__ = [
    "DatabaseHealthTool",
    "HealthType"
]


class HealthType(Enum):
    """Enumeration of health check types."""
    
    INDEX = "index"
    CONNECTION = "connection"
    VACUUM = "vacuum"
    SEQUENCE = "sequence"
    REPLICATION = "replication"
    BUFFER = "buffer"
    CONSTRAINT = "constraint"
    ALL = "all"