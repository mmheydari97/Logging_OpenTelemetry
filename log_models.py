from pydantic import BaseModel
from typing import Dict, Any, Optional
from datetime import datetime
import uuid


class LogData(BaseModel):
    """
    Base log data model used for creating and transmitting log entries.
    This is the standard format that both the logger and viewer understand.
    """
    timestamp: str
    level: str
    function_name: str
    module: str
    duration_ms: float
    status: str  # 'success', 'error', 'running', etc.
    message: str  # Actual stdout/stderr content or result message
    args: Optional[str] = None
    kwargs: Optional[Dict[str, Any]] = None
    result: Optional[str] = None
    error: Optional[str] = None
    error_type: Optional[str] = None
    
    # OpenTelemetry specific fields
    severity_number: Optional[str] = None  # Keep as string (e.g., 'SEVERITY_NUMBER_INFO')
    severity_text: Optional[str] = None
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    
    # Additional metadata
    resource_attributes: Optional[Dict[str, Any]] = None
    log_attributes: Optional[Dict[str, Any]] = None
    scope_name: Optional[str] = None

    @classmethod
    def create_success_log(
        cls,
        function_name: str,
        module: str,
        duration_ms: float,
        message: str = None,
        result: str = None,
        args: str = None,
        kwargs: Dict[str, Any] = None,
        level: str = "INFO"
    ) -> "LogData":
        """Helper method to create a success log entry"""
        return cls(
            timestamp=datetime.now().isoformat(),
            level=level,
            function_name=function_name,
            module=module,
            duration_ms=duration_ms,
            status="success",
            message=message or f"Function '{function_name}' executed successfully",
            args=args,
            kwargs=kwargs,
            result=result
        )
    
    @classmethod
    def create_error_log(
        cls,
        function_name: str,
        module: str,
        duration_ms: float,
        error: str,
        error_type: str,
        message: str = None,
        args: str = None,
        kwargs: Dict[str, Any] = None
    ) -> "LogData":
        """Helper method to create an error log entry"""
        return cls(
            timestamp=datetime.now().isoformat(),
            level="ERROR",
            function_name=function_name,
            module=module,
            duration_ms=duration_ms,
            status="error",
            message=message or f"Function '{function_name}' failed: {error}",
            args=args,
            kwargs=kwargs,
            error=error,
            error_type=error_type
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return self.model_dump(exclude_none=True)


class LogRecord(LogData):
    """
    Extended log record for storage and display in the viewer.
    Includes additional fields needed for the dashboard.
    """
    id: str
    raw_data: Dict[str, Any]
    
    def __init__(self, log_data: LogData, log_id: str = None, raw_data: Dict[str, Any] = None, **kwargs):
        # Convert LogData to dict and merge with additional fields
        data = log_data.model_dump() if isinstance(log_data, LogData) else log_data
        data.update(kwargs)
        
        super().__init__(
            id=log_id or str(uuid.uuid4()),
            raw_data=raw_data or data,
            **data
        )
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any], log_id: str = None) -> "LogRecord":
        """Create LogRecord from dictionary data"""
        return cls(
            log_data=data,
            log_id=log_id or str(uuid.uuid4()),
            raw_data=data
        )
    
    def get_preview_fields(self) -> Dict[str, Any]:
        """Get the main preview fields for dashboard display"""
        return {
            "timestamp": self.timestamp,
            "level": self.level,
            "function_name": self.function_name,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "message": self.message
        }
    