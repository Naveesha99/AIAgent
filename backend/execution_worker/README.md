# Execution Worker Image

This directory contains the isolated Python runtime used by the Phase 5.4 Docker execution service. Phase 5.3 centralized and verified the sandbox policy; Phase 5.4 applies that policy to one container per execution, captures bounded output and results, and removes the container after terminal outcomes. Execution remains disabled by default and is enabled only with `EXECUTION_ENABLED=true` and an available Docker daemon and worker image.

Generated code runs only inside the restricted container, never in the FastAPI process. This is a layered containment boundary, not a guarantee that arbitrary code is safe. There is no AST validator, execution queue, persistence, agent self-correction, or chart rendering.

## Directory structure

```text
execution_worker/
├── Dockerfile
├── requirements.txt
├── .dockerignore
├── verify_image.sh
├── verify_sandbox.py
└── worker/
    ├── __init__.py
    ├── execute.py
    ├── health.py
    └── main.py
```

The worker is independent of the FastAPI application. It does not copy `backend/app`, import Pydantic or Gemini SDKs, or install backend API dependencies. The backend-side sandbox policy lives in `backend/app/core/sandbox.py`, `backend/app/core/config.py`, and `backend/app/services/sandbox_config.py`; the Docker execution service is in `backend/app/services/docker_execution_service.py`.

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

The image installs dependencies only at build time. The worker has no package-install path, and generated code cannot install packages dynamically because it has no network access and the root filesystem is read-only.

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

## Sandbox architecture (Phases 5.3–5.4)

The sandbox policy is centralized in the backend so that image defaults are separate from per-execution security rules. It produces a validated `SandboxContainerSpec`, which the Docker execution service applies when starting one container per execution.

```text
API settings (.env)
        │
        ▼
backend/app/core/config.py ──► backend/app/core/sandbox.py
        │                       validated policy + container spec
        ▼
backend/app/services/sandbox_config.py
        │                       timeout caps + structured termination errors
        ▼
backend/app/services/docker_execution_service.py ──► Docker CLI
        │                                               │ restricted container
        │                                               ▼
        │                                         worker.execute
        │                                               │ bounded protocol frames
        ▼                                               ▼
execution API ◄────────────────────────────── lifecycle + validated result
```

Each execution uses its own container, a generated container name derived from its canonical execution identifier, and no shared writable filesystem. A container name is never accepted from a user or from an execution request.

### Implemented controls vs. remaining work

| Control | Status |
| --- | --- |
| Non-root user, no privileged mode, capability drop, no-new-privileges | Defined and validated in one reusable policy. Verified against Docker when available. |
| `none` network mode, no ports, no DNS or external routes | Defined in the policy. Verified with a real container when Docker is available. |
| Read-only root, read-only single-file dataset mount, bounded `/tmp` | Defined in the policy. Verified with a real container when Docker is available. |
| CPU, memory, swap, process, and temporary-storage limits | Defined, validated, and passed to Docker. |
| Wall-clock timeout and independent output byte limits | Enforced by the Phase 5.4 runtime; output is streamed and retained only within configured limits. |
| Container naming, labels, and cleanup | Generated IDs and labels are applied; the service kills and force-removes the specific container in cleanup paths. |
| API-to-Docker integration and isolated code execution | Implemented behind `EXECUTION_ENABLED`; disabled by default. |
| Job queue, AST validation, persistence, self-correction, charts | Not implemented. |

## Container security settings

The centralized policy applies these Docker settings:

| Setting | Value | Purpose |
| --- | --- | --- |
| Runtime user | `10001:10001` | Uses the Phase 5.2 non-root worker user; blocks root-owned file access and privilege escalation. |
| Privileged mode | Never enabled | Prevents the container from receiving host devices and kernel-level privileges. |
| Linux capabilities | `--cap-drop=ALL`, no additions | Removes capabilities such as `SYS_ADMIN`, `NET_RAW`, and `MKNOD` from untrusted code. |
| Privilege escalation | `--security-opt=no-new-privileges:true` | Prevents setuid/setgid or file capabilities from granting more privilege. |
| Seccomp | Docker's built-in default profile | Restricts risky system calls. The policy never sets `seccomp=unconfined`. |
| Root filesystem | `--read-only` | Makes the worker runtime and system files immutable during execution. |
| IPC namespace | `--ipc=none` | Isolates shared-memory IPC from the host and other containers. |
| PID namespace | Docker's private default | The policy never uses `--pid=host`; one execution cannot inspect host processes. |
| Docker socket | Never mounted | The container cannot control the Docker daemon or start sibling containers. |
| Ports | None published or exposed | The worker cannot receive inbound connections or expose host services. |
| Environment | Fixed safe base plus validated non-secret keys | Prevents API keys, tokens, credentials, and environment files from entering the container. |

