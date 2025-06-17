from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send
import gzip
import json
import logging
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import uuid
from datetime import datetime
from collections import defaultdict
from google.protobuf.json_format import MessageToDict
from opentelemetry.proto.logs.v1 import logs_pb2
from opentelemetry.proto.collector.logs.v1 import logs_service_pb2


class LogRecord(BaseModel):
    id: str
    timestamp: str
    level: str
    function_name: str
    module: str
    duration_ms: float
    status: str
    message: str
    args: Optional[str] = None
    kwargs: Optional[Dict[str, Any]] = None
    result: Optional[str] = None
    error: Optional[str] = None
    error_type: Optional[str] = None
    raw_data: Dict[str, Any]

class LogStorage:
    def __init__(self):
        self.logs: List[LogRecord] = []
        self.logs_by_function: Dict[str, List[LogRecord]] = defaultdict(list)

    def add_log(self, log_data: Dict[str, Any]) -> str:
        log_id = str(uuid.uuid4())
        
        log_record = LogRecord(
            id=log_id,
            timestamp=log_data.get('timestamp', datetime.utcnow().isoformat()),
            level=log_data.get('level', 'INFO'),
            function_name=log_data.get('function_name', 'unknown'),
            module=log_data.get('module', 'unknown'),
            duration_ms=log_data.get('duration_ms', 0.0),
            status=log_data.get('status', 'unknown'),
            message=log_data.get('message', ''),
            args=log_data.get('args'),
            kwargs=log_data.get('kwargs'),
            result=log_data.get('result'),
            error=log_data.get('error'),
            error_type=log_data.get('error_type'),
            raw_data=log_data
        )
        
        self.logs.append(log_record)
        self.logs_by_function[log_record.function_name].append(log_record)
        
        if len(self.logs) > 1000:
            removed_log = self.logs.pop(0)
            self.logs_by_function[removed_log.function_name].remove(removed_log)
        
        return log_id

    def get_logs(self, limit: int = 100, function_name: Optional[str] = None) -> List[LogRecord]:
        if function_name:
            filtered_logs = self.logs_by_function.get(function_name, [])
        else:
            filtered_logs = self.logs
        return sorted(filtered_logs, key=lambda x: x.timestamp, reverse=True)[:limit]

    def get_log_by_id(self, log_id: str) -> Optional[LogRecord]:
        for log in self.logs:
            if log.id == log_id:
                return log
        return None


# class GzipRequestMiddleware:
#     def __init__(self, app):
#         self.app = app

#     async def __call__(self, scope, receive, send):
#         if scope["type"] == "http":
#             headers = dict(scope["headers"])
#             if b"content-encoding" in headers and headers[b"content-encoding"] == b"gzip":
                
#                 original_receive = receive
                
#                 async def receive_decompressed() -> Message:
#                     message = await original_receive()
                    
#                     if message["type"] == "http.request":
#                         if message.get("body"):
#                             message["body"] = gzip.decompress(message["body"])
                    
#                     return message
                
#                 receive = receive_decompressed

#         await self.app(scope, receive, send)


def extract_attribute_value(attribute):
    """Extract value from OTLP attribute protobuf object"""
    if attribute.value.HasField('string_value'):
        return attribute.value.string_value
    elif attribute.value.HasField('int_value'):
        return attribute.value.int_value
    elif attribute.value.HasField('double_value'):
        return attribute.value.double_value
    elif attribute.value.HasField('bool_value'):
        return attribute.value.bool_value
    elif attribute.value.HasField('bytes_value'):
        return attribute.value.bytes_value.decode('utf-8', errors='ignore')
    elif attribute.value.HasField('array_value'):
        return [extract_attribute_value(item) for item in attribute.value.array_value.values]
    elif attribute.value.HasField('kvlist_value'):
        return {kv.key: extract_attribute_value(kv.value) for kv in attribute.value.kvlist_value.values}
    else:
        return str(attribute.value)


def extract_any_value(any_value):
    """Extract value from OTLP AnyValue protobuf object"""
    if any_value.HasField('string_value'):
        return any_value.string_value
    elif any_value.HasField('int_value'):
        return any_value.int_value
    elif any_value.HasField('double_value'):
        return any_value.double_value
    elif any_value.HasField('bool_value'):
        return any_value.bool_value
    elif any_value.HasField('bytes_value'):
        return any_value.bytes_value.decode('utf-8', errors='ignore')
    elif any_value.HasField('array_value'):
        return [extract_any_value(item) for item in any_value.array_value.values]
    elif any_value.HasField('kvlist_value'):
        return {kv.key: extract_any_value(kv.value) for kv in any_value.kvlist_value.values}
    else:
        return str(any_value)


