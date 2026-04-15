"""
FastAPI application entry point for the Cricket Hot Match Engine.

Models are loaded once at startup via the lifespan handler and stored on
app.state so all routes share a single EngineOrchestrator instance.

Run locally:
    conda run -n cricket_hot uvicorn engine.server:app --reload --port 8000
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from engine.orchestrator import EngineOrchestrator

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.engine = EngineOrchestrator(MODELS_DIR)
    yield


app = FastAPI(
    title="Cricket Hot Match Engine",
    version="1.0.0",
    lifespan=lifespan,
)

from engine.routes import router  # noqa: E402 — must import after app is defined

app.include_router(router)
