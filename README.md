# AI Data Analyst Agent

An application planned to let users upload CSV files, ask questions in natural language, analyze data with Python and pandas, generate charts, and correct analysis errors where possible.

## Current status

**Phase 4.2 — Natural-language question interpretation (Google Gemini).** The Next.js frontend includes a configurable API client, backend health check, accessible CSV upload, and paginated dataset preview. The backend validates bounded UTF-8 CSV uploads, stores them in private local temporary storage with a 24-hour default retention period, and provides dataset metadata, descriptive statistics, data quality findings, and reusable filtering, sorting, grouping, aggregation, and summarization. A provider-independent LLM client interprets natural-language questions from bounded schema and aggregate statistics, without sending full CSV contents or raw row values, and returns a validated structured interpretation with clarifications and limitations. It does not calculate results, execute generated code, generate charts, or orchestrate an agent yet.

## Planned technology stack

- **Frontend:** Next.js, TypeScript, and Tailwind CSS
- **Backend:** Python and FastAPI
- **Data analysis:** Python and pandas
- **Language model:** Google Gemini through the official `google-genai` Python SDK, behind a provider-independent client interface

## Repository layout

- `frontend/` — Next.js App Router application with TypeScript, Tailwind CSS, ESLint, and a backend connection check; see [`frontend/README.md`](frontend/README.md).
- `backend/` — Python and FastAPI application with versioned health, CSV upload/validation, and paginated preview endpoints using private local temporary storage. See [`backend/README.md`](backend/README.md).
- `docs/` — project architecture and development documentation.

The `docs/` directory is reserved for project architecture and development documentation.

## Prerequisites for future development

Install Git, Node.js 20.9 or later with npm, and Python 3.9 or later with pip. The frontend lockfile is npm lockfile version 3; use npm for reproducible installs.

## Local development setup

The frontend and backend run in separate terminals. From a clean checkout, first install frontend and backend dependencies and create the local frontend environment file.

### Install dependencies

From the repository root:

```bash
cd frontend
npm ci
if [ ! -f .env.local ]; then cp .env.example .env.local; fi
cd ../backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Windows PowerShell backend setup, from `backend/`:

```powershell
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

### Start both applications

In **terminal 1**, from `backend/`, activate `.venv` as above and run:

```bash
uvicorn app.main:app --reload
```

The API listens at `http://127.0.0.1:8000` by default.

In **terminal 2**, from `frontend/`, run:

```bash
npm run dev
```

The frontend listens at `http://localhost:3000`. Open it and select **Check backend connection** to make a real request to the API.

### Command reference

| Task | Command (run from) |
| --- | --- |
| Frontend lint | `npm run lint` (`frontend/`) |
| Frontend production build | `npm run build` (`frontend/`) |
| Frontend tests | No frontend test runner is configured; use the browser connection check |
| Backend tests | `pytest` (`backend/`, with `.venv` active) |
| Direct API health check | `curl http://127.0.0.1:8000/api/v1/health` |
| Upload a CSV | `curl -X POST http://127.0.0.1:8000/api/v1/datasets/upload -F "file=@sample.csv"` |
| Interactive API docs | Open `http://127.0.0.1:8000/docs` |

See the frontend and backend READMEs for environment setup and platform-specific commands.

## Environment configuration

`frontend/.env.example` defines `NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000`. Copy it to `frontend/.env.local` if the local file does not already exist; Next.js reads environment files from the frontend project directory and must be restarted after changes. `backend/.env.example` documents backend settings, including `CORS_ORIGINS` and the optional Gemini provider settings. Copy it to `backend/.env`; the backend loads that ignored local file automatically, and process environment variables take precedence. The root `.env.example` remains a repository-level reference and is not loaded automatically by either application. `GEMINI_API_KEY` is backend-only and is required only for natural-language question interpretation; health, upload, profiling, quality, and analytics do not use it. See the backend README for Gemini setup and troubleshooting. Never commit real credentials or put private keys in `NEXT_PUBLIC_` variables.

## Integration troubleshooting

- **Backend unavailable:** verify Uvicorn is running on `127.0.0.1:8000` and that `curl http://127.0.0.1:8000/api/v1/health` returns `{"status":"ok","service":"ai-data-analyst-api"}`.
- **CORS error:** ensure the browser's frontend origin (`http://localhost:3000` or `http://127.0.0.1:3000`) appears in backend `CORS_ORIGINS`, then restart the backend.
- **Wrong or missing API URL:** check `frontend/.env.local` for `NEXT_PUBLIC_API_BASE_URL`, ensure it is an absolute HTTP(S) base URL, and restart Next.js after changing it.
- **Port already in use:** stop the existing server process or choose an unused port; if the frontend starts on a different port, add that exact origin to backend `CORS_ORIGINS`.

## Development conventions

- Keep frontend and backend code separated in their respective directories.
- Use environment variables for configuration; never commit secrets.
- Validate inputs at application boundaries.
- Add tests alongside future functionality.
- Keep data-analysis execution isolated from the main API process.
- Prefer small, focused changes and give modules clear responsibilities.

## Git and secret management

Commit source code, documentation, and dependency manifests and lockfiles when they are introduced. Do not commit credentials, local environment files, uploaded datasets, temporary analysis data, generated exports, logs, or build output. `.gitignore` contains the initial exclusions; review it when adding new generated or sensitive local-data locations.

## Roadmap

1. **Phase 1.1 — Repository setup:** create the folder structure and initial conventions. (Complete.)
2. **Phase 1.2 — Frontend initialization:** establish the Next.js application shell and frontend tooling. (Complete.)
3. **Phase 1.3 — Backend initialization:** establish the FastAPI application and process health check. (Complete.)
4. **Phase 1.4 — Frontend/backend integration:** configure CORS and verify the frontend health check. (Complete.)
5. **Phase 1.5 — Setup verification:** verify reproducible setup, checks, and local frontend/backend communication. (Complete.)
6. **Phase 2.1 — File upload API:** accept bounded multipart CSV uploads without retaining them. (Complete.)
7. **Phase 2.2 — CSV validation:** inspect UTF-8 contents, headers, and row structure with configurable resource limits. (Complete.)
8. **Phase 2.3 — Temporary dataset storage:** keep validated uploads on private local disk with expiration and cleanup. (Complete; development/single-instance only.)
9. **Phase 2.4 — CSV upload UI:** select, validate, and upload CSV files through the frontend. (Complete.)
10. **Phase 2.5 — Dataset preview:** show stored dataset metadata and paginated sample rows. (Complete.)
11. **Phase 3.1 — Dataset metadata:** infer column types and missing-value counts. (Complete.)
12. **Phase 3.2 — Statistical profiling:** return descriptive statistics for stored datasets. (Complete.)
13. **Phase 3.3 — Data quality analysis:** report missing values, duplicates, invalid dates, and type inconsistencies. (Complete.)
14. **Phase 3.4 — Reusable analytics service:** support filtering, sorting, grouping, aggregation, and summarization. (Complete.)
15. **Phase 4.2 — Natural-language question interpretation:** interpret questions from bounded dataset metadata and return validated structured intent, metrics, filters, assumptions, and limitations without executing analysis. (Complete.)
16. **Question-and-answer experience:** connect validated interpretations to analysis and chart generation.
17. **Reliability and refinement:** improve error correction, testing, and user experience.

Later phases are planned only; none of their functionality is present yet. Local temporary file storage is not production dataset storage.
