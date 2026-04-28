from fastapi import Depends, FastAPI
from sqlmodel import Session, select

from app.api.admin import router as admin_router
from app.config import settings
from app.db import get_session
from app.models import Competitor

app = FastAPI(title=settings.app_name)
app.include_router(admin_router)


@app.get("/healthz")
def healthz(session: Session = Depends(get_session)) -> dict[str, str | int]:
    competitor_count = len(session.exec(select(Competitor)).all())
    return {"status": "ok", "competitors": competitor_count}


@app.get("/")
def root() -> dict[str, str]:
    return {"app": settings.app_name, "environment": settings.environment}
