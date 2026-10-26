# AI Data Analyst API

The backend uses FastAPI and Pydantic. It provides a process-level health check and validates bounded CSV uploads before storing them in a private local directory. Dataset files expire after a configurable period. This local filesystem storage is intended for development and a single application instance, not production deployment.

Phase 4.1 adds a provider-independent LLM client abstraction and a Google Gemini implementation. The client is configured from environment variables but is not wired into any endpoint yet, so CSV upload, profiling, data quality, and analytics never send data to Gemini.

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

`backend/.env.example` contains safe backend defaults, including `CORS_ORIGINS`, a comma-separated list of browser origins allowed to access the API. The defaults allow `http://localhost:3000` and `http://127.0.0.1:3000`. CORS does not allow credentials. Create a local `backend/.env` from the example, which is ignored by Git and loaded automatically when present:

```bash
cp .env.example .env
```

Process environment variables take precedence over values in `backend/.env`. The example file itself is not loaded automatically. Set local values in `backend/.env` or export them in your shell. The example also lists the optional Gemini provider settings described below. For example, on macOS/Linux:

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
export DATA_QUALITY_SAMPLE_LIMIT=5
export ANALYTICS_MAX_RESULT_ROWS=1000
export ANALYTICS_MAX_GROUPS=100
export ANALYTICS_MAX_VALUE_FREQUENCIES=10
export DATASET_STORAGE_DIR=data/datasets
export DATASET_TTL_HOURS=24
export LLM_PROVIDER=gemini
export LLM_MODEL=gemini-3.5-flash-lite
export GEMINI_API_KEY=your-api-key-from-google-ai-studio
export LLM_TIMEOUT_SECONDS=30
export LLM_MAX_RETRIES=2
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
$env:DATA_QUALITY_SAMPLE_LIMIT = "5"
$env:ANALYTICS_MAX_RESULT_ROWS = "1000"
$env:ANALYTICS_MAX_GROUPS = "100"
$env:ANALYTICS_MAX_VALUE_FREQUENCIES = "10"
$env:DATASET_STORAGE_DIR = "data/datasets"
$env:DATASET_TTL_HOURS = "24"
$env:LLM_PROVIDER = "gemini"
$env:LLM_MODEL = "gemini-3.5-flash-lite"
$env:GEMINI_API_KEY = "your-api-key-from-google-ai-studio"
$env:LLM_TIMEOUT_SECONDS = "30"
$env:LLM_MAX_RETRIES = "2"
```

`APP_ENV` accepts `development` or `production`. Development mode enables FastAPI debug error reporting. `MAX_UPLOAD_SIZE_BYTES` is the maximum accepted upload size, defaulting to 10 MiB (10,485,760 bytes). CSV validation limits default to 200 columns, 100,000 data rows, 1 MiB per field, and 200,000 physical lines. Override them using `MAX_CSV_COLUMNS`, `MAX_CSV_ROWS`, `MAX_CSV_FIELD_SIZE`, and `MAX_CSV_VALIDATION_LINES`, respectively. `ANALYTICS_MAX_RESULT_ROWS` defaults to 1000 and caps filter/sort result rows. `ANALYTICS_MAX_GROUPS` defaults to 100 and caps returned groups. `ANALYTICS_MAX_VALUE_FREQUENCIES` defaults to 10 and caps categorical top values in summaries. `DATASET_STORAGE_DIR` defaults to `backend/data/datasets`; a relative override is resolved relative to the backend directory. `DATASET_TTL_HOURS` defaults to 24 hours. `API_HOST` and `API_PORT` document the server bind address; pass those values to Uvicorn when overriding the defaults:

```bash
uvicorn app.main:app --reload --host "$API_HOST" --port "$API_PORT"
```

In Windows PowerShell, use `$env:API_HOST` and `$env:API_PORT` in the Uvicorn command instead.

Do not put credentials in `backend/.env.example` or commit a local environment file. A Gemini API key is needed only when an application service constructs the Gemini client; the existing health, upload, preview, metadata, statistics, quality, and analytics endpoints do not use an LLM key.

## Configure the Google Gemini provider

Phase 4.1 provides a provider-independent client interface under `app/services/llm/`. The factory selects the provider from `LLM_PROVIDER`, and only the Gemini implementation is supported in this phase. Constructing the client does not send a request or consume API quota. Actual `generate` calls are not wired to any API endpoint yet.

### Obtain an API key

1. Open [Google AI Studio](https://aistudio.google.com/) and sign in.
2. Select or create the Google Cloud project that should own the API usage.
3. Use the API key controls in AI Studio to create a key for that project.
4. Store the key only in the backend process environment or an untracked local environment file. Never put it in frontend variables such as `NEXT_PUBLIC_*`, source code, test fixtures, documentation, or Git.

### Configure the provider

```bash
export LLM_PROVIDER=gemini
export LLM_MODEL=gemini-3.5-flash-lite
export GEMINI_API_KEY="your-api-key-from-google-ai-studio"
export LLM_TIMEOUT_SECONDS=30
export LLM_MAX_RETRIES=2
```

Windows PowerShell:

```powershell
$env:LLM_PROVIDER = "gemini"
$env:LLM_MODEL = "gemini-3.5-flash-lite"
$env:GEMINI_API_KEY = "your-api-key-from-google-ai-studio"
$env:LLM_TIMEOUT_SECONDS = "30"
$env:LLM_MAX_RETRIES = "2"
```

Defaults and validation:

- `LLM_PROVIDER` defaults to `gemini`; names are case-insensitive. Any other provider is rejected with a clear `LLMConfigurationError` when the client is requested. Keep the provider abstraction when adding another implementation.
- `LLM_MODEL` defaults to `gemini-3.5-flash-lite`, a free-tier-eligible model at the time of writing. A missing or blank model is rejected.
- `GEMINI_API_KEY` has no default and must be non-blank when the Gemini client is constructed. It is held as a secret and is not included in settings representations.
- `LLM_TIMEOUT_SECONDS` defaults to `30` and must be greater than `0` and at most `600`. The timeout is applied both to the SDK HTTP request and to the asynchronous call.
- `LLM_MAX_RETRIES` defaults to `2` and must be between `0` and `5`. It counts retries after the initial attempt; `2` allows at most three attempts.

### Choose an available model

Google changes model availability, free-tier eligibility, and rate limits. The default model is only a starting point:

- Check the [Gemini models list](https://ai.google.dev/gemini-api/docs/models), [pricing and free tier](https://ai.google.dev/gemini-api/docs/pricing), and [rate limits](https://ai.google.dev/gemini-api/docs/rate-limits) for the current project and region.
- Use a model ID shown by Google's documentation or AI Studio. At the time of writing, Google recommends newer models such as `gemini-3.5-flash-lite` and `gemini-3.8-flash` for new projects; older 2.5 models may have restricted access.
- Set `LLM_MODEL` to the selected ID. No source-code change is required.
- Do not assume every model has free-tier access. Confirm eligibility for your project, region, and usage before relying on it.

A model that is unavailable to the project maps to `LLMModelUnavailableError` rather than being retried.

### Timeouts, retries, and errors

Provider-specific SDK errors are converted to internal exceptions that do not include credentials or raw response data:

- `LLMAuthenticationError` and `LLMPermissionError` cover rejected credentials and denied access. They are not retried.
- `LLMModelUnavailableError` covers unsupported or unavailable models. It is not retried.
- `LLMInvalidRequestError` covers malformed requests and other non-retryable client errors. It is not retried.
- `LLMRateLimitError` represents a rate limit. The client retries a bounded number of times and respects `Retry-After` or provider `RetryInfo` guidance when present.
- `LLMQuotaExceededError` represents exhausted quota or billing limits. It is not retried.
- `LLMTimeoutError` and `LLMConnectionError` cover timeouts and network failures and are retried within the configured limit.
- `LLMTransientError` covers temporary server failures and is retried with exponential backoff.
- `LLMResponseError` covers empty or malformed provider responses and is not retried.
- `LLMProviderError` covers unexpected provider failures that cannot be classified safely.

The retry delay starts at 0.5 seconds, doubles per retry, and is capped at 30 seconds. Provider retry guidance is also capped by that bound, so retries never continue indefinitely.

### Troubleshoot provider errors

- **`GEMINI_API_KEY is required`** — set `GEMINI_API_KEY` in the backend environment and restart the backend. Confirm the value is not empty or whitespace.
- **Authentication or permission failure** — verify the key in AI Studio, ensure it belongs to the intended project, and check API restrictions, project status, and billing. Do not print the key while debugging.
- **Model unavailable** — confirm the exact `LLM_MODEL` identifier is available to the project. Switch to a currently listed model. Model access and free-tier eligibility depend on Google's current rollout.
- **Rate limit** — the client retries within the configured limit and honors provider guidance. Reduce request frequency or check the project's rate limits if failures continue.
- **Quota exhausted** — quota failures are not retried. Check AI Studio quotas and billing, wait for the quota window to reset, or use a project with available quota.
- **Timeout** — increase `LLM_TIMEOUT_SECONDS` within the allowed range, check network connectivity, and verify the model is responsive. Timeouts are retried only within `LLM_MAX_RETRIES`.
- **Connection failure** — check outbound network access, DNS, proxy, and firewall settings.
- **Malformed response** — the provider returned no usable text or an unexpected structure. Retry manually, confirm the model ID, and inspect provider status.

API keys and request content are not logged or included in safe error messages. Keep all Gemini configuration on the backend.

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

## Statistical profiling API

`GET /api/v1/datasets/{dataset_id}/statistics` returns descriptive statistics calculated from the stored CSV:

```bash
curl http://127.0.0.1:8000/api/v1/datasets/16335394-bb59-48aa-b13a-3a1dc91bab12/statistics
```

Example response (other fields are omitted here only for brevity):

```json
{
  "dataset_id": "16335394-bb59-48aa-b13a-3a1dc91bab12",
  "filename": "sample.csv",
  "row_count": 3,
  "column_count": 3,
  "column_statistics": [
    {
      "name": "quantity",
      "data_type": "integer",
      "non_null_count": 3,
      "null_count": 0,
      "numeric": {
        "count": 3,
        "mean": 4.0,
        "median": 4.0,
        "standard_deviation": 2.0,
        "variance": 4.0,
        "minimum": 2.0,
        "maximum": 6.0,
        "q1": 3.0,
        "q3": 5.0,
        "iqr": 2.0,
        "sum": 12.0
      },
      "distinct_count": null,
      "mode": null,
      "mode_frequency": null,
      "value_frequencies": [],
      "value_frequencies_truncated": false,
      "earliest": null,
      "latest": null
    }
  ]
}
```

Column statistics follow the Phase 3.1 logical types. Numeric columns include valid numeric count, mean, median, sample standard deviation and sample variance (`ddof=1`), minimum, maximum, sum, Q1, Q3, and IQR. Standard deviation and variance are null with fewer than two valid numeric values. Numeric quartiles use linear interpolation at rank `(n - 1) * q`, matching pandas' default `Series.quantile` interpolation. Numeric values are parsed as finite decimal values; statistics that cannot be represented as finite JSON numbers are returned as `null`.

Categorical and boolean columns include distinct count, mode, and mode frequency. Mode ties are resolved by ascending original string value. Categorical columns include a frequency list capped at 20 values, ordered by descending frequency then value; `value_frequencies_truncated` is true when more than 20 distinct values exist. Boolean columns do not include a frequency list. Other string columns include only distinct count, avoiding large mode/frequency outputs. Date and datetime columns include distinct count and earliest/latest values serialized using ISO 8601; dates use `YYYY-MM-DD`, while datetimes preserve their parsed time and offset. If a datetime column mixes timezone-aware and timezone-naive values, its bounds are null because they cannot be compared unambiguously. Empty CSV cells count as null and are excluded from statistics and frequencies.

Statistics reuse Phase 3.1's type inference and the same CSV validator, limits, and row parsing. The CSV is parsed once per statistics request; numeric observations are retained to calculate exact quantiles, and distinct values are retained for exact distinct counts. Memory therefore grows with numeric observations and distinct strings, within the existing 10 MiB upload, 100,000-row, and 200-column limits. No results are persisted. Inferred types and descriptive summaries are not guarantees of data quality and do not imply causation.

Malformed IDs return **422**, missing or expired datasets return **404**, and unreadable/corrupted stored data returns a generic **500** without exposing filesystem paths or internal exceptions.

## Data quality API

`GET /api/v1/datasets/{dataset_id}/quality` performs read-only checks against the original stored CSV:

```bash
curl http://127.0.0.1:8000/api/v1/datasets/16335394-bb59-48aa-b13a-3a1dc91bab12/quality
```

The typed response includes `dataset_id`, `filename`, row/column counts, a dataset `summary`, ordered `column_quality`, and bounded `issues`. The summary reports missing cells, rows with one or more missing values, duplicate rows, duplicate groups, invalid date values, and potential type inconsistencies. Column summaries include Phase 3.1 inferred type, non-null and missing counts, missing percentage, and applicable invalid-date/type-inconsistency counts and percentages.

Response shape:

```json
{
  "dataset_id": "16335394-bb59-48aa-b13a-3a1dc91bab12",
  "filename": "sample.csv",
  "row_count": 3,
  "column_count": 2,
  "summary": {
    "total_missing_cells": 1,
    "rows_with_missing_values": 1,
    "duplicate_rows": 0,
    "duplicate_groups": 0,
    "invalid_date_values": 0,
    "type_inconsistencies": 0
  },
  "column_quality": [
    {
      "name": "order_date",
      "inferred_type": "date",
      "non_null_count": 3,
      "missing_count": 0,
      "missing_percentage": 0.0,
      "invalid_date_count": 0,
      "invalid_date_percentage": 0.0,
      "type_inconsistency_count": 0,
      "type_inconsistency_percentage": 0.0
    },
    {
      "name": "amount",
      "inferred_type": "float",
      "non_null_count": 2,
      "missing_count": 1,
      "missing_percentage": 33.33333333333333,
      "invalid_date_count": 0,
      "invalid_date_percentage": 0.0,
      "type_inconsistency_count": 0,
      "type_inconsistency_percentage": 0.0
    }
  ],
  "issues": []
}
```

Empty and whitespace-only fields are missing. `0`, `False`, and literal `N/A` are retained as values. Duplicate rows compare every parsed data field; a row repeated three times contributes two duplicate rows and one duplicate group. Repeated values in individual columns alone are not duplicates. Duplicate detection uses collision-resistant row fingerprints. Potential numeric/text inconsistency is reported when at least five values are numeric and at least 80% of non-missing values match that pattern; integer values with leading zeros and identifier columns ending in `id`, `identifier`, or `uuid` are excluded. Mixed boolean spellings are flagged when at least five values use recognized boolean tokens and both `true`/`false` and `yes`/`no` vocabularies appear. Date-like columns use Phase 3.1's supported explicit formats; valid values from other supported date formats are not counted as invalid. Invalid non-empty date values are reported separately from missing values.

Issue examples are capped by `DATA_QUALITY_SAMPLE_LIMIT`, default 5 and configurable from 0 through 20; each returned example is limited to 200 characters. Duplicate counts use the “beyond the first occurrence” convention. Duplicate detection stores fixed-size BLAKE2 fingerprints bounded by the existing 100,000-row limit; an astronomically unlikely fingerprint collision could affect a count.

Analysis is one streaming validation pass and does not use pandas, rewrite the CSV, persist results, or return unrestricted dataset contents. Findings indicate potential quality concerns, not automatic corrections or guarantees of invalid data. Malformed IDs return **422**, missing or expired datasets return **404**, and unreadable/corrupted stored data returns a generic **500** without filesystem paths or internal exceptions.

## Analytics API

`POST /api/v1/datasets/{dataset_id}/analytics` runs one validated analytics operation against a stored CSV:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/datasets/16335394-bb59-48aa-b13a-3a1dc91bab12/analytics \
  -H "Content-Type: application/json" \
  -d '{"operation":"summarize"}'
```

