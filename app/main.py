from fastapi import FastAPI

from app.config import settings

app = FastAPI(title=settings.app_name)


@app.get("/healthz")
def healthz() -> dict[str, str | int]:
    return {"status": "ok", "competitors": 0}


@app.get("/")
def root() -> dict[str, str]:
    return {"app": settings.app_name, "environment": settings.environment}