def parse_protobuf_logs(protobuf_data: bytes) -> List[Dict[str, Any]]:
    """Parse OTLP protobuf logs data and return list of log dictionaries"""
    try:
        # Parse the ExportLogsServiceRequest
        export_request = logs_service_pb2.ExportLogsServiceRequest()
        export_request.ParseFromString(protobuf_data)
        
        parsed_logs = []
        
        for resource_logs in export_request.resource_logs:
            # Extract resource attributes
            resource_attributes = {}
            if resource_logs.resource:
                for attr in resource_logs.resource.attributes:
                    resource_attributes[attr.key] = extract_attribute_value(attr)
            
            for scope_logs in resource_logs.scope_logs:
                # Extract scope attributes
                scope_attributes = {}
                if scope_logs.scope:
                    for attr in scope_logs.scope.attributes:
                        scope_attributes[attr.key] = extract_attribute_value(attr)
                
                for log_record in scope_logs.log_records:
                    # Extract log record attributes
                    log_attributes = {}
                    for attr in log_record.attributes:
                        log_attributes[attr.key] = extract_attribute_value(attr)
                    
                    # Extract log body
                    body = ""
                    if log_record.body:
                        body = extract_any_value(log_record.body)
                    
                    # Convert timestamp from nanoseconds to ISO format
                    timestamp = datetime.fromtimestamp(log_record.time_unix_nano / 1_000_000_000).isoformat() if log_record.time_unix_nano else datetime.utcnow().isoformat()
                    
                    # Try to extract structured data from otel.log_data attribute
                    otel_log_data = log_attributes.get('otel.log_data', {})
                    if isinstance(otel_log_data, str):
                        try:
                            otel_log_data = json.loads(otel_log_data)
                        except json.JSONDecodeError:
                            otel_log_data = {}
                    
                    log_data = {
                        'timestamp': timestamp,
                        'level': log_record.severity_text or otel_log_data.get('level', 'INFO'),
                        'function_name': otel_log_data.get('function_name', 'unknown'),
                        'module': otel_log_data.get('module', 'unknown'),
                        'duration_ms': otel_log_data.get('duration_ms', 0.0),
                        'status': otel_log_data.get('status', 'unknown'),
                        'message': body,
                        'args': otel_log_data.get('args'),
                        'kwargs': otel_log_data.get('kwargs'),
                        'result': otel_log_data.get('result'),
                        'error': otel_log_data.get('error'),
                        'error_type': otel_log_data.get('error_type'),
                        'severity_number': log_record.severity_number,
                        'severity_text': log_record.severity_text,
                        'resource_attributes': resource_attributes,
                        'scope_attributes': scope_attributes,
                        'log_attributes': log_attributes,
                        'trace_id': log_record.trace_id.hex() if log_record.trace_id else None,
                        'span_id': log_record.span_id.hex() if log_record.span_id else None,
                    }
                    
                    parsed_logs.append(log_data)
        
        return parsed_logs
    
    except Exception as e:
        raise ValueError(f"Failed to parse protobuf data: {str(e)}")


logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = FastAPI(title="OpenTelemetry Log Viewer")
# app.add_middleware(GzipRequestMiddleware)
log_storage = LogStorage()

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})


# @app.post("/api/logs")
# async def receive_log(request: Request):
#     """
#     Receives a batch of logs in OTLP protobuf format, parses them,
#     and adds them to the in-memory log storage.
#     Handles gzip compressed payloads properly.
#     """
#     # Get the raw body (already decompressed by middleware if gzipped)
#     body = await request.body()
    
#     # Check content type to determine format
#     content_type = request.headers.get("content-type", "").lower()
    
#     logs_added = 0
    
#     try:
#         if "application/x-protobuf" in content_type or "application/octet-stream" in content_type:
#             # Handle protobuf format
#             parsed_logs = parse_protobuf_logs(body)
#             for log_data in parsed_logs:
#                 log_storage.add_log(log_data)
#                 logs_added += 1
                
#         elif "application/json" in content_type:
#             # Handle JSON format (fallback for existing functionality)
#             try:
#                 payload = json.loads(body.decode('utf-8'))
#             except UnicodeDecodeError:
#                 payload = json.loads(body.decode('utf-8', errors='replace'))
            
#             # Parse JSON format (your existing logic)
#             if "resourceLogs" in payload:
#                 for resource_log in payload.get("resourceLogs", []):
#                     resource_attributes = {}
#                     for attr in resource_log.get("resource", {}).get("attributes", []):
#                         key = attr.get("key")
#                         value = list(attr.get("value", {}).values())[0]
#                         resource_attributes[key] = value
                        
#                     for scope_log in resource_log.get("scopeLogs", []):
#                         scope_attributes = {}
#                         for attr in scope_log.get("scope", {}).get("attributes", []):
#                             key = attr.get("key")
#                             value = list(attr.get("value", {}).values())[0]
#                             scope_attributes[key] = value
                        
#                         for log_record in scope_log.get("logRecords", []):
#                             log_data = {}
                            
