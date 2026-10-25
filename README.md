# AI Data Analyst Agent

An application planned to let users upload CSV files, ask questions in natural language, analyze data with Python and pandas, generate charts, and correct analysis errors where possible.

## Current status

**Phase 1.3 — Frontend and backend foundations.** The Next.js frontend is initialized with the App Router, TypeScript, Tailwind CSS, and ESLint; it displays a development-stage workspace shell. The Python backend uses FastAPI and provides a process-level health endpoint. Dataset upload, question submission, analysis, and AI functionality are not implemented.

## Planned technology stack

- **Frontend:** Next.js, TypeScript, and Tailwind CSS
- **Backend:** Python and FastAPI
- **Data analysis:** Python and pandas
- **Language model:** Provider and model to be selected/configured during a later phase

## Repository layout

- `frontend/` — Next.js App Router application with TypeScript, Tailwind CSS, and ESLint; currently a non-functional workspace shell.
- `backend/` — Python and FastAPI application foundation with a versioned health endpoint; see [`backend/README.md`](backend/README.md) for setup and commands.
- `docs/` — project architecture and development documentation.

The `docs/` directory is reserved for project architecture and development documentation.

## Prerequisites for future development

Frontend development requires Git, Node.js (20.9 or later), and npm. Backend development requires Python 3.9 or later and pip.

## Planned local development workflow

The frontend and backend workflows are documented separately.

1. From `frontend/`, install dependencies with `npm install`.
2. Start the development server with `npm run dev`.
3. Run `npm run lint` and `npm run build` to check the frontend.
4. For the backend, follow [`backend/README.md`](backend/README.md) to create its virtual environment, install dependencies, start Uvicorn, or run pytest.

## Environment configuration

The root `.env.example` lists safe placeholders for anticipated cross-application configuration. `backend/.env.example` documents backend-specific settings; backend settings are read from process environment variables, and that file is not loaded automatically. For frontend-local configuration, place values in `frontend/.env.local`; that file is ignored by Git. Never commit real credentials or add private keys to `NEXT_PUBLIC_` variables.

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
4. **Data-analysis workflow:** add CSV upload, data inspection, and isolated analysis capabilities.
5. **Question-and-answer experience:** connect natural-language questions to analysis and chart generation.
6. **Reliability and refinement:** improve error correction, testing, and user experience.

Later phases are planned only; none of their functionality is present yet.
