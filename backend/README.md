# AI Data Analyst API

The backend uses FastAPI and Pydantic. It provides a process-level health check and accepts bounded CSV file uploads. Uploaded bytes are counted and acknowledged but are not retained or available as a dataset after the request. CSV contents are not parsed or validated in this phase.

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
export MAX_UPLOAD_SIZE_BYTES=10485760
```

On Windows PowerShell:

```powershell
$env:APP_ENV = "development"
$env:API_HOST = "127.0.0.1"
$env:API_PORT = "8000"
$env:CORS_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000"
$env:MAX_UPLOAD_SIZE_BYTES = "10485760"
```

`APP_ENV` accepts `development` or `production`. Development mode enables FastAPI debug error reporting. `MAX_UPLOAD_SIZE_BYTES` is the maximum accepted upload size, defaulting to 10 MiB (10,485,760 bytes). `API_HOST` and `API_PORT` document the server bind address; pass those values to Uvicorn when overriding the defaults:

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

## CSV upload API

`POST /api/v1/datasets/upload` accepts a multipart form upload using the field name `file`:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/datasets/upload \
  -F "file=@sample.csv"
```

The filename must be non-empty and end in `.csv` (case-insensitive). The maximum size defaults to 10 MiB and can be changed with `MAX_UPLOAD_SIZE_BYTES`. The server counts the bytes read from the upload in bounded chunks; it does not rely on the request size header or reported MIME type to enforce this limit. An accepted upload returns HTTP 201 with a response like:

```json
{
  "dataset_id": "16335394-bb59-48aa-b13a-3a1dc91bab12",
  "filename": "sample.csv",
  "size_bytes": 8,
  "content_type": "text/csv",
  "status": "uploaded"
}
```

`dataset_id` is only a generated acknowledgement identifier in this phase; it does not refer to retained data. The upload is read incrementally and closed after processing. No permanent or managed temporary dataset storage is created. File contents, encoding, headers, and CSV validity are not checked yet.

Expected errors:

- **400 Bad Request** — whitespace-only filename, unsupported extension, or read/close failure.
- **413 Payload Too Large** — actual received bytes exceed `MAX_UPLOAD_SIZE_BYTES`.
- **422 Unprocessable Entity** — multipart `file` field is missing, the multipart filename is empty, or the request is malformed.

The endpoint documents these responses in `/docs`.

## Run tests

From `backend/`, with development dependencies installed:

```bash
pytest
```

Tests use FastAPI's in-process test client and do not start a separate server or contact external services.
