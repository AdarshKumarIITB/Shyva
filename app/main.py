"""FastAPI application entry point."""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from app.audit.db import init_db
from app.api import classify, clarify, lookup, duties, health
from app.api import v3_classify

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="Shyva",
    description="Tariff Classification & Duty Lookup Engine",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes
app.include_router(health.router, prefix="/api", tags=["health"])
app.include_router(classify.router, prefix="/api", tags=["classification"])
app.include_router(clarify.router, prefix="/api", tags=["classification"])
app.include_router(lookup.router, prefix="/api", tags=["lookup"])
app.include_router(duties.router, prefix="/api", tags=["duties"])
app.include_router(v3_classify.router, prefix="/api/v3", tags=["v3-classification"])


@app.get("/")
async def root():
    index = FRONTEND_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return {"message": "Shyva API running. Use /api/ endpoints or /docs for OpenAPI."}


# Static assets (CSS, JS) served under /static
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")