The generated command contains no `--privileged`, `--publish`, `--expose`, `--pid=host`, `--ipc=host`, `--network=host`, Docker socket mount, or `--env-file`.

## Network isolation

Execution containers use Docker's `none` network mode. This is enforced by Docker configuration, not by prompt instructions to the generated Python:

- There is no external interface, default route, DNS resolver, or published port.
- The container cannot reach the internet, the local network, host services, other containers, backend APIs, or cloud metadata endpoints.
- No ports are published from the worker image.
- Future runtime verification must keep the container in `none` network mode; adding any bridge, host, overlay, or custom network requires a separate security review.

The verification script confirms that a real container has no route, cannot connect to an external IP, and cannot resolve DNS.

## Filesystem layout

```text
/input/     Read-only dataset input mounted as /input/dataset.csv
/tmp/       Bounded, writable tmpfs; noexec, nosuid, nodev
/worker/    Worker runtime and Python packages, read-only
```

The policy mounts exactly one selected dataset file, never the backend storage directory. Sibling datasets, source code, credentials, configuration, and unrelated host directories are not visible. The host dataset path is resolved and must remain inside the permitted dataset root; symlinks that escape that root and socket files such as `docker.sock` are rejected.

`/tmp` is an in-memory temporary filesystem bounded by `SANDBOX_TMP_SIZE`. It is mounted with `noexec`, `nosuid`, and `nodev` and is destroyed when the container is removed. The current runner writes no output files; future changes must not share a writable layer between jobs.

Only read-only inputs, a bounded temporary filesystem, and a read-only runtime are available. Host directories with source code, credentials, configuration files, or unrelated datasets are never mounted.

## Resource limits and defaults

| Setting | Default | Validation range | Enforcement |
| --- | --- | --- | --- |
| `SANDBOX_MEMORY_LIMIT` | `512m` | 64 MiB–4 GiB | Docker `--memory` |
| `SANDBOX_MEMORY_SWAP_LIMIT` | Memory limit (swap disabled) | 64 MiB–4 GiB, at least memory | Docker `--memory-swap` |
| `SANDBOX_CPU_LIMIT` | `1.0` | 0.1–4.0 CPUs | Docker `--cpus` |
| `SANDBOX_PIDS_LIMIT` | `64` | 16–512 processes | Docker `--pids-limit` |
| `SANDBOX_TMP_SIZE` | `64m` | 8 MiB–1 GiB | Docker tmpfs `size=` |
| `SANDBOX_EXECUTION_TIMEOUT_SECONDS` | `30` | >0–600 seconds | Backend wall-clock timeout; requests are clamped to this and the execution maximum |
| `SANDBOX_MAX_STDOUT_BYTES` | `100000` | 1–2,000,000 bytes | Worker/backend stdout capture ceiling |
| `SANDBOX_MAX_STDERR_BYTES` | `50000` | 1–2,000,000 bytes | Worker/backend stderr capture ceiling |
| Open files | 256 soft/hard | Fixed conservative value | Docker `--ulimit nofile` |
| Core dumps | 0 | Fixed conservative value | Docker `--ulimit core` |

Defaults are conservative for a small analytics workload and remain configurable for later workload testing. Values are validated at application startup; unsafe values such as `SANDBOX_NETWORK_MODE=bridge`, `SANDBOX_READ_ONLY_ROOT=false`, a swap limit below the memory limit, or an unknown seccomp profile are rejected.

Execution requests cannot override these settings. `ExecutionRequest` rejects unknown fields such as `network_mode`, `privileged`, `cap_add`, `docker_image`, `memory_limit_bytes`, `pids_limit`, `mounts`, `environment`, or `container_name`. The runtime clamps a requested timeout to the smaller of the request timeout, the execution maximum, and the sandbox hard timeout.

