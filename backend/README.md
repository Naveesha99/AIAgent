# AI Data Analyst API

The backend uses FastAPI and Pydantic. It provides a process-level health check and validates bounded CSV uploads before storing them in a private local directory. Dataset files expire after a configurable period. This local filesystem storage is intended for development and a single application instance, not production deployment.

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
export MAX_CSV_COLUMNS=200
export MAX_CSV_ROWS=100000
export MAX_CSV_FIELD_SIZE=1048576
export MAX_CSV_VALIDATION_LINES=200000
export DATASET_STORAGE_DIR=data/datasets
export DATASET_TTL_HOURS=24
```

On Windows PowerShell:

```powershell
$env:APP_ENV = "development"
$env:API_HOST = "127.0.0.1"
$env:API_PORT = "8000"
$env:CORS_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000"
$env:MAX_UPLOAD_SIZE_BYTES = "10485760"
$env:MAX_CSV_COLUMNS = "200"
$env:MAX_CSV_ROWS = "100000"
$env:MAX_CSV_FIELD_SIZE = "1048576"
$env:MAX_CSV_VALIDATION_LINES = "200000"
$env:DATASET_STORAGE_DIR = "data/datasets"
$env:DATASET_TTL_HOURS = "24"
```

`APP_ENV` accepts `development` or `production`. Development mode enables FastAPI debug error reporting. `MAX_UPLOAD_SIZE_BYTES` is the maximum accepted upload size, defaulting to 10 MiB (10,485,760 bytes). CSV validation limits default to 200 columns, 100,000 data rows, 1 MiB per field, and 200,000 physical lines. Override them using `MAX_CSV_COLUMNS`, `MAX_CSV_ROWS`, `MAX_CSV_FIELD_SIZE`, and `MAX_CSV_VALIDATION_LINES`, respectively. `DATASET_STORAGE_DIR` defaults to `backend/data/datasets`; a relative override is resolved relative to the backend directory. `DATASET_TTL_HOURS` defaults to 24 hours. `API_HOST` and `API_PORT` document the server bind address; pass those values to Uvicorn when overriding the defaults:

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

The filename must be non-empty and end in `.csv` (case-insensitive). The reported MIME type is informational only. The server counts actual bytes in bounded chunks and stops as soon as the configured file-size limit is exceeded. It then validates the content as strict UTF-8, accepting an optional UTF-8 BOM. The CSV must have non-empty, unique header names and at least one non-empty data row. Every data row must have the same number of fields as the header. Quoted commas, escaped quotes, and quoted multiline values are supported; malformed quoting is rejected. A `.csv` extension or MIME type does not establish that the content is valid CSV.

Validation also stops when any configured resource limit is reached: 200 columns, 100,000 data rows, a 1 MiB field, or 200,000 physical lines by default. The server parses incrementally and does not retain a list of data rows. An accepted upload returns HTTP 201 with a response like:

```json
{
  "dataset_id": "16335394-bb59-48aa-b13a-3a1dc91bab12",
  "filename": "sample.csv",
  "size_bytes": 8,
  "content_type": "text/csv",
  "status": "uploaded"
}
```

`dataset_id` is a server-generated UUID and names the internally stored file; original filenames are display metadata only. Validated bytes are written incrementally to a mode-restricted staging file, then atomically finalized without replacing an existing dataset. Dataset files are stored under `DATASET_STORAGE_DIR`, outside publicly served directories. The uploaded multipart file and staging files are closed or removed after processing.

The default retention period is 24 hours from the stored file's modification time. Expired datasets and partial-upload files older than the same TTL are removed when a new upload arrives, and cleanup can also be invoked explicitly through the storage service. There is no periodic background cleanup. Active staged uploads and files opened through the storage service are protected from cleanup. Local disk storage is process-local, single-instance development storage; it has no database, replication, or production lifecycle guarantees.

Expected errors:

- **400 Bad Request** — invalid filename or CSV content, including unsupported encoding, duplicate/empty headers, missing data rows, inconsistent row widths, malformed CSV, or read/close failure. For example: `{"detail":"The CSV must use valid UTF-8 encoding."}`
- **413 Payload Too Large** — actual received bytes exceed `MAX_UPLOAD_SIZE_BYTES`, or a CSV validation resource limit is exceeded.
- **500 Internal Server Error** — storage is unavailable or safe finalization fails; no successful response is returned.
- **422 Unprocessable Entity** — multipart `file` field is missing, the multipart filename is empty, or the request is malformed.

The endpoint documents these responses in `/docs`.

## Dataset preview API

`GET /api/v1/datasets/{dataset_id}/preview` returns one page from a stored CSV:

```bash
curl "http://127.0.0.1:8000/api/v1/datasets/16335394-bb59-48aa-b13a-3a1dc91bab12/preview?page=1&page_size=10"
```

`page` defaults to `1` and must be at least `1`. `page_size` defaults to `10` and must be between `1` and `100`. The typed response includes the dataset ID and original display filename, total data rows (excluding the header), ordered columns, page rows, and pagination metadata. Empty CSV cells are represented as JSON `null`. Values remain strings, preserving content such as leading zeros; quoted commas, quotes, multiline fields, and UTF-8 BOM are handled by the same strict CSV validation used during upload.

The API scans the stored CSV to validate it, count logical data rows, and collect only the requested page. Consequently, later pages still require a bounded-memory scan of the file. Invalid IDs or pagination values return **422**, missing or expired datasets return **404**, and unreadable/corrupted stored data returns a safe **500** response. The preview endpoint does not expose storage paths or file contents beyond the requested page.

## Dataset metadata API

`GET /api/v1/datasets/{dataset_id}/metadata` scans a stored CSV and returns its row count, ordered columns, missing-value counts, and inferred logical types:

```bash
curl http://127.0.0.1:8000/api/v1/datasets/16335394-bb59-48aa-b13a-3a1dc91bab12/metadata
```

Example response:

```json
{
  "dataset_id": "16335394-bb59-48aa-b13a-3a1dc91bab12",
  "filename": "sample.csv",
  "row_count": 3,
  "column_count": 3,
  "columns": [
    {
      "name": "order_id",
      "data_type": "string",
      "non_null_count": 3,
      "null_count": 0,
      "is_numeric": false,
      "is_date": false,
      "is_categorical": false
    },
    {
      "name": "quantity",
      "data_type": "integer",
      "non_null_count": 3,
      "null_count": 0,
      "is_numeric": true,
      "is_date": false,
      "is_categorical": false
    },
    {
      "name": "region",
      "data_type": "categorical",
      "non_null_count": 2,
      "null_count": 1,
      "is_numeric": false,
      "is_date": false,
      "is_categorical": true
    }
  ],
  "numeric_columns": ["quantity"],
  "date_columns": [],
  "categorical_columns": ["region"]
}
```

Supported logical types are `integer`, `float`, `boolean`, `date`, `datetime`, `categorical`, and `string`. The ordered `columns` list contains each header exactly once. `numeric_columns`, `date_columns`, and `categorical_columns` are derived in that same order; booleans are not numeric and datetimes are included in `date_columns`.

Inference is deterministic and conservative:

- Empty CSV cells count as null; all other cells count as non-null. Blank records are handled the same way as upload and preview validation and are excluded from the data row count. An empty or entirely missing column is typed as `string`.
- Integer and finite decimal/exponent values are numeric. A column is numeric only when every non-empty value matches; digit-only values with leading zeros (such as `00123`) remain strings. Columns whose final name component is `id`, `identifier`, or `uuid` remain strings even when their values are numeric. NaN/Infinity tokens and values that overflow finite floating-point parsing are not inferred as numeric.
- ISO dates and datetimes are recognized, as are slash- or dash-separated day/month/year formats when the order can be established from a value such as `13/02/2025`. Ambiguous dates such as `01/02/2025` alone remain strings. Values must consistently match the same recognized date format.
- Boolean inference recognizes case-insensitive `true`/`false` or `yes`/`no` when the column uses only one of those pairs and contains both values.
- Categorical inference excludes numeric, date, and boolean fields. It requires at least five non-empty cells, at most 50 distinct short values, no more than half as many distinct values as non-empty cells, and repeated values. The distinct-value threshold is also limited by the square root of the number of non-empty cells, with a minimum of five.
- Other columns are `string`; CSV values are never rewritten.

The endpoint uses the same strict, bounded CSV validator as upload and preview, including its configured upload, row, column, field, and physical-line limits. It performs one streaming pass and retains only bounded per-column inference state; it does not persist metadata. Type inference is heuristic, not a guarantee of semantic correctness. Column-name rules may not recognize every identifier naming convention, and text values that happen to satisfy a recognized date format can still be ambiguous.

Malformed IDs return **422**, missing or expired datasets return **404**, and unreadable/corrupted stored data returns a generic **500** without exposing filesystem paths or parser internals.

## Run tests

From `backend/`, with development dependencies installed:

```bash
pytest
```

Tests use FastAPI's in-process test client and isolated temporary storage directories; they do not start a separate server or contact external services. Run only the preview tests with:

```bash
pytest tests/test_dataset_preview.py
```

Run only the metadata tests with:

```bash
pytest tests/test_dataset_metadata.py
```

Run only the storage tests with:

```bash
pytest tests/test_dataset_storage.py
```
