"""Main FastAPI Application Entrypoint.

Provides the REST API server for the Azure OpenAI Agentic Continuous Internal Audit KRI Engine.
Configures CORS, lifespan initialization, OpenAPI documentation, and API v1 routing.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.core.config import settings
from app.core.database import init_db
from app.api.v1 import api_v1_router
from app.api.ui_routes import router as ui_router
# Ensure all tools are registered in global registry
import app.tools  # noqa: F401


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager to initialize database schema on startup."""
    init_db()
    yield


app = FastAPI(
    title=settings.app_name,
    description=(
        "Backend REST API for the Continuous Internal Audit Key Risk Indicator (KRI) Engine. "
        "Orchestrates autonomous LLM agent execution with Azure OpenAI tool-calling for financial audit workflows."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# CORS Configuration for Person B Frontend Integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Permits local development and frontend cross-origin requests
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount API routers
app.include_router(api_v1_router)
app.include_router(ui_router)


@app.get("/", tags=["Health & Status"])
def root():
    """Root endpoint with service identity and system metadata."""
    return {
        "app_name": settings.app_name,
        "version": "1.0.0",
        "environment": settings.environment,
        "status": "OPERATIONAL",
        "docs_url": "/docs",
        "api_v1": "/api/v1",
        "mock_llm_mode": settings.use_mock_llm,
    }


@app.get("/health", tags=["Health & Status"], status_code=status.HTTP_200_OK)
def health_check():
    """Health check endpoint for container probes and uptime monitoring."""
    return {"status": "HEALTHY", "database": "CONNECTED"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
