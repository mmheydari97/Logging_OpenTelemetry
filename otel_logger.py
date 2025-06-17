import time
import functools
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
            self.log_format = "{timestamp} | {level} | {function_name} | {duration_ms}ms | {message}"
            self._setup_telemetry()
            StaticLogger._initialized = True

    def configure(self, endpoint: str = None, log_format: str = None):
        """Configure the logger endpoint and format"""
        if endpoint:
            self.endpoint = endpoint
        if log_format:
            self.log_format = log_format
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
                timestamp = datetime.now().isoformat()
                
                log_data = {
                    "function_name": func.__name__,
                    "module": func.__module__,
                    "timestamp": timestamp,
                    "level": level
                }
                
                if include_args:
                    log_data["args"] = str(args) if args else None
                    log_data["kwargs"] = kwargs if kwargs else None
                
                with self.tracer.start_as_current_span(f"{func.__name__}_execution") as span:
                    span.set_attribute("function.name", func.__name__)
                    span.set_attribute("function.module", func.__module__)
                    
                    try:
                        result = func(*args, **kwargs)
                        end_time = time.time()
                        duration_ms = round((end_time - start_time) * 1000, 2)
                        
                        log_data.update({
                            "duration_ms": duration_ms,
                            "status": "success"
                        })
                        
                        if include_result:
                            log_data["result"] = str(result) if result is not None else None
                        
                        span.set_attribute("function.duration_ms", duration_ms)
                        span.set_attribute("function.status", "success")
                        
                        formatted_message = self.log_format.format(
                            timestamp=timestamp,
                            level=level,
                            function_name=func.__name__,
                            duration_ms=duration_ms, 
                            message="Function executed successfully" 
                        )
                        
                        self.logger.info(formatted_message, extra={"otel.log_data": log_data})
                        return result
                        
                    except Exception as e:
                        end_time = time.time()
                        duration_ms = round((end_time - start_time) * 1000, 2)
                        
                        log_data.update({
                            "duration_ms": duration_ms,
                            "status": "error",
                            "error": str(e), 
                            "error_type": type(e).__name__
                        })
                        
                        span.set_attribute("function.duration_ms", duration_ms)
                        span.set_attribute("function.status", "error")
                        span.set_attribute("function.error", str(e))
                        span.record_exception(e)
                        
                        formatted_message = self.log_format.format(
                            timestamp=timestamp, 
                            level="ERROR", 
                            function_name=func.__name__, 
                            duration_ms=duration_ms, 
                            message=f"Function failed with error: {str(e)}"
                        )
                        
                        self.logger.error(formatted_message, extra={"otel.log_data": log_data})
                        raise
            return wrapper
        return decorator

logger = StaticLogger()
