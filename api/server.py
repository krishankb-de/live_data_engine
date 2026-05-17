"""FastAPI application entry point.

Run: uvicorn api.server:app --reload --port 8000
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

from api.routes import batches, listings, versions

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("PDM Live-Data Engine API starting")
    yield
    logger.info("PDM Live-Data Engine API stopped")


app = FastAPI(
    title="PDM Live-Data Engine",
    version="0.1.0",
    lifespan=lifespan,
)

_cors_origins = [o.strip() for o in os.environ.get("API_CORS_ORIGINS", "").split(",") if o.strip()]
if not _cors_origins:
    _cors_origins = ["http://localhost:5173", "http://localhost:5174"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def structured_logging_middleware(request: Request, call_next) -> Response:
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = round((time.perf_counter() - start) * 1000)
    logger.info(
        '{"request_id":"%s","method":"%s","path":"%s","status":%d,"duration_ms":%d}',
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    response.headers["X-Request-Id"] = request_id
    return response


app.include_router(batches.router)
app.include_router(listings.router)
app.include_router(versions.router)


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.get("/readyz")
def readyz() -> dict:
    supabase_ok = False
    redis_ok = False

    try:
        from scraper.supabase_client import smoke_test
        result = smoke_test()
        supabase_ok = result.get("ok", False)
    except Exception:
        pass

    redis_url = os.environ.get("REDIS_URL", "")
    if redis_url:
        try:
            import redis as redis_lib
            r = redis_lib.from_url(redis_url, socket_connect_timeout=2)
            r.ping()
            redis_ok = True
        except Exception:
            pass
    else:
        redis_ok = True  # Redis not configured → not a dep for this deployment

    ok = supabase_ok and redis_ok
    from fastapi.responses import JSONResponse
    return JSONResponse(
        content={"ok": ok, "supabase": supabase_ok, "redis": redis_ok},
        status_code=200 if ok else 503,
    )
