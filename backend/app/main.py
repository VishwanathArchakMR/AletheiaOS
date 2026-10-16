import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.health import router as health_router
from app.api.routes.knowledge import router as knowledge_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.graph.client import GraphClient
from app.graph.repository import GraphRepository

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    client = GraphClient(settings)
    repository = GraphRepository(client)
    app.state.graph_repository = repository
    try:
        repository.initialize()
    except Exception:
        logger.exception("FalkorDB schema initialization deferred until the database is available")
    yield
    client.close()


settings = get_settings()
app = FastAPI(title="AletheiaOS API", version="0.1.0", lifespan=lifespan)
app.state.settings = settings
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
app.include_router(health_router, prefix="/api/v1")
app.include_router(knowledge_router, prefix="/api/v1")
