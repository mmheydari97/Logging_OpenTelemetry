import time
import functools
import json
import logging
import inspect
import os
from datetime import datetime
from typing import Optional, Callable
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

from log_models import LogData


class StaticLogger:
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
            self.endpoint = endpoint if endpoint else "http://localhost:4317"
            self._setup_telemetry()
            StaticLogger._initialized = True

    def _setup_telemetry(self):
        """Setup OpenTelemetry tracing and logging with console output"""
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

        otel_handler = LoggingHandler(level=logging.NOTSET, logger_provider=logger_provider)

        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)

        console_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        console_handler.setFormatter(console_formatter)

        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        root_logger.handlers.clear()
        root_logger.addHandler(otel_handler)
        root_logger.addHandler(console_handler)

        self.logger = logging.getLogger(__name__)

    def log_execution(self, level: str = "INFO", include_args: bool = False, include_result: bool = False):
        """
        Decorator that logs function execution with timing and optional args/result.
        Ensures function execution and duration tracking are robust,
        with separate error handling for OpenTelemetry logging/tracing.
        """
        level = level.upper()
        def decorator(func: Callable) -> Callable:
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                start_time = time.time()
                result = None
                exception_occurred = None

                # --- 1. Execute the wrapped function and track its duration ---
                try:
                    result = func(*args, **kwargs)
                except Exception as e:
                    exception_occurred = e
                finally:
                    end_time = time.time()
                    duration_ms = round((end_time - start_time) * 1000, 2)

                # --- 2. Attempt OpenTelemetry logging/tracing (robustly) ---
                try:
                    if not self._initialized:
                        logging.warning(
                            f"Logger is not configured. Function '{func.__name__}' execution details will not be sent to OpenTelemetry. Call logger.configure() first."
                        )
                        # Still print a basic console message for completion or failure
                        if exception_occurred:
                            logging.error(f"{func.__name__}() failed in {duration_ms}ms: {type(exception_occurred).__name__}: {str(exception_occurred)}")
                        else:
                            logging.info(f"{func.__name__}() executed successfully in {duration_ms}ms")
                        if exception_occurred:
                            raise exception_occurred # Re-raise original function's exception
                        return result

                    # If initialized, proceed with OpenTelemetry operations
                    with self.tracer.start_as_current_span(f"{func.__name__}_execution") as span:
                        span.set_attribute("function.name", func.__name__)
                        span.set_attribute("function.module", func.__module__)
                        span.set_attribute("function.duration_ms", duration_ms)

                        if exception_occurred:
                            span.set_attribute("function.status", "error")
                            span.set_attribute("function.error", str(exception_occurred))
                            span.record_exception(exception_occurred)

                            log_data = LogData.create_error_log(
                                function_name=func.__name__,
                                module=func.__module__,
                                duration_ms=duration_ms,
                                error=str(exception_occurred),
                                error_type=type(exception_occurred).__name__,
                                message=f"ERROR: {str(exception_occurred)}",
                                args=str(args) if include_args and args else None,
                                kwargs=kwargs if include_args and kwargs else None
                            )
                            console_message = f"{func.__name__}() failed in {duration_ms}ms: {type(exception_occurred).__name__}: {str(exception_occurred)}"
                            if include_args and (args or kwargs):
                                args_str = f"args={args}" if args else ""
                                kwargs_str = f"kwargs={kwargs}" if kwargs else ""
                                args_info = ", ".join(filter(None, [args_str, kwargs_str]))
                                console_message += f" [{args_info}]"
                            self.logger.error(
                                console_message,
                                extra={"otel.log_data": json.dumps(log_data.to_dict())}
                            )
                        else:
                            span.set_attribute("function.status", "success")
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
                            console_message = f"{func.__name__}() executed successfully in {duration_ms}ms"
                            if include_args and (args or kwargs):
                                args_str = f"args={args}" if args else ""
                                kwargs_str = f"kwargs={kwargs}" if kwargs else ""
                                args_info = ", ".join(filter(None, [args_str, kwargs_str]))
                                console_message += f" [{args_info}]"
                            if include_result and result is not None:
                                console_message += f" -> {str(result)[:100]}{'...' if len(str(result)) > 100 else ''}"

                            if level == "WARNING":
                                self.logger.warning(
                                    console_message,
                                    extra={"otel.log_data": json.dumps(log_data.to_dict())}
                                )
                            else:
                                self.logger.info(
                                    console_message,
                                    extra={"otel.log_data": json.dumps(log_data.to_dict())}
                                )

                except Exception as e_decorator:
                    # Catch any exceptions occurring within the decorator's logging/tracing logic
                    self._handle_decorator_failure(
                        f"An error occurred within the logger decorator for function '{func.__name__}' during OpenTelemetry operations.",
                        e_decorator
                    )

                # --- 3. Re-raise original function's exception if it occurred ---
                if exception_occurred:
                    raise exception_occurred
                return result

            return wrapper
        return decorator

    def _handle_decorator_failure(self, message: str, exception: Exception):
        """Helper to log decorator-specific failures without disrupting main logic."""
        # Use standard logging directly here to avoid circular dependencies if self.logger fails
        logging.error(f"{message} Error: {type(exception).__name__}: {str(exception)}", exc_info=True)


    def log_custom(self, message: str, level: str = "INFO", auto_locate: bool = False):
        """
        Method to log custom messages with optional extra data.
        If auto_locate is True, appends file, function, and line information to the message.
        Robustly handles OpenTelemetry logging even if not fully configured or on error.
        """
        display_message = message # Keep original message for log_data
        level = level.upper()
        try:
            # If auto_locate is True, get caller information
            if auto_locate:
                frames = inspect.stack()
                frames = frames[1:5] if len(frames) > 1 else None
                location_info = "\n".join(
                    f"{os.path.basename(frame.filename)}: {frame.lineno} -> {frame.function}"
                    for frame in frames
                )
                display_message = f"{message} (Called from:\n{location_info})"

            if not self._initialized:
                logging.warning(
                    f"Logger is not configured. Custom log message will not be sent to OpenTelemetry. Call logger.configure() first."
                )
                if level == "ERROR":
                    logging.error(display_message)
                elif level == "WARNING":
                    logging.warning(display_message)
                else:
                    logging.info(display_message)
                return

            log_data = LogData(
                timestamp=datetime.now().isoformat(),
                level=level,
                function_name="custom_log", # This function's name
                module=__name__, # This module
                duration_ms=0.0,
                status="info",
                message=display_message,
            )
            
            if level == "ERROR":
                self.logger.error(display_message, extra={"otel.log_data": json.dumps(log_data.to_dict())})
            elif level == "WARNING":
                self.logger.warning(display_message, extra={"otel.log_data": json.dumps(log_data.to_dict())})
            else:
                self.logger.info(display_message, extra={"otel.log_data": json.dumps(log_data.to_dict())})

        except Exception as e_custom_log:
            self._handle_decorator_failure(
                f"An error occurred while attempting to send a custom log: '{display_message}'.",
                e_custom_log
            )

logger = StaticLogger()