The request body is a discriminated union keyed by `operation`: `filter`, `sort`, `group`, `aggregate`, or `summarize`. `filter` requires a `filters` object; the other operations may include one. Unsupported fields are rejected.

### Filtering

`filters` contains a `combine` value (`and`, the default, or `or`) and one or more `conditions`. Each condition has a `column`, an `operator`, and, except for null checks, a `value`.

Supported operators:

- Comparisons: `equals`, `not_equals`, `greater_than`, `greater_than_or_equal`, `less_than`, `less_than_or_equal`.
- Strings: `contains`, `starts_with`, `ends_with`.
- Null checks: `is_null`, `is_not_null`.

```json
{
  "operation": "filter",
  "filters": {
    "combine": "and",
    "conditions": [
      {"column": "region", "operator": "equals", "value": "North"},
      {"column": "amount", "operator": "greater_than", "value": 20}
    ]
  },
  "columns": ["name", "amount"],
  "sort_by": [{"column": "amount", "direction": "desc"}],
  "limit": 50
}
```

```json
{
  "dataset_id": "16335394-bb59-48aa-b13a-3a1dc91bab12",
  "operation": "filter",
  "columns": ["name", "amount"],
  "rows": [
    {"name": "Zeta", "amount": "60"},
    {"name": "Gamma", "amount": "30"}
  ],
  "row_count": 6,
  "result_count": 2,
  "truncated": false,
  "matched_count": 2,
  "total_groups": null,
  "summary": null
}
```

