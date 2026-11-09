# Execution Worker Image

This directory contains an isolated Python runtime for a future pandas analysis execution engine. Phase 5.2 prepares and verifies the image only. The worker does not accept jobs, execute user-generated code, call the API, or enforce a sandbox. It performs a startup health check and then remains idle until the container is stopped.

## Directory structure

```text
execution_worker/
├── Dockerfile
├── requirements.txt
├── .dockerignore
├── verify_image.sh
└── worker/
    ├── __init__.py
    ├── health.py
    └── main.py
```

The worker is independent of the FastAPI application. It does not copy `backend/app`, import Pydantic or Gemini SDKs, or install backend API dependencies.

## Runtime and installed libraries

- Base image: `python:3.12-slim-bookworm`
- Python: `3.12`
- Worker name: `ai-data-analyst-execution-worker`
- Worker version: `0.1.0`
- Runtime user: UID/GID `10001:10001`
- Working directory: `/worker`

Pinned packages:

| Package | Version |
| --- | --- |
| pandas | 2.2.3 |
| NumPy | 2.0.2 |
| python-dateutil | 2.9.0.post0 |
| pytz | 2024.2 |
| six | 1.17.0 |
| tzdata | 2024.2 |

The backend API supports Python 3.9 or later. The worker uses Python 3.12, which is independently maintained and within the backend's supported language range. SciPy is not installed because no existing backend analytics feature imports it. FastAPI, Uvicorn, Pydantic, Gemini SDKs, database clients, and frontend packages are intentionally excluded.

The image installs dependencies only at build time. The worker has no package-install path, and future user code must not be allowed to install packages dynamically.

## Build

Run from the repository root:

```bash
docker build --tag ai-data-analyst-worker:dev ./backend/execution_worker
```

The build context must be `backend/execution_worker`. The Dockerfile copies only `requirements.txt` and `worker/`. `backend/.env`, uploaded datasets, the backend virtual environment, caches, and Git history are outside this context and are also excluded by `.dockerignore`. Do not build with `backend/` as the context unless the Dockerfile copy paths and ignore rules are updated deliberately.

The Dockerfile uses a single stage because the pinned analytics packages have binary wheels. No compiler or system build packages are required in the final image.

## Start and stop

Perform a one-shot startup health check:

```bash
docker run --rm ai-data-analyst-worker:dev --once
```

Start the idle worker:

```bash
docker run --rm --name ai-data-analyst-worker ai-data-analyst-worker:dev
```

Stop it gracefully with another terminal or process manager:

```bash
docker stop --time 10 ai-data-analyst-worker
```

The process handles `SIGTERM` and `SIGINT`, logs shutdown, and exits with status `0`. If startup health verification fails, the worker exits with status `1`.

The worker does not expose an HTTP server or other network listener. No ports are published or exposed by the image.

## Verify

Run the verification script from the repository root when Docker is available:

```bash
./backend/execution_worker/verify_image.sh
```

To use another image tag:

```bash
IMAGE=ai-data-analyst-worker:verify ./backend/execution_worker/verify_image.sh
```

The script builds the image and checks:

- One-shot startup health verification.
- Runtime UID `10001`.
- Python major/minor version `3.12`.
- Importability of pandas and NumPy.
- Worker health output and version metadata.
- Absence of FastAPI, Google SDK, HTTP client, Pydantic, and Uvicorn packages.
- Absence of environment files, uploaded datasets, and key material under `/worker`.
- Graceful `SIGTERM` shutdown with exit code `0`.
- Final image size.

Check health directly:

```bash
docker run --rm --entrypoint python ai-data-analyst-worker:dev -m worker.health
```

Inspect packages:

```bash
docker run --rm --entrypoint python ai-data-analyst-worker:dev -m pip list
```

Inspect the image size:

```bash
docker image inspect --format '{{.Size}}' ai-data-analyst-worker:dev
```

If Docker is unavailable, run the static backend tests and report that build, runtime, package, and shutdown checks could not be executed:

```bash
cd backend
python -m pytest -q tests/test_phase52_execution_worker.py
```

## Recommended future container restrictions

These restrictions must be applied when a later phase launches the worker as an execution container. The image alone is not a secure sandbox.

- Run with a read-only root filesystem. Mount only a read-only dataset input and a bounded temporary output location.
- Use `--cap-drop=ALL` and `--security-opt=no-new-privileges`; never run privileged.
- Do not mount the Docker socket or any host control socket.
- Disable network access by default, or restrict egress to an explicitly reviewed destination.
- Apply CPU, memory, process, file-size, open-file, and wall-clock limits.
- Apply an ephemeral writable layer with a size limit and clean it after every execution.
- Keep the non-root UID/GID, use a restrictive seccomp profile, and avoid host path mounts other than validated inputs.
- Inject only per-job configuration through a controlled protocol. Never bake API keys, database credentials, or environment files into the image.
- Treat container isolation as one layer only; the later execution phase still needs request validation, resource enforcement, output bounds, and a hardened runtime policy.

## Current limitations

- No Python code execution, `exec`, `eval`, `compile`, AST validation, or sandbox enforcement.
- No API integration, job submission, result retrieval, queue, worker protocol, or orchestration.
- No network listener or exposed ports.
- No fake execution results and no claims that analysis has run.
- Startup health verification checks the runtime environment only; it does not prove that any future execution policy is secure.

The worker remains idle or exits after `--once` until a later phase defines and implements the execution protocol.
