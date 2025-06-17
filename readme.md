# 🧪 OpenTelemetry Logging Demo with FastAPI

This project demonstrates a simple yet powerful logging system using **OpenTelemetry** to track and visualize Python function execution. It includes:

* 🧰 A **decorator-based logger** with execution timing and structured metadata
* 📤 A FastAPI server that **receives OTLP logs** in protobuf format
* 📊 A web-based **dashboard to browse logs** with drill-down details

---

## 🛠️ Features

* Decorator to auto-log function metadata: name, args, result, duration, status
* Logs sent to OpenTelemetry Collector via OTLP gRPC
* In-memory storage of logs for demo purposes
* FastAPI viewer to explore and inspect logs
* Auto-refreshing dashboard built with vanilla JS

---

## 🚀 Getting Started

### 1. Clone & Set Up Environment

```bash
uv venv --python 3.12 .logging_venv
.\.logging_venv\Scripts\activate
uv pip install -r requirements.txt
```

### 2. Start the Stack

Ensure Docker is installed, then:

```bash
docker-compose up -d
```

This launches the OpenTelemetry Collector with the provided `config.yaml`.

### 3. Run the Example App

```bash
python fastapi_viewer.py
python example_app.py
```

It logs sample function calls using your `otel_logger`.

### 4. View Logs in Browser

Visit: [http://localhost:8000](http://localhost:8000)
The dashboard auto-refreshes every 5 seconds.

---

## 📂 Project Structure

```
├── example_app.py         # Example script with logging decorators
├── otel_logger.py         # Singleton logger class using OpenTelemetry
├── fastapi_viewer.py      # FastAPI server to receive and view logs
├── templates/dashboard.html
├── static/js/scripts.js   # JS to fetch and display logs
├── config.yaml            # OpenTelemetry Collector config
├── docker-compose.yml     # Containerized OTEL stack
├── requirements.txt
```

---

## 🧪 Notes

* Logs are kept in memory (max 1000); adapt `LogStorage` for persistent storage in production.
* Adjust OTLP endpoints in `otel_logger.py` and `docker-compose.yml` as needed.


