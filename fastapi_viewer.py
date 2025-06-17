from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send
import gzip
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import uuid
from datetime import datetime
from collections import defaultdict


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


class GzipRequestMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            if b"content-encoding" in headers and headers[b"content-encoding"] == b"gzip":
                
                # The original request body is now a stream of compressed bytes.
                # We need to read this stream fully to decompress it.
                original_receive = receive
                
                async def receive_decompressed() -> Message:
                    message = await original_receive()
                    
                    # If the request body is sent in chunks, you might need more complex
                    # logic to accumulate all chunks before decompressing.
                    # For OTLP, the body is typically sent in a single chunk.
                    if message["type"] == "http.request":
                        if message.get("body"):
                            message["body"] = gzip.decompress(message["body"])
                    
                    return message
                
                # Replace the original receive stream with our new decompressing one
                receive = receive_decompressed

        await self.app(scope, receive, send)


app = FastAPI(title="OpenTelemetry Log Viewer")
app.add_middleware(GzipRequestMiddleware)
log_storage = LogStorage()

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})

@app.post("/api/logs")
async def receive_log(request: Request):
    """
    Receives a batch of logs in OTLP/JSON format, parses them,
    and adds them to the in-memory log storage.
    """
    payload = await request.json()
    logs_added = 0

    # The OTLP/JSON payload is a nested structure. We need to navigate it.
    if "resourceLogs" in payload:
        for resource_log in payload.get("resourceLogs", []):
            for scope_log in resource_log.get("scopeLogs", []):
                for log_record in scope_log.get("logRecords", []):
                    
                    # Find the attribute that contains our custom log data dictionary
                    for attribute in log_record.get("attributes", []):
                        if attribute.get("key") == "otel.log_data":
                            
                            # The 'otel.log_data' is a nested structure itself.
                            # We need to reconstruct the flat dictionary from it.
                            log_data = {}
                            kv_list = attribute.get("value", {}).get("kvlistValue", {}).get("values", [])
                            
                            for kv in kv_list:
                                key = kv.get("key")
                                # The value is nested inside another dictionary, e.g., {"stringValue": "..."}
                                # We get the first (and only) value from that inner dictionary.
                                value = list(kv.get("value", {}).values())[0]
                                log_data[key] = value

                            # Now that we have the flat dictionary, add it to our storage
                            if log_data:
                                log_storage.add_log(log_data)
                                logs_added += 1

    return {"status": "success", "logs_added": logs_added}

@app.get("/api/logs", response_model=List[LogRecord])
async def get_logs_api(limit: int = 100, function_name: Optional[str] = None):
    return log_storage.get_logs(limit=limit, function_name=function_name)

@app.get("/api/logs/{log_id}", response_model=LogRecord)
async def get_log_detail_api(log_id: str):
    log = log_storage.get_log_by_id(log_id)
    if not log:
        raise HTTPException(status_code=404, detail="Log not found")
    return log

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="localhost", port=8000)
