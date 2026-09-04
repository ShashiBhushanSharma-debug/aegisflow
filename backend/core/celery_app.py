import os 
from celery import Celery

# Using the REDIS URL from the environment, fallback to localhost for local testing 
# Use Redis URL from environment, fallback to localhost for local testing
redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "aegisflow_workers",
    broker=redis_url,
    backend=redis_url,
    include=["backend.workers.tasks"] # Where our tasks will live
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)