An empty CSV cell is null. Only `is_null` and `is_not_null` match missing values; every other operator excludes nulls, including `not_equals`. Whitespace-only cells are treated as values, not nulls.

Operators are checked against the inferred column type:

- `equals` and `not_equals` support numeric, string, categorical, boolean, date, and datetime columns.
- Ordering comparisons support numeric, date, and datetime columns.
- String operators require a string or categorical column and a string value.
- Boolean comparisons accept booleans or case-insensitive `true`/`false` and `yes`/`no`.
- Date values must be valid. Ambiguous values such as `01/02/2025` are rejected; use an unambiguous value such as `2025-02-01`.

Numeric comparisons parse finite decimal values. Date filters compare parsed dates rather than raw text. Datetimes are compared in UTC; values without an offset are treated as UTC.

### Sorting

`sort_by` accepts one or more `{ "column": ..., "direction": "asc" | "desc" }` entries. Sorting is type-aware and stable: rows with equal values retain their previous order. Missing values sort after non-missing values in ascending order and before non-missing values in descending order.

```json
{
  "operation": "sort",
  "filters": {
    "conditions": [
      {"column": "region", "operator": "is_not_null"}
    ]
  },
  "sort_by": [
    {"column": "region", "direction": "asc"},
    {"column": "amount", "direction": "desc"}
  ],
  "columns": ["region", "name", "amount"],
  "limit": 100
}
```

