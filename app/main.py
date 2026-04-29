from contextlib import asynccontextmanager

import structlog
from fastapi import Depends, FastAPI
from sqlmodel import Session, select

from app.api.admin import router as admin_router
from app.config import settings
from app.db import get_session
from app.models import Competitor
from app.scheduler import build_scheduler

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN201
    scheduler = build_scheduler()
    scheduler.start()
    logger.info("scheduler.started", jobs=[j.id for j in scheduler.get_jobs()])
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        logger.info("scheduler.stopped")


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.include_router(admin_router)


@app.get("/healthz")
def healthz(session: Session = Depends(get_session)) -> dict[str, str | int]:
    competitor_count = len(session.exec(select(Competitor)).all())
    return {"status": "ok", "competitors": competitor_count}


@app.get("/")
def root() -> dict[str, str]:
    return {"app": settings.app_name, "environment": settings.environment}
