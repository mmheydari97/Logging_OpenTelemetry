import time
import random
from otel_logger import logger

# Configure the logger to send data to the OpenTelemetry collector
logger.configure(endpoint="http://localhost:4317")

@logger.log_execution(level="INFO", include_args=True, include_result=True)
def calculate_sum(a: int, b: int) -> int:
    time.sleep(0.1)
    return a + b

@logger.log_execution(level="WARNING", include_args=True)
def risky_operation():
    time.sleep(0.2)
    if random.random() < 0.3:
        raise ValueError("A random failure occurred.")
    return "Success"

if __name__ == "__main__":
    print("Running application with OpenTelemetry logging...")
    
    sum_result = calculate_sum(15, 30)
    print(f"Sum result: {sum_result}")
    
    try:
        risky_result = risky_operation()
        print(f"Risky operation result: {risky_result}")
    except ValueError as e:
        print(f"Risky operation failed as expected: {e}")
        
    logger.log_custom("just testing it", level="info", auto_locate=True)
    print("Execution complete. Check the FastAPI dashboard for logs at http://localhost:8000")
    