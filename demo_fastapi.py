import time
import logging
import random
import threading
from fastapi import FastAPI
import uvicorn
from api_spy import ApiSpyMiddleware

app = FastAPI()
# Add our performance terminal spy middleware
app.add_middleware(ApiSpyMiddleware)

# Set up logging to stdout to verify stream interception
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("demo")

@app.get("/")
def read_root():
    logger.info("Processing fast request at /")
    return {"status": "ok"}

@app.get("/slow")
def read_slow():
    logger.info("Processing slow request at /slow (sleeps 250ms)")
    time.sleep(0.25)
    return {"status": "delayed"}

@app.post("/submit")
def post_submit():
    logger.info("Processing POST request at /submit (sleeps 50ms)")
    time.sleep(0.05)
    return {"status": "created"}

@app.get("/error")
def force_error():
    logger.error("Processing request at /error - raising simulation error")
    raise ValueError("Simulation error triggered!")

def log_generator():
    """Simulates background task logs to verify sticky dashboard positioning."""
    actions = [
        "Worker thread health check passed.",
        "Internal cache garbage collector run complete.",
        "Sync task successfully fetched 12 records.",
        "Warning: Connection pool capacity reached 80%.",
        "Database replication status: Synced.",
    ]
    time.sleep(3) # Wait for server startup messages to clear
    while True:
        action = random.choice(actions)
        if "Warning" in action:
            logger.warning(f"[BgTask] {action}")
        else:
            logger.info(f"[BgTask] {action}")
        time.sleep(random.uniform(2.0, 4.0))

if __name__ == "__main__":
    # Start background logger
    threading.Thread(target=log_generator, daemon=True).start()
    
    # Run uvicorn server
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