### Timeout vs. resource exhaustion

- An **execution timeout** means the runtime's wall-clock limit expired and Docker was asked to kill the container. It maps to `execution_timeout` and may be retryable.
- **Resource exhaustion** means Docker reports an out-of-memory kill (`State.OOMKilled`) or another configured resource was exhausted. It maps to `resource_limit_exceeded` and is not retryable by default.
- A process killed by a signal (for example exit code 137) is not classified from the exit code alone. The runtime checks timeout, cancellation, and Docker's `OOMKilled` state so these outcomes remain distinguishable when Docker reports enough information.

Docker does not natively truncate stdout or stderr. The worker emits bounded, framed output, and the backend independently caps retained output bytes while draining the Docker stream.

## Process isolation and lifecycle

Each execution uses its own container. The policy forbids shared writable filesystems, host PID namespaces, host IPC namespaces, and a shared network namespace. The process count is bounded by `SANDBOX_PIDS_LIMIT`; open files and core dumps are additionally bounded.

Container identity and tracking are generated, not user-supplied:

- Container names use `ai-data-analyst-sandbox-<canonical-execution-uuid>`.
- Every container carries `ai-data-analyst.sandbox=true` and `ai-data-analyst.execution-id=<uuid>` labels.
- The future backend can list managed containers with `docker ps --all --filter label=ai-data-analyst.sandbox=true`.
- The runtime removes its own container on success, failure, timeout, cancellation, and startup error. Cleanup failure is logged and does not replace an earlier execution error.

### Cleanup strategy

The policy exposes argument builders for exact-container cleanup and label-scoped reconciliation:

- `sandbox_cleanup_args(execution_id)` returns `docker rm --force` for one generated container name.
- `sandbox_named_cleanup_args(name)` accepts only names generated by the policy.
- `sandbox_sweep_args()` lists all managed containers, including stopped ones.
- `sandbox_sweep_remove_args()` provides the removal prefix for a label-scoped orphan sweep.

The current service removes its exact generated container name in `finally`; it does not yet run a startup or periodic label-scoped orphan sweep. A process or daemon crash can therefore leave a labeled container for operator reconciliation. Cleanup never deletes host files or unrelated containers. The policy deliberately does not use automatic removal alone, because explicit cleanup remains possible after failed or timed-out containers.

## Configure

The backend reads the sandbox settings from its normal environment or `backend/.env`:

```bash
export SANDBOX_WORKER_IMAGE=ai-data-analyst-worker:dev
export SANDBOX_NETWORK_MODE=none
export SANDBOX_READ_ONLY_ROOT=true
export SANDBOX_SECCOMP_PROFILE=default
export SANDBOX_MEMORY_LIMIT=512m
# Optional; defaults to the memory limit (swap disabled).
# export SANDBOX_MEMORY_SWAP_LIMIT=512m
export SANDBOX_CPU_LIMIT=1.0
export SANDBOX_PIDS_LIMIT=64
export SANDBOX_TMP_SIZE=64m
export SANDBOX_EXECUTION_TIMEOUT_SECONDS=30
export SANDBOX_MAX_STDOUT_BYTES=100000
export SANDBOX_MAX_STDERR_BYTES=50000
```

These values are separate from the image. Do not bake secrets, host paths, or per-job limits into the Dockerfile.

## Verify the sandbox policy

Static configuration checks do not require Docker:

```bash
cd backend
python -m pytest -q tests/test_phase53_sandbox.py -k "not docker"
```

When Docker is available, build and verify the real container restrictions:

```bash
cd backend
source .venv/bin/activate
python execution_worker/verify_sandbox.py --build
```

Use `--image IMAGE` for another tag, or `--skip-build` to require an existing image. The script checks:

- Non-root UID/GID, empty effective capability set, no-new-privileges, and default seccomp.
- `none` network mode, no route, no external connection, no DNS, and no published ports.
- Read-only root and read-only dataset input; writable bounded `/tmp`.
- No Docker socket, no host source/credential directories, and no sibling dataset.
- Memory, swap, CPU, process, and tmpfs limits in Docker's host configuration.
- Generated container identity and cleanup of the containers it created.

