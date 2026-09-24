"""API v1 router assembly."""

from fastapi import APIRouter
from app.api.v1.kri_routes import router as kri_router
from app.api.v1.audit_routes import router as audit_router

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(kri_router)
api_v1_router.include_router(audit_router)

__all__ = ["api_v1_router"]