`columns` is an optional projection for `filter` and `sort`; it does not have to include the sort columns.

### Grouping and aggregation

`group` requires one or more `group_by` columns. `aggregate` accepts an optional `group_by` list. If `group` has no aggregations, it counts rows per group. An empty group value is preserved as an explicit `null` group.

```json
{
  "operation": "group",
  "group_by": ["region"],
  "aggregations": [
    {"function": "sum", "column": "amount"},
    {"function": "count", "column": "price"},
    {"function": "nunique", "column": "name", "alias": "unique_names"}
  ]
}
```

```json
{
  "dataset_id": "16335394-bb59-48aa-b13a-3a1dc91bab12",
  "operation": "group",
  "columns": ["region", "sum_amount", "count_price", "unique_names"],
  "rows": [
    {"region": null, "sum_amount": 40.0, "count_price": 1, "unique_names": 1},
    {"region": "North", "sum_amount": 100.0, "count_price": 2, "unique_names": 3},
    {"region": "South", "sum_amount": 70.0, "count_price": 2, "unique_names": 2}
  ],
  "row_count": 6,
  "result_count": 3,
  "truncated": false,
  "matched_count": 6,
  "total_groups": 3,
  "summary": null
}
```

Groups are ordered by group key using the same type-aware ordering as sorting, with missing values first. A `group` request without aggregations counts rows per group under `count_rows`. Default aggregation aliases use `<function>_<column>`, while a `count` without a column uses `count_rows`. Group and aggregation output names are unique; `alias` can rename an aggregation. Aggregations run after filters.

