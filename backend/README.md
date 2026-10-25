# AI Data Analyst API

The backend foundation uses FastAPI and Pydantic. It currently provides only a process-level health check; it does not check external services or implement dataset processing, analytics, or AI integration.

## Requirements

- Python 3.9 or later
- pip (included with Python)

The project dependencies are pinned in `pyproject.toml`. The `.venv/` virtual environment is local and ignored by Git.

## Create and activate the virtual environment

Run these commands from `backend/`.

macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Windows PowerShell:

```powershell
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
```

## Install dependencies

With the virtual environment activated:

```bash
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

The `dev` extra includes pytest and the HTTP client used by FastAPI's in-process test client.

## Configure the backend

`backend/.env.example` contains safe backend defaults, including `CORS_ORIGINS`, a comma-separated list of browser origins allowed to access the API. The defaults allow `http://localhost:3000` and `http://127.0.0.1:3000`. CORS does not allow credentials. Settings are read from process environment variables; the example file is not loaded automatically. Set the values in your shell or copy them into your local environment setup. For example, on macOS/Linux:

```bash
export APP_ENV=development
export API_HOST=127.0.0.1
export API_PORT=8000
export CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
```

On Windows PowerShell:

```powershell
$env:APP_ENV = "development"
$env:API_HOST = "127.0.0.1"
$env:API_PORT = "8000"
$env:CORS_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000"
```

`APP_ENV` accepts `development` or `production`. Development mode enables FastAPI debug error reporting. `API_HOST` and `API_PORT` document the server bind address; pass those values to Uvicorn when overriding the defaults:

```bash
uvicorn app.main:app --reload --host "$API_HOST" --port "$API_PORT"
```

In Windows PowerShell, use `$env:API_HOST` and `$env:API_PORT` in the Uvicorn command instead.

Do not put credentials in this example file. No secrets are required by the backend at this stage.

## Run the development server

From `backend/`, with the virtual environment activated:

```bash
uvicorn app.main:app --reload
```

The server uses `127.0.0.1:8000` by default. Interactive API documentation is available at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs). The frontend separately uses `frontend/.env.local` for `NEXT_PUBLIC_API_BASE_URL`; the root `.env.example` is a repository-level reference and is not loaded automatically by either application.

## Health check

`GET /api/v1/health` returns HTTP 200 when the API process is responsive:

```json
{
  "status": "ok",
  "service": "ai-data-analyst-api"
}
```

This endpoint does not indicate the health of any external service.

To check it directly while the server is running:

```bash
curl http://127.0.0.1:8000/api/v1/health
```

For browser requests, FastAPI permits the two local frontend origins listed above. Set `CORS_ORIGINS` to a comma-separated list of explicit origins when configuring another deployment; production origins are not preconfigured.

## Run tests

From `backend/`, with development dependencies installed:

```bash
pytest
```

Tests use FastAPI's in-process test client and do not start a separate server or contact external services.