Run the Docker-backed pytest checks as well:

```bash
cd backend
python -m pytest -q tests/test_phase53_sandbox.py -k docker
```

Docker-backed tests skip automatically when Docker or the worker image is unavailable.

## Execution lifecycle and API

The execution interface is mounted at `/api/v1/executions` when `EXECUTION_ENABLED=true`:

- `POST /api/v1/executions` validates the request, assigns a server-generated UUID, starts an asynchronous execution task, and returns `202` with `{ "execution_id": "...", "status": "pending" }`.
- `GET /api/v1/executions/{execution_id}` returns the latest status.
- `GET /api/v1/executions/{execution_id}/result` returns `202` with the latest status while the job is in progress, then the full `ExecutionResult` once terminal.
- `POST /api/v1/executions/{execution_id}/cancel` kills a live container and returns the terminal structured result. Calling it after a terminal state returns that same result.

The service tracks `pending -> queued -> running -> completed|failed|timed_out|cancelled`; invalid transitions are rejected. Pre-start validation or dataset problems are `rejected`. Each result includes generated execution and dataset IDs, start/completion timestamps where applicable, duration, independent stdout/stderr, optional result/error, and only the resource measurements Docker or the worker can report. Runtime code errors are `execution_failed`; Docker startup/availability failures are infrastructure errors; an OOM reported by Docker is `resource_limit_exceeded`.

Lifecycle records are in memory, not persisted. The service prunes terminal history toward 1,000 records but does not evict active executions; there is no queue or concurrency admission control, so bursts of active requests can exceed that retention target. A backend restart loses status and result history.

## Timeout and output capture

The requested timeout is validated against server settings and clamped to the smaller of the request, `EXECUTION_MAX_TIMEOUT_SECONDS`, and `SANDBOX_EXECUTION_TIMEOUT_SECONDS`. The backend enforces a wall-clock deadline, issues `docker kill --signal=KILL` to the generated container name, waits for Docker, captures drained output frames, and force-removes the container. Docker's container termination kills its remaining processes; the PID limit bounds their number. Timeout is different from resource exhaustion: timeout is a server wall-clock deadline (`timed_out` / `execution_timeout`), while resource exhaustion is reported when Docker exposes an OOM state (`failed` / `resource_limit_exceeded`). Docker may not identify every CPU, PID, or tmpfs-limit event distinctly.

The worker redirects Python `stdout` and `stderr` into separate protocol streams. It keeps at most `SANDBOX_MAX_STDOUT_BYTES` and `SANDBOX_MAX_STDERR_BYTES` (also bounded by `EXECUTION_MAX_STDOUT_BYTES` and `EXECUTION_MAX_STDERR_BYTES`). Line breaks are preserved; each stream reports `truncated` and original byte count. The backend drains protocol output incrementally and caps its own retained buffers; Docker CLI diagnostics are not exposed as user output.

## Result contract

Generated code reads the uploaded CSV as the pandas DataFrame variable `df` and must assign the final result to `result`. Only JSON-compatible null, booleans, finite numbers, strings, lists/tuples, and dictionaries with string keys are accepted, along with pandas DataFrame and Series values. A DataFrame is encoded as `{ "columns": [...], "records": [...] }`; a Series is `{ "name": ..., "values": [...] }`. Supported pandas/NumPy scalar values are normalized to finite JSON values. Timestamps are ISO strings; missing pandas values become JSON `null`.

The defaults are `EXECUTION_MAX_RESULT_BYTES=2000000` and `EXECUTION_MAX_RESULT_ROWS=1000`. Serialization is streamed and bounded, with a nesting-depth limit. Oversized results or row counts produce `output_limit_exceeded`; unsupported objects, non-string dictionary keys, non-finite numbers, missing `result`, and serialization errors produce `execution_failed`. Arbitrary Python objects and their `repr()` are never returned.

## Example responses

Successful result (returned by the result endpoint after it is terminal):

