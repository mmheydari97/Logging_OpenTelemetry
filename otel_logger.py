import time
import functools
import json
from datetime import datetime
from typing import Optional, Callable
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource
import logging
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

from log_models import LogData


class StaticLogger:
    """
    Singleton logger class that integrates with OpenTelemetry.
    Provides function decoration for automatic logging with execution timing.
    """
    _instance: Optional['StaticLogger'] = None
    _initialized: bool = False

    def __new__(cls) -> 'StaticLogger':
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not self._initialized:
            self.endpoint = "http://localhost:4317"  # Default OTLP endpoint 
            self._setup_telemetry()
            StaticLogger._initialized = True

    def configure(self, endpoint: str = None):
        """Configure the logger endpoint"""
        if endpoint:
            self.endpoint = endpoint
        self._setup_telemetry()

    def _setup_telemetry(self):
        """Setup OpenTelemetry tracing and logging"""
        resource = Resource.create({
            "service.name": "python-logger",
            "service.version": "1.0.0"
        })
        
        trace.set_tracer_provider(TracerProvider(resource=resource))
        otlp_exporter = OTLPSpanExporter(endpoint=self.endpoint, insecure=True)
        span_processor = BatchSpanProcessor(otlp_exporter)
        trace.get_tracer_provider().add_span_processor(span_processor)
        self.tracer = trace.get_tracer(__name__)
        
        logger_provider = LoggerProvider(resource=resource)
        set_logger_provider(logger_provider)
        
        otlp_log_exporter = OTLPLogExporter(endpoint=self.endpoint, insecure=True)
        logger_provider.add_log_record_processor(BatchLogRecordProcessor(otlp_log_exporter))
        
        handler = LoggingHandler(level=logging.NOTSET, logger_provider=logger_provider)
        logging.getLogger().addHandler(handler)
        logging.getLogger().setLevel(logging.INFO)
        
        self.logger = logging.getLogger(__name__)

    def log_execution(self, level: str = "INFO", include_args: bool = False, include_result: bool = False):
        """
        Decorator that logs function execution with timing and optional args/result
        """
        def decorator(func: Callable) -> Callable:
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                start_time = time.time()
                
                with self.tracer.start_as_current_span(f"{func.__name__}_execution") as span:
                    span.set_attribute("function.name", func.__name__)
                    span.set_attribute("function.module", func.__module__)
                    
                    try:
                        result = func(*args, **kwargs)
                        end_time = time.time()
                        duration_ms = round((end_time - start_time) * 1000, 2)
                        
                        # Create success log data
                        log_data = LogData.create_success_log(
                            function_name=func.__name__,
                            module=func.__module__,
                            duration_ms=duration_ms,
                            message=f"Function completed successfully" + (f" -> {str(result)[:100]}..." if include_result and result is not None else ""),
                            result=str(result) if include_result and result is not None else None,
                            args=str(args) if include_args and args else None,
                            kwargs=kwargs if include_args and kwargs else None,
                            level=level
                        )
                        
                        span.set_attribute("function.duration_ms", duration_ms)
                        span.set_attribute("function.status", "success")
                        
                        # Send the actual message content instead of formatted string
                        self.logger.info(
                            log_data.message, 
                            extra={"otel.log_data": json.dumps(log_data.to_dict())}
                        )
                        return result
                        
                    except Exception as e:
                        end_time = time.time()
                        duration_ms = round((end_time - start_time) * 1000, 2)
                        
                        # Create error log data
                        log_data = LogData.create_error_log(
                            function_name=func.__name__,
                            module=func.__module__,
                            duration_ms=duration_ms,
                            error=str(e),
                            error_type=type(e).__name__,
                            message=f"ERROR: {str(e)}",  # Actual error message that would appear in stderr
                            args=str(args) if include_args and args else None,
                            kwargs=kwargs if include_args and kwargs else None
                        )
                        
                        span.set_attribute("function.duration_ms", duration_ms)
                        span.set_attribute("function.status", "error")
                        span.set_attribute("function.error", str(e))
                        span.record_exception(e)
                        
                        # Send the actual error message instead of formatted string
                        self.logger.error(
                            log_data.message, 
                            extra={"otel.log_data": json.dumps(log_data.to_dict())}
                        )
                        raise
            return wrapper
        return decorator

    def log_custom(self, message: str, level: str = "INFO", **extra_data):
        """
        Method to log custom messages with optional extra data
        """
        log_data = LogData(
            timestamp=datetime.now().isoformat(),
            level=level,
            function_name="custom_log",
            module=__name__,
            duration_ms=0.0,
            status="info",
            message=message,
            **extra_data
        )
        
        if level.upper() == "ERROR":
            self.logger.error(message, extra={"otel.log_data": json.dumps(log_data.to_dict())})
        elif level.upper() == "WARNING":
            self.logger.warning(message, extra={"otel.log_data": json.dumps(log_data.to_dict())})
        else:
            self.logger.info(message, extra={"otel.log_data": json.dumps(log_data.to_dict())})


logger = StaticLogger()
