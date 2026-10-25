# AI Data Analyst Agent

An application planned to let users upload CSV files, ask questions in natural language, analyze data with Python and pandas, generate charts, and correct analysis errors where possible.

## Current status

**Phase 1.4 — Frontend/backend integration.** The Next.js frontend includes a configurable API client and a button that checks the FastAPI process health endpoint. FastAPI allows the configured local frontend origins through CORS. Dataset upload, question submission, analysis, and AI functionality are not implemented.

## Planned technology stack

- **Frontend:** Next.js, TypeScript, and Tailwind CSS
- **Backend:** Python and FastAPI
- **Data analysis:** Python and pandas
- **Language model:** Provider and model to be selected/configured during a later phase

## Repository layout

- `frontend/` — Next.js App Router application with TypeScript, Tailwind CSS, ESLint, and a backend connection check; see [`frontend/README.md`](frontend/README.md).
- `backend/` — Python and FastAPI application with a versioned health endpoint and explicit CORS origins; see [`backend/README.md`](backend/README.md).
- `docs/` — project architecture and development documentation.

The `docs/` directory is reserved for project architecture and development documentation.

## Prerequisites for future development

Frontend development requires Git, Node.js (20.9 or later), and npm. Backend development requires Python 3.9 or later and pip.

## Planned local development workflow

The frontend and backend run as separate development servers.

1. From `frontend/`, install dependencies with `npm install`.
2. Copy `frontend/.env.example` to `frontend/.env.local`; the default API URL is `http://127.0.0.1:8000`.
3. Start the backend in `backend/` with its virtual environment activated: `uvicorn app.main:app --reload` (default port 8000).
4. Start the frontend in `frontend/` with `npm run dev` (default port 3000).
5. Visit `http://localhost:3000` and select **Check backend connection**. To test the API directly, request `http://127.0.0.1:8000/api/v1/health`.
6. Run `npm run lint` and `npm run build` in `frontend/`; run `pytest` in `backend/`.

See the frontend and backend READMEs for environment setup and platform-specific commands.

## Environment configuration

`frontend/.env.example` is the Next.js configuration template and defines `NEXT_PUBLIC_API_BASE_URL`. Copy it to `frontend/.env.local`; Next.js reads environment files from the frontend project directory and must be restarted after changes. `backend/.env.example` documents backend settings, including `CORS_ORIGINS`; the backend reads process environment variables and does not automatically load that example file. The root `.env.example` remains a repository-level reference and is not loaded automatically. Never commit real credentials or put private keys in `NEXT_PUBLIC_` variables.

## Integration troubleshooting

- **Backend unavailable:** verify Uvicorn is running on `127.0.0.1:8000` and that `curl http://127.0.0.1:8000/api/v1/health` returns the expected JSON.
- **CORS error:** ensure the browser's frontend origin (`http://localhost:3000` or `http://127.0.0.1:3000`) appears in backend `CORS_ORIGINS`, then restart the backend.
- **Wrong or missing API URL:** check `frontend/.env.local` for `NEXT_PUBLIC_API_BASE_URL`, ensure it is an absolute HTTP(S) base URL, and restart Next.js after changing it.

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
5. **Data-analysis workflow:** add CSV upload, data inspection, and isolated analysis capabilities.
6. **Question-and-answer experience:** connect natural-language questions to analysis and chart generation.
7. **Reliability and refinement:** improve error correction, testing, and user experience.

Later phases are planned only; none of their functionality is present yet.
