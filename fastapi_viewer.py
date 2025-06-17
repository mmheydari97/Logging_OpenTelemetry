from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import gzip
import json
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional
import uuid
from collections import defaultdict
from pydantic import BaseModel

from google.protobuf.json_format import MessageToDict
from opentelemetry.proto.collector.logs.v1 import logs_service_pb2

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
            timestamp=log_data.get('timestamp', datetime.now().isoformat()),
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
        
        # Keep only last 1000 logs
        if len(self.logs) > 1000:
            removed_log = self.logs.pop(0)
            if removed_log.function_name in self.logs_by_function:
                if removed_log in self.logs_by_function[removed_log.function_name]:
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

def extract_attribute_value(attr_dict):
    """Extract value from OTLP attribute dictionary"""
    value_dict = attr_dict.get('value', {})
    
    if 'string_value' in value_dict:
        return value_dict['string_value']
    elif 'int_value' in value_dict:
        return int(value_dict['int_value'])
    elif 'double_value' in value_dict:
        return float(value_dict['double_value'])
    elif 'bool_value' in value_dict:
        return value_dict['bool_value']
    elif 'bytes_value' in value_dict:
        return value_dict['bytes_value']
    else:
        return str(value_dict)

def extract_body_value(body_dict):
    """Extract value from OTLP body dictionary"""
    if 'string_value' in body_dict:
        return body_dict['string_value']
    elif 'int_value' in body_dict:
        return str(body_dict['int_value'])
    elif 'double_value' in body_dict:
        return str(body_dict['double_value'])
    elif 'bool_value' in body_dict:
        return str(body_dict['bool_value'])
    else:
        return str(body_dict)

def parse_protobuf_logs(protobuf_data: bytes) -> List[Dict[str, Any]]:
    """Parse OTLP protobuf logs data and return list of log dictionaries"""
    try:
        # Parse the ExportLogsServiceRequest
        export_request = logs_service_pb2.ExportLogsServiceRequest()
        export_request.ParseFromString(protobuf_data)
        
        # Convert to dictionary for easier processing
        protobuf_dict = MessageToDict(export_request, preserving_proto_field_name=True)
        
        parsed_logs = []
        
        for resource_logs in protobuf_dict.get('resource_logs', []):
            # Extract resource attributes
            resource_attributes = {}
            for attr in resource_logs.get('resource', {}).get('attributes', []):
                key = attr.get('key')
                value = extract_attribute_value(attr)
                resource_attributes[key] = value
            
            for scope_logs in resource_logs.get('scope_logs', []):
                # Extract scope info
                scope_name = scope_logs.get('scope', {}).get('name', 'unknown')
                
                for log_record in scope_logs.get('log_records', []):
                    # Extract log record attributes
                    log_attributes = {}
                    for attr in log_record.get('attributes', []):
                        key = attr.get('key')
                        value = extract_attribute_value(attr)
                        log_attributes[key] = value
                    
                    # Extract log body
                    body = ""
                    if 'body' in log_record:
                        body = extract_body_value(log_record['body'])
                    
                    # Convert timestamp from nanoseconds to ISO format
                    timestamp_nano = log_record.get('time_unix_nano')
                    if timestamp_nano:
                        # Convert string nanoseconds to datetime
                        timestamp_seconds = int(timestamp_nano) / 1_000_000_000
                        timestamp = datetime.fromtimestamp(timestamp_seconds).isoformat()
                    else:
                        timestamp = datetime.now().isoformat()
                    
                    # Try to extract structured data from otel.log_data attribute
                    otel_log_data = {}
                    otel_log_data_raw = log_attributes.get('otel.log_data', '{}')
                    if isinstance(otel_log_data_raw, str):
                        try:
                            otel_log_data = json.loads(otel_log_data_raw)
                        except json.JSONDecodeError:
                            logger.warning(f"Failed to parse otel.log_data as JSON: {otel_log_data_raw}")
                    
                    # Build the log data
                    log_data = {
                        'timestamp': timestamp,
                        'level': log_record.get('severity_text', otel_log_data.get('level', 'INFO')),
                        'function_name': otel_log_data.get('function_name', scope_name),
                        'module': otel_log_data.get('module', resource_attributes.get('service.name', 'unknown')),
                        'duration_ms': otel_log_data.get('duration_ms', 0.0),
                        'status': otel_log_data.get('status', 'unknown'),
                        'message': body,
                        'args': otel_log_data.get('args'),
                        'kwargs': otel_log_data.get('kwargs'),
                        'result': otel_log_data.get('result'),
                        'error': otel_log_data.get('error'),
                        'error_type': otel_log_data.get('error_type'),
                        'severity_number': log_record.get('severity_number'),
                        'severity_text': log_record.get('severity_text'),
                        'resource_attributes': resource_attributes,
                        'log_attributes': log_attributes,
                        'scope_name': scope_name,
                        'trace_id': log_record.get('trace_id'),
                        'span_id': log_record.get('span_id'),
                    }
                    parts = [part for part in str(body).split('|')]

                    if len(parts) >= 5:
                        log_data['level'] = parts[1]
                        log_data['function_name'] = parts[2]
                        log_data['duration_ms'] = float(parts[3].split('ms')[0] if 'ms' in parts[3] else parts[3])
                        log_data['message'] = parts[4]
                    parsed_logs.append(log_data)
        
        return parsed_logs
    
    except Exception as e:
        logger.error(f"Failed to parse protobuf data: {e}")
        raise ValueError(f"Failed to parse protobuf data: {str(e)}")

# Initialize FastAPI app and storage
app = FastAPI(title="OpenTelemetry Log Viewer")
log_storage = LogStorage()

# Mount static files and templates if you have them
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})

@app.post("/api/logs")
async def receive_logs(request: Request):
    """
    Receives OTLP protobuf logs, parses them, and stores them
    """
    try:
        # Get the request body
        body = await request.body()
        content_encoding = request.headers.get("content-encoding", "")
        
        # Handle gzip compression if present
        if content_encoding == "gzip":
            if body.startswith(b'\x1f\x8b'):  # Check gzip magic bytes
                body = gzip.decompress(body)
                logger.info(f"Decompressed gzipped data to {len(body)} bytes")
            else:
                logger.info(f"Content-Encoding header says gzip but data is not compressed")
        
        # Parse the protobuf logs
        parsed_logs = parse_protobuf_logs(body)
        
        # Store each log
        logs_added = 0
        for log_data in parsed_logs:
            log_storage.add_log(log_data)
            logs_added += 1
        
        logger.info(f"Successfully added {logs_added} logs to storage")
        
        return {
            "status": "success", 
            "logs_added": logs_added,
            "total_logs_in_storage": len(log_storage.logs)
        }
        
    except Exception as e:
        logger.error(f"Error processing logs: {e}")
        raise HTTPException(status_code=400, detail=f"Error processing logs: {str(e)}")

@app.get("/api/logs", response_model=List[LogRecord])
async def get_logs_api(limit: int = 100, function_name: Optional[str] = None):
    """Get stored logs for display"""
    return log_storage.get_logs(limit=limit, function_name=function_name)

@app.get("/api/logs/{log_id}", response_model=LogRecord)
async def get_log_detail_api(log_id: str):
    """Get detailed view of a specific log"""
    log = log_storage.get_log_by_id(log_id)
    if not log:
        raise HTTPException(status_code=404, detail="Log not found")
    return log

@app.get("/api/stats")
async def get_stats():
    """Get statistics about stored logs"""
    total_logs = len(log_storage.logs)
    functions = list(log_storage.logs_by_function.keys())
    
    return {
        "total_logs": total_logs,
        "unique_functions": len(functions),
        "function_names": functions
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
