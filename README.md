# AI Data Analyst Agent

An application planned to let users upload CSV files, ask questions in natural language, analyze data with Python and pandas, generate charts, and correct analysis errors where possible.

## Current status

**Phase 1.2 — Frontend foundation.** The Next.js frontend is initialized with the App Router, TypeScript, Tailwind CSS, and ESLint. It displays a development-stage workspace shell; dataset upload, question submission, backend services, and analysis functionality are not implemented. The backend remains uninitialized, and no backend dependencies are installed.

## Planned technology stack

- **Frontend:** Next.js, TypeScript, and Tailwind CSS
- **Backend:** Python and FastAPI
- **Data analysis:** Python and pandas
- **Language model:** Provider and model to be selected/configured during a later phase

## Repository layout

- `frontend/` — Next.js App Router application with TypeScript, Tailwind CSS, and ESLint; currently a non-functional workspace shell.
- `backend/` — reserved for the future Python and FastAPI application.
- `docs/` — project architecture and development documentation.

The backend and documentation directories are reserved for their later phases and do not yet contain application code.

## Prerequisites for future development

Frontend development currently requires Git, Node.js (20.9 or later), and npm. Backend Python requirements and versions will be documented when the backend is initialized.

## Planned local development workflow

The frontend workflow is available; the backend workflow is not implemented yet.

1. From `frontend/`, install dependencies with `npm install`.
2. Start the development server with `npm run dev`.
3. Run `npm run lint` and `npm run build` to check the frontend.

The backend does not yet have install, start, or test commands.

## Environment configuration

The root `.env.example` lists safe placeholders for anticipated local configuration, including the future frontend API base URL. No backend integration reads this value yet. For future frontend-local configuration, place values in `frontend/.env.local`; that file is ignored by Git. Never commit real credentials or add private keys to `NEXT_PUBLIC_` variables.

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
3. **Phase 1.3 — Backend initialization:** create the Python and FastAPI foundation.
4. **Data-analysis workflow:** add CSV upload, data inspection, and isolated analysis capabilities.
5. **Question-and-answer experience:** connect natural-language questions to analysis and chart generation.
6. **Reliability and refinement:** improve error correction, testing, and user experience.

Later phases are planned only; none of their functionality is present yet.
