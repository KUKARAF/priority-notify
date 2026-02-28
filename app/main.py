from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.database import engine
from app.models import Base
from app.routes import auth, frontend, notifications, tokens

settings = get_settings()

structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(
        structlog._log_levels.NAME_TO_LEVEL[settings.LOG_LEVEL.lower()]
    ),
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
)

log = structlog.get_logger()

templates = Jinja2Templates(directory="app/templates")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    log.info("database_ready")
    yield
    await engine.dispose()


app = FastAPI(title="priority-notify", lifespan=lifespan)

# CORS
if settings.cors_origins_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Routers
app.include_router(auth.router)
app.include_router(notifications.router)
app.include_router(tokens.router)
app.include_router(frontend.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
