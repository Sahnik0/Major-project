import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from apps.api.app.config import settings
from apps.api.app.db import init_db
from apps.api.app.routers import chat, datasets, forecasts, health, jobs
from apps.api.app.routers import settings as app_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("advisor.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Initializing database...")
    init_db()

    # Device detection
    try:
        import torch

        if torch.cuda.is_available():
            device_name = torch.cuda.get_device_name(0)
            logger.info(f"NVIDIA GPU detected and ready: {device_name} (CUDA available)")
        else:
            logger.info("No CUDA GPU detected. Falling back to CPU.")
    except Exception as e:
        logger.warning(f"PyTorch not loaded or CUDA check error: {e}. Defaulting to CPU.")

    logger.info(
        f"Ollama endpoint configured at: {settings.OLLAMA_URL} (model: {settings.OLLAMA_MODEL})"
    )
    logger.info(f"TimesFM version: {settings.TIMESFM_VERSION}")

    yield

    # Shutdown
    logger.info("Shutting down Demand Forecasting & Inventory Advisor API...")


app = FastAPI(
    title="Demand Forecasting & Inventory Advisor API",
    description="Local-first sales forecasting and inventory advisor engine powered by TimesFM and Gemma.",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Standardized error handling: {code, message, hint}
@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    detail = exc.detail
    if isinstance(detail, dict) and "code" in detail:
        return JSONResponse(status_code=exc.status_code, content=detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": f"HTTP_{exc.status_code}",
            "message": str(detail),
            "hint": "Refer to the API documentation at /docs for request requirements.",
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    first_err = errors[0] if errors else {}
    loc = " -> ".join(str(part) for part in first_err.get("loc", []))
    msg = first_err.get("msg", "Invalid request body or parameters")
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "code": "VALIDATION_ERROR",
            "message": f"Validation failed at '{loc}': {msg}",
            "hint": "Check parameter types and required fields against OpenAPI docs.",
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception(f"Unhandled server error: {exc}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "code": "INTERNAL_SERVER_ERROR",
            "message": "An unexpected server error occurred.",
            "hint": "Inspect server logs or contact system administrator.",
        },
    )


# Register routers
app.include_router(health.router, prefix="/api/v1")
app.include_router(datasets.router, prefix="/api/v1")
app.include_router(forecasts.router, prefix="/api/v1")
app.include_router(jobs.router, prefix="/api/v1")
app.include_router(chat.router, prefix="/api/v1")
app.include_router(app_settings.router, prefix="/api/v1")

# Convenience root health endpoint
app.include_router(health.router)


@app.get("/")
def root():
    return {
        "app": "Demand Forecasting & Inventory Advisor API",
        "version": "0.1.0",
        "docs": "/docs",
        "health": "/api/v1/health",
    }
