from __future__ import annotations

import asyncio
import shutil
import time
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import router as api_router
from app.config import get_settings
from app.db import SessionLocal, create_all
from app.log import request_logger, setup_logging
from app.services import runtime_config, skills_install
from app.services.apps import seed_gallery
from app.services.nlcompile import mark_active_nlcompile_sessions_interrupted
from app.services.prompt_assistant import mark_active_prompt_assistant_sessions_interrupted
from app.services.prompts import seed_prompt_templates
from app.services.runs import mark_active_runs_interrupted
from app.services.skills import reconcile_skill_dependencies
from app.services.workspace_gc import cleanup_orphan_run_homes, cleanup_orphan_run_workspaces


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.runtime_dir.mkdir(parents=True, exist_ok=True)
    await create_all()
    async with SessionLocal() as db:
        await seed_prompt_templates(db)
        await runtime_config.write_configs(db)
        await reconcile_skill_dependencies(db)
        await skills_install.sync_global_skills(db)
        await seed_gallery(db)
        await mark_active_runs_interrupted(db)
        await cleanup_orphan_run_workspaces(db)
        await cleanup_orphan_run_homes(db)
        await mark_active_nlcompile_sessions_interrupted(db)
        await mark_active_prompt_assistant_sessions_interrupted(db)
    disk_task = asyncio.create_task(disk_monitor_loop())
    try:
        yield
    finally:
        disk_task.cancel()


app = FastAPI(title="Mira Backend", docs_url="/api/docs", openapi_url="/api/openapi.json", lifespan=lifespan)
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_timing(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    elapsed = time.perf_counter() - start
    if elapsed > 2:
        request_logger.warning("%s %s took %.2fs", request.method, request.url.path, elapsed)
    return response


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc.detail)})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"detail": "请求参数无效"})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    request_id = uuid4().hex
    request_logger.exception("request_id=%s unhandled error on %s", request_id, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "服务器内部错误", "request_id": request_id},
    )


app.include_router(api_router, prefix="/api")


async def disk_monitor_loop() -> None:
    settings = get_settings()
    while True:
        free = shutil.disk_usage(settings.runtime_dir).free
        if free < settings.disk_min_free_bytes:
            request_logger.warning(
                "runtime filesystem free space below threshold: free=%s threshold=%s",
                free,
                settings.disk_min_free_bytes,
            )
        await asyncio.sleep(3600)