Aggregation semantics:

- `count` counts non-null values of the specified column. When `count` has no `column`, it counts rows. This is the same in grouped and ungrouped results.
- `nunique` counts distinct non-null values and works for any column type.
- `sum`, `mean`, `median`, and `std` require a numeric column. `std` is the sample standard deviation and is null with fewer than two values. `sum`, `mean`, and `median` are null when no non-null numeric values remain.
- `min` and `max` support numeric, date, datetime, string, and categorical columns. Numeric results are numbers, dates and datetimes are ISO strings, and other results are strings. Boolean columns are rejected.
- Missing values are excluded from every aggregation. They are not converted to zero.

An ungrouped `aggregate` always returns one row, even when filters match no rows; row counts are then `0` and numeric aggregations are `null`.

### Summaries

`summarize` returns dataset counts, ordered column names and inferred types, numeric summaries, categorical summaries, and missing-value counts. It reuses the metadata and statistical profiling logic from the metadata and statistics endpoints. `top_values_limit` controls how many categorical values are returned; it is capped by `ANALYTICS_MAX_VALUE_FREQUENCIES`.

```json
{
  "operation": "summarize",
  "top_values_limit": 5
}
```

The response has an empty top-level `rows` list and a `summary` object. The summary includes `row_count`, `column_count`, `columns`, `numeric`, `categorical`, and `missing`. Numeric summaries contain `count`, `mean`, `median`, `minimum`, `maximum`, and `standard_deviation`. Categorical summaries contain `distinct_count`, bounded `top_values`, and a `truncated` flag. Missing summaries contain counts and percentages. The top-level `truncated` flag is true when any categorical top-value list or grouped summary was truncated.