```json
{
  "execution_id": "3eb183ce-8a78-4cd5-b902-a8d4fa2c81cd",
  "dataset_id": "16335394-bb59-48aa-b13a-3a1dc91bab12",
  "status": "completed",
  "started_at": "2026-01-01T00:00:00Z",
  "completed_at": "2026-01-01T00:00:01Z",
  "duration_ms": 1000,
  "stdout": {"content": "rows=2\\n", "truncated": false, "original_size_bytes": 7},
  "stderr": {"content": "", "truncated": false, "original_size_bytes": 0},
  "result": {"total": 30},
  "error": null,
  "resource_usage": {
    "cpu_time_ms": 12.5,
    "wall_time_ms": 1000,
    "peak_memory_bytes": 1048576,
    "exit_code": 0,
    "termination_reason": "normal_exit"
  }
}
```

Failed analysis:

```json
{
  "execution_id": "3eb183ce-8a78-4cd5-b902-a8d4fa2c81cd",
  "dataset_id": "16335394-bb59-48aa-b13a-3a1dc91bab12",
  "status": "failed",
  "started_at": "2026-01-01T00:00:00Z",
  "completed_at": "2026-01-01T00:00:01Z",
  "duration_ms": 1000,
  "stdout": {"content": "", "truncated": false, "original_size_bytes": 0},
  "stderr": {"content": "ValueError: invalid analysis\\n", "truncated": false, "original_size_bytes": 29},
  "result": null,
  "error": {
    "code": "execution_failed",
    "category": "execution_failed",
    "message": "The generated analysis code failed.",
    "retryable": false
  },
  "resource_usage": {"exit_code": 0, "termination_reason": "normal_exit"}
}
```

Timed-out analysis:

```json
{
  "execution_id": "3eb183ce-8a78-4cd5-b902-a8d4fa2c81cd",
  "dataset_id": "16335394-bb59-48aa-b13a-3a1dc91bab12",
  "status": "timed_out",
  "started_at": "2026-01-01T00:00:00Z",
  "completed_at": "2026-01-01T00:00:30Z",
  "duration_ms": 30000,
  "stdout": {"content": "started\\n", "truncated": false, "original_size_bytes": 8},
  "stderr": {"content": "", "truncated": false, "original_size_bytes": 0},
  "result": null,
  "error": {
    "code": "execution_timeout",
    "category": "execution_timeout",
    "message": "The execution exceeded the configured time limit.",
    "retryable": true
  },
  "resource_usage": {"termination_reason": "timeout"}
}
```

Measurements unavailable from Docker or the worker remain `null` or absent; they are not estimated. The worker's CPU and peak-memory figures are process-level measurements. Docker's CPU quota and memory limits are hard ceilings, not precise per-job billing metrics.

## Cleanup and verification

Every execution has a generated UUID and corresponding generated Docker name and labels. The service removes only that container with `docker rm --force` after completion, runtime failure, timeout, cancellation, or startup failure. Cleanup failures are logged and do not overwrite the primary execution error. No host-level path cleanup or removal of unrelated containers is used.

There is no periodic orphan sweep yet. If the backend or Docker daemon crashes, a labeled container may remain; operators can inspect the labels and reconcile only containers matching `ai-data-analyst.sandbox=true`. Local filesystem dataset storage and execution records are process-local/single-instance features, not production multi-instance persistence.

Run mocked lifecycle tests, the Docker-backed execution tests (when Docker is available), and existing sandbox tests from `backend/`:

```bash
python -m pytest -q tests/test_phase54_execution.py
python -m pytest -q tests/test_phase53_sandbox.py
python execution_worker/verify_sandbox.py --skip-build
```

## Remaining limitations and operational considerations

- Generated Python is executed with full Python builtins inside the restricted container; no AST/import allowlist is implemented. Docker hardening is a containment layer, not a proof of safety.
- Execution is disabled by default; enabling it exposes code-execution endpoints and requires a trusted, patched Docker daemon and host kernel.
- Lifecycle and result records are in memory and are lost on backend restart. Terminal history is pruned toward 1,000 records; active records are retained, and there is no queue, admission control, retry, or multi-instance coordination.
- There is no periodic orphan-container sweep. A daemon or backend crash can leave a labeled container until operator cleanup.
- Resource measurements are best-effort and platform-dependent; Docker may not expose why every PID, CPU, or temporary-storage boundary caused termination.
- Dataset storage and staging are local filesystem operations and are not production shared storage.
- Resource limits should be revisited after workload testing; defaults are intentionally small.
