# konkurrenttracker

Konkurrenceovervaagning for Epico. Ugentlig PDF-rapport + live dashboard, baseret paa 5 gratis datakilder (Jobindex, karriere-sider, CVR, Google News, Wayback Machine) + Claude-analyse.

Status: **Sprint 01 - Fundament** (under opsaetning).

## Stack

- Python 3.12 + FastAPI + SQLModel + Alembic
- PostgreSQL 16 (Railway add-on)
- Claude Sonnet 4.6 (syntese) + Haiku 4.5 (klassificering)
- WeasyPrint (PDF) + Postmark (mail)
- React + Vite (dashboard, kommer i Sprint 04)
- Hosted paa Railway

## Lokal udvikling

Kommer naar Sprint 01 er gennemfoert.

## Sprint-plan

| Sprint | Mal | Status |
| --- | --- | --- |
| 01 - Fundament | Tom FastAPI-app paa Railway m/ DB | I gang |
| 02 - Datakilder | 5 scrapere + cron-jobs | - |
| 03 - Claude-analyse | Klassificering + signal-detektion | - |
| 04 - Dashboard | React-frontend m/ basic auth | - |
| 05 - PDF og mail | Ugentlig leverance via Postmark | - |

Se `epico-udviklingsplan.pdf` for fuld kontekst.
