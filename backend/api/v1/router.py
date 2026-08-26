from fastapi import APIRouter
from backend.api.v1.endpoints import cases, webhooks

api_router = APIRouter()

# Register the webhooks router
api_router.include_router(webhooks.router, prefix="/webhooks", tags=["webhooks"])
# Adding the new router cases
api_router.include_router(cases.router, prefix="/cases", tags=["cases"])