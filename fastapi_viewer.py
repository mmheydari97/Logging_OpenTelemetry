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

from google.protobuf.json_format import MessageToDict
from opentelemetry.proto.collector.logs.v1 import logs_service_pb2

from log_models import LogData, LogRecord

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class LogStorage:
    def __init__(self):
        self.logs: List[LogRecord] = []
        self.logs_by_function: Dict[str, List[LogRecord]] = defaultdict(list)

    def add_log(self, log_data: Dict[str, Any]) -> str:
        """Add a log entry to storage, converting dict to LogRecord"""
        log_record = LogRecord.from_dict(log_data)
        
        self.logs.append(log_record)
        self.logs_by_function[log_record.function_name].append(log_record)
        
        # Keep only last 1000 logs
        if len(self.logs) > 1000:
            removed_log = self.logs.pop(0)
            if removed_log.function_name in self.logs_by_function:
                if removed_log in self.logs_by_function[removed_log.function_name]:
                    self.logs_by_function[removed_log.function_name].remove(removed_log)
        
        return log_record.id

    def add_log_from_log_data(self, log_data: LogData) -> str:
        """Add a log entry from LogData object"""
        log_record = LogRecord(log_data=log_data, raw_data=log_data.to_dict())
        
        self.logs.append(log_record)
        self.logs_by_function[log_record.function_name].append(log_record)
        
        # Keep only last 1000 logs
        if len(self.logs) > 1000:
            removed_log = self.logs.pop(0)
            if removed_log.function_name in self.logs_by_function:
                if removed_log in self.logs_by_function[removed_log.function_name]:
                    self.logs_by_function[removed_log.function_name].remove(removed_log)
        
        return log_record.id

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


def parse_protobuf_logs(protobuf_data: bytes) -> List[LogData]:
    """Parse OTLP protobuf logs data and return list of LogData objects"""
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
                    
                    # Extract log body (this is the actual message)
                    body = ""
                    if 'body' in log_record:
                        body = extract_body_value(log_record['body'])
                    
                    # Convert timestamp from nanoseconds to ISO format
                    timestamp_nano = log_record.get('time_unix_nano')
                    if timestamp_nano:
                        timestamp_seconds = int(timestamp_nano) / 1_000_000_000
                        timestamp = datetime.fromtimestamp(timestamp_seconds).isoformat()
                    else:
                        timestamp = datetime.now().isoformat()
                    
                    # Try to extract structured data from otel.log_data attribute
                    otel_log_data = {}
                    otel_log_data_raw = log_attributes.get('otel.log_data')
                    if isinstance(otel_log_data_raw, str):
                        try:
                            otel_log_data = json.loads(otel_log_data_raw)
                        except json.JSONDecodeError:
                            logger.warning(f"Failed to parse otel.log_data as JSON: {otel_log_data_raw}")
                    
                    # Create LogData object with all available information
                    log_data_dict = {
                        'timestamp': otel_log_data.get('timestamp', timestamp),
                        'level': otel_log_data.get('level', log_record.get('severity_text', 'INFO')),
                        'function_name': otel_log_data.get('function_name', scope_name),
                        'module': otel_log_data.get('module', resource_attributes.get('service.name', 'unknown')),
                        'duration_ms': otel_log_data.get('duration_ms', 0.0),
                        'status': otel_log_data.get('status', 'unknown'),
                        'message': body,  # Use the actual log message body
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
                    
                    # Handle legacy format parsing (if body contains pipe-separated values)
                    parts = [part.strip() for part in str(body).split('|')]
                    if len(parts) >= 5:
                        log_data_dict['level'] = parts[1]
                        log_data_dict['function_name'] = parts[2]
                        duration_part = parts[3]
                        if 'ms' in duration_part:
                            try:
                                log_data_dict['duration_ms'] = float(duration_part.split('ms')[0])
                            except ValueError:
                                pass
                        log_data_dict['message'] = parts[4]
                    
                    # Filter out None values and create LogData object
                    filtered_dict = {k: v for k, v in log_data_dict.items() if v is not None}
                    
                    try:
                        log_data = LogData(**filtered_dict)
                        parsed_logs.append(log_data)
                    except Exception as e:
                        logger.warning(f"Failed to create LogData object: {e}, falling back to dict")
                        # Fallback: create a basic LogData with minimal required fields
                        basic_log_data = LogData(
                            timestamp=filtered_dict.get('timestamp', datetime.now().isoformat()),
                            level=filtered_dict.get('level', 'INFO'),
                            function_name=filtered_dict.get('function_name', 'unknown'),
                            module=filtered_dict.get('module', 'unknown'),
                            duration_ms=filtered_dict.get('duration_ms', 0.0),
                            status=filtered_dict.get('status', 'unknown'),
                            message=filtered_dict.get('message', body)
                        )
                        parsed_logs.append(basic_log_data)
        
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
        
        # Parse the protobuf logs into LogData objects
        parsed_logs = parse_protobuf_logs(body)
        
        # Store each log
        logs_added = 0
        for log_data in parsed_logs:
            log_storage.add_log_from_log_data(log_data)
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


@app.get("/api/logs/preview/{log_id}")
async def get_log_preview_api(log_id: str):
    """Get preview fields for a specific log"""
    log = log_storage.get_log_by_id(log_id)
    if not log:
        raise HTTPException(status_code=404, detail="Log not found")
    return log.get_preview_fields()


@app.get("/api/logs/preview")
async def get_logs_preview_api(limit: int = 100, function_name: Optional[str] = None):
    """Get preview fields for multiple logs"""
    logs = log_storage.get_logs(limit=limit, function_name=function_name)
    return [log.get_preview_fields() for log in logs]


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
    