#                             log_data["timestamp"] = log_record.get("timeUnixNano")
#                             log_data["severity_number"] = log_record.get("severityNumber")
#                             log_data["severity_text"] = log_record.get("severityText")
#                             log_data["body"] = list(log_record.get("body", {}).get("kvlistValue", {}).get("values", [{}])[0].get("value", {}).values())[0] if log_record.get("body") else ""
                            
#                             attributes = {}
#                             for attribute in log_record.get("attributes", []):
#                                 key = attribute.get("key")
#                                 value = list(attribute.get("value", {}).values())[0]
#                                 attributes[key] = value
                            
#                             log_data.update({
#                                 "resource_attributes": resource_attributes,
#                                 "scope_attributes": scope_attributes,
#                                 "attributes": attributes
#                             })
                            
#                             log_storage.add_log(log_data)
#                             logs_added += 1
#         else:
#             # Try protobuf as default since OTLP uses protobuf by default
#             try:
#                 parsed_logs = parse_protobuf_logs(body)
#                 for log_data in parsed_logs:
#                     log_storage.add_log(log_data)
#                     logs_added += 1
#             except ValueError:
#                 raise HTTPException(
#                     status_code=400, 
#                     detail=f"Unsupported content type: {content_type}. Expected application/x-protobuf or application/json"
#                 )
    
#     except json.JSONDecodeError as e:
#         raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")
#     except ValueError as e:
#         raise HTTPException(status_code=400, detail=str(e))
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

#     return {"status": "success", "logs_added": logs_added}

# @app.get("/api/logs", response_model=List[LogRecord])
# async def get_logs_api(limit: int = 100, function_name: Optional[str] = None):
#     return log_storage.get_logs(limit=limit, function_name=function_name)

def protobuf_to_dict(protobuf_message):
    """Convert protobuf message to dictionary for easy inspection"""
    try:
        return MessageToDict(protobuf_message, preserving_proto_field_name=True)
    except Exception as e:
        logger.error(f"Failed to convert protobuf to dict: {e}")
        return {"error": f"Conversion failed: {str(e)}"}

@app.post("/api/logs")
async def decode_protobuf_logs(request: Request):
    """
    Decode protobuf logs and return the raw structure for inspection
    """
    try:
        # Get headers info
        content_type = request.headers.get("content-type", "")
        content_encoding = request.headers.get("content-encoding", "")
        logger.info(f"Content-Type: {content_type}, Content-Encoding: {content_encoding}")
        
        # Get raw body
        body = await request.body()
        logger.info(f"Received {len(body)} bytes")
        
        # Decompress if gzipped
        if content_encoding == "gzip":
            try:
                body = gzip.decompress(body)
                logger.info(f"Decompressed to {len(body)} bytes")
            except Exception as e:
                logger.error(f"Gzip decompression failed: {e}")
                raise HTTPException(status_code=400, detail=f"Gzip decompression failed: {str(e)}")
        
        # Try to parse as OTLP protobuf
        try:
            export_request = logs_service_pb2.ExportLogsServiceRequest()
            export_request.ParseFromString(body)
            logger.info("Successfully parsed protobuf!")
            
            # Convert to dictionary for inspection
            protobuf_dict = protobuf_to_dict(export_request)
            
            # Log the structure (truncated for readability)
            logger.info("=== PROTOBUF STRUCTURE ===")
            logger.info(json.dumps(protobuf_dict, indent=2, default=str)[:2000] + "...")
            
            # Count resource logs and log records
            resource_logs_count = len(export_request.resource_logs)
            total_log_records = sum(
                len(scope_logs.log_records) 
                for resource_logs in export_request.resource_logs
                for scope_logs in resource_logs.scope_logs
            )
            
            logger.info(f"Found {resource_logs_count} resource logs with {total_log_records} total log records")
            
            # Return the parsed structure for inspection
            return {
                "status": "success",
                "message": "Protobuf decoded successfully",
                "resource_logs_count": resource_logs_count,
                "total_log_records": total_log_records,
                "raw_protobuf_structure": protobuf_dict
            }
            
        except Exception as e:
            logger.error(f"Protobuf parsing failed: {e}")
            logger.error(f"First 100 bytes as hex: {body[:100].hex()}")
            raise HTTPException(status_code=400, detail=f"Protobuf parsing failed: {str(e)}")
            
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@app.get("/api/logs")
async def get_debug_info():
    return {
        "message": "Protobuf debugging endpoint",
        "note": "Check server logs for detailed protobuf structure when POST requests are made"
    }


@app.get("/api/logs/{log_id}", response_model=LogRecord)
async def get_log_detail_api(log_id: str):
    log = log_storage.get_log_by_id(log_id)
    if not log:
        raise HTTPException(status_code=404, detail="Log not found")
    return log

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level='debug')
