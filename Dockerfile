# Dockerfile til Railway - giver fuld kontrol over WeasyPrint's system-deps.
# Bruger python:3.12-slim som base + apt-get installs af cairo/pango/glib/etc.

FROM python:3.12-slim

# WeasyPrint kraever disse runtime-libs.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libharfbuzz-subset0 \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    fonts-liberation \
    fonts-dejavu-core \
    libxml2 \
    shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Installer Python-deps foerst (caches saalaenge pyproject.toml ikke aendres)
COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir hatchling && \
    pip install --no-cache-dir \
      "fastapi>=0.115.0" "uvicorn[standard]>=0.32.0" "sqlmodel>=0.0.22" \
      "alembic>=1.13.0" "psycopg[binary]>=3.2.0" "pydantic-settings>=2.6.0" \
      "httpx>=0.27.0" "beautifulsoup4>=4.12.0" "lxml>=5.3.0" \
      "feedparser>=6.0.11" "anthropic>=0.39.0" "jinja2>=3.1.0" \
      "structlog>=24.4.0" "pyyaml>=6.0.2" "apscheduler>=3.10.4" \
      "tzdata>=2024.1" "weasyprint>=63.0"

# Saa kopier resten af projektet
COPY . .
RUN pip install --no-cache-dir -e .

# Railway saetter $PORT
CMD ["sh", "-c", "alembic upgrade head && python -m app.seed && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