Filters may be combined with `summarize`; `summary.row_count` then reports the number of matching rows while the top-level `row_count` remains the dataset total. To add grouped summaries, provide `group_by` and optional `aggregations`. Grouped results appear in `summary.groups`, with `group_by`, `total_groups`, and `groups_truncated`.

### Result limits and truncation

- `filter` and `sort` return at most `limit` rows, capped by `ANALYTICS_MAX_RESULT_ROWS` (default 1000). If more rows match, `truncated` is true and `matched_count` reports the full match count.
- `group` and `aggregate` return at most `limit` groups, capped by `ANALYTICS_MAX_GROUPS` (default 100). `total_groups` reports all groups found, and `truncated` is true when some are omitted.
- Categorical top-value lists are capped by `ANALYTICS_MAX_VALUE_FREQUENCIES`.
- Result limits are validated as positive integers. Requested limits above the configured maximum are clamped to the maximum.

Analytics never modifies the stored CSV. Filtering without sorting streams rows and retains at most the result limit plus one. Sorting must retain all matching rows in memory before ordering them. Grouping keeps per-group aggregation state for every group found, even when only the first configured number of groups is returned; median and standard deviation retain each group's numeric observations, and distinct counts retain each group's values. Summaries reuse the existing statistical profiler, which retains numeric observations and distinct strings. All operations are bounded by the configured CSV size, row, column, field, and line limits; no database or separate analytics process is used.

Malformed dataset IDs and invalid request parameters return **422**. Missing or expired datasets return **404**. Unreadable or corrupted stored data returns a generic **500** without filesystem paths or parser internals.

## Run tests

From `backend/`, with development dependencies installed:

```bash
pytest
```

Tests use FastAPI's in-process test client and isolated temporary storage directories; they do not start a separate server or contact external services. The Phase 4.1 tests inject fake Gemini clients and mocked SDK outcomes, so they do not require `GEMINI_API_KEY`, an internet connection, or paid API usage.

Run only the Gemini configuration and provider-selection tests with:

```bash
pytest tests/test_phase41_llm_config.py
```

Run only the mocked Gemini client, error, and retry tests with:

```bash
pytest tests/test_phase41_gemini_client.py
```

Run only the regression tests that exercise health, upload, profiling, quality, and analytics without Gemini configuration with:

```bash
pytest tests/test_phase41_without_gemini.py
```

Run only the preview tests with:

```bash
pytest tests/test_dataset_preview.py
```

Run only the metadata tests with:

```bash
pytest tests/test_dataset_metadata.py
```

Run only the statistical profile tests with:

```bash
pytest tests/test_dataset_statistics.py
```

Run only the data quality tests with:

```bash
pytest tests/test_dataset_quality.py
```

Run only the analytics tests with:

```bash
pytest tests/test_dataset_analytics.py
```

Run only the storage tests with:

```bash
pytest tests/test_dataset_storage.py
```
