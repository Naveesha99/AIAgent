# AI Data Analyst Agent

An application planned to let users upload CSV files, ask questions in natural language, analyze data with Python and pandas, generate charts, and correct analysis errors where possible.

## Current status

**Repository setup only.** This repository currently contains initial folders and project documentation. The frontend, backend, and application functionality have not been implemented, and no application dependencies are installed.

## Planned technology stack

- **Frontend:** Next.js, TypeScript, and Tailwind CSS
- **Backend:** Python and FastAPI
- **Data analysis:** Python and pandas
- **Language model:** Provider and model to be selected/configured during a later phase

## Repository layout

- `frontend/` — reserved for the future Next.js, TypeScript, and Tailwind CSS application.
- `backend/` — reserved for the future Python and FastAPI application.
- `docs/` — project architecture and development documentation.

These directories are intentionally empty until their implementation phases begin.

## Prerequisites for future development

When application development starts, contributors will need Git, Node.js and its package manager, and Python. The supported Node.js and Python versions and package-manager choices will be documented when the frontend and backend are initialized. No toolchain setup is required for this repository-scaffolding phase.

## Planned local development workflow

**Not implemented yet.** Once the applications and their dependency manifests exist, the intended workflow will be:

1. Clone the repository and install the separately documented frontend and backend dependencies.
2. Copy `.env.example` to a local, untracked environment file and fill in only the values needed for local development.
3. Start the backend and frontend using commands that will be documented with their respective applications.
4. Run the documented tests and checks for each application.

There are no start, install, or test commands available yet.

## Environment configuration

`.env.example` lists safe placeholders for anticipated local configuration. It is not loaded by any application in this phase. Put actual credentials and machine-specific values in an untracked local environment file (for example, `.env.local`), and never commit that file. Do not put real credentials in `.env.example`.

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

1. **Phase 1.1 — Repository setup:** create the folder structure and initial conventions. (Current phase.)
2. **Phase 1.2 — Application foundations:** initialize the frontend and backend projects and document their development commands.
3. **Data-analysis workflow:** add CSV upload, data inspection, and isolated analysis capabilities.
4. **Question-and-answer experience:** connect natural-language questions to analysis and chart generation.
5. **Reliability and refinement:** improve error correction, testing, and user experience.

Later phases are planned only; none of their functionality is present yet.
