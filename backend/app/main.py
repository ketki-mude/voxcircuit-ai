import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health, ws_call
from app.config import Settings, get_settings
from app.db import create_db_engine, create_sessionmaker
from app.live.factory import LiveSessionFactory, default_live_session_factory


def create_app(
    settings: Settings | None = None,
    live_session_factory: LiveSessionFactory | None = None,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_db_engine(settings.database_url)
        app.state.sessionmaker = create_sessionmaker(engine)
        yield
        await engine.dispose()

    app = FastAPI(title="VoxCircuit AI", lifespan=lifespan)
    app.state.settings = settings
    app.state.live_session_factory = live_session_factory or default_live_session_factory(settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(ws_call.router)
    return app


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
app = create_app()
