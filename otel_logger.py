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
        pass

    def configure(self, endpoint: str = None):
        """Configure the logger endpoint"""
        if not self._initialized:
            self.endpoint = endpoint if endpoint else "http://localhost:4317"  # Default OTLP endpoint 
            self._setup_telemetry()
            StaticLogger._initialized = True

    def _setup_telemetry(self):
        """Setup OpenTelemetry tracing and logging with console output"""
        resource = Resource.create({
            "service.name": "python-logger",
            "service.version": "1.0.0"
        })
        
        # Setup tracing
        trace.set_tracer_provider(TracerProvider(resource=resource))
        otlp_exporter = OTLPSpanExporter(endpoint=self.endpoint, insecure=True)
        span_processor = BatchSpanProcessor(otlp_exporter)
        trace.get_tracer_provider().add_span_processor(span_processor)
        self.tracer = trace.get_tracer(__name__)
        
        # Setup OpenTelemetry logging
        logger_provider = LoggerProvider(resource=resource)
        set_logger_provider(logger_provider)
        
        otlp_log_exporter = OTLPLogExporter(endpoint=self.endpoint, insecure=True)
        logger_provider.add_log_record_processor(BatchLogRecordProcessor(otlp_log_exporter))
        
        # Create OpenTelemetry handler
        otel_handler = LoggingHandler(level=logging.NOTSET, logger_provider=logger_provider)
        
        # Create console handler for local output
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        
        # Create a formatter for console output
        console_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        console_handler.setFormatter(console_formatter)
        
        # Configure the root logger
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        
        # Clear any existing handlers to avoid duplicates
        root_logger.handlers.clear()
        
        # Add both handlers
        root_logger.addHandler(otel_handler)  # For OpenTelemetry
        root_logger.addHandler(console_handler)  # For console output
        
        # Get our specific logger instance
        self.logger = logging.getLogger(__name__)

    def log_execution(self, level: str = "INFO", include_args: bool = False, include_result: bool = False):
        """
        Decorator that logs function execution with timing and optional args/result
        """
        def decorator(func: Callable) -> Callable:
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                if not self._initialized:
                    logging.warning(
                        f"Logger is not configured. Function '{func.__name__}' execution will not be logged by OpenTelemetry. Call logger.configure() first."
                    )
                    return func(*args, **kwargs) # Still execute the function

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
                        
                        # Create a console-friendly message
                        console_message = f"{func.__name__}() executed successfully in {duration_ms}ms"
                        if include_args and (args or kwargs):
                            args_str = f"args={args}" if args else ""
                            kwargs_str = f"kwargs={kwargs}" if kwargs else ""
                            args_info = ", ".join(filter(None, [args_str, kwargs_str]))
                            console_message += f" [{args_info}]"
                        if include_result and result is not None:
                            console_message += f" -> {str(result)[:100]}{'...' if len(str(result)) > 100 else ''}"
                        
                        # Log with both the detailed data for OpenTelemetry and readable message for console
                        if level.upper() == "WARNING":
                            self.logger.warning(
                                console_message, 
                                extra={"otel.log_data": json.dumps(log_data.to_dict())}
                            )
                        else:
                            self.logger.info(
                                console_message, 
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
                            message=f"ERROR: {str(e)}",
                            args=str(args) if include_args and args else None,
                            kwargs=kwargs if include_args and kwargs else None
                        )
                        
                        span.set_attribute("function.duration_ms", duration_ms)
                        span.set_attribute("function.status", "error")
                        span.set_attribute("function.error", str(e))
                        span.record_exception(e)
                        
                        # Create console-friendly error message
                        console_message = f"{func.__name__}() failed in {duration_ms}ms: {type(e).__name__}: {str(e)}"
                        if include_args and (args or kwargs):
                            args_str = f"args={args}" if args else ""
                            kwargs_str = f"kwargs={kwargs}" if kwargs else ""
                            args_info = ", ".join(filter(None, [args_str, kwargs_str]))
                            console_message += f" [{args_info}]"
                        
                        # Log error with both detailed data and readable message
                        self.logger.error(
                            console_message, 
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
        
        # The message will appear on console as-is, plus the structured data goes to OpenTelemetry
        if level.upper() == "ERROR":
            self.logger.error(message, extra={"otel.log_data": json.dumps(log_data.to_dict())})
        elif level.upper() == "WARNING":
            self.logger.warning(message, extra={"otel.log_data": json.dumps(log_data.to_dict())})
        else:
            self.logger.info(message, extra={"otel.log_data": json.dumps(log_data.to_dict())})


logger = StaticLogger()
