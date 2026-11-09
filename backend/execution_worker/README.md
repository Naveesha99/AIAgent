# Execution Worker Image

This directory contains an isolated Python runtime and the security policy for a future pandas analysis execution engine. Phase 5.2 prepares and verifies the image. Phase 5.3 adds a centralized Docker sandbox policy that defines non-root execution, network isolation, filesystem restrictions, resource limits, process isolation, and cleanup rules. The worker still does not accept jobs, execute user-generated code, call the API, or apply the policy at runtime. It performs a startup health check and then remains idle until the container is stopped.

The sandbox policy is implemented as configuration and static/runtime verification only. It is not yet wired into an execution runtime, so this phase does not claim that generated code can be executed securely.

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
    ├── health.py
    └── main.py
```

The worker is independent of the FastAPI application. It does not copy `backend/app`, import Pydantic or Gemini SDKs, or install backend API dependencies. The backend-side sandbox policy lives in `backend/app/core/sandbox.py`, `backend/app/core/config.py`, and `backend/app/services/sandbox_config.py`; it is not baked into the worker image.

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

## Sandbox architecture (Phase 5.3)

The sandbox policy is centralized in the backend so that image defaults are separate from per-execution security rules. The policy produces a validated `SandboxContainerSpec`; a future execution runtime is expected to pass those settings to Docker when it starts one container per execution. This phase does not start job containers or connect the worker to the API.

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
future Docker execution runtime (not implemented in Phase 5.3)
```

Each future execution must use its own container, a generated container name derived from its canonical execution identifier, and no shared writable filesystem. A container name is never accepted from a user or from an execution request.

### Implemented controls vs. pending runtime integration

| Control | Phase 5.3 status |
| --- | --- |
| Non-root user, no privileged mode, capability drop, no-new-privileges | Defined and validated in one reusable policy. Verified against Docker when available. |
| `none` network mode, no ports, no DNS or external routes | Defined in the policy. Verified with a real container when Docker is available. |
| Read-only root, read-only single-file dataset mount, bounded `/tmp` | Defined in the policy. Verified with a real container when Docker is available. |
| CPU, memory, swap, process, temporary-storage, timeout, and output limits | Defined and validated. Docker enforces CPU, memory, swap, process, and tmpfs limits; timeout and output-byte enforcement still require the future runtime. |
| Container naming, labels, and cleanup arguments | Helpers are implemented and tested. Cleanup execution and orphan reconciliation are pending the execution runtime. |
| API-to-Docker integration, job queue, execution, AST validation, self-correction | Not implemented in this phase. |

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

`/tmp` is an in-memory temporary filesystem bounded by `SANDBOX_TMP_SIZE`. It is mounted with `noexec`, `nosuid`, and `nodev` and is destroyed when the container is removed. The future runtime should prefer no writable output or a per-execution bounded output directory; it must not share a writable layer between jobs.

Only read-only inputs, a bounded temporary filesystem, and a read-only runtime are available. Host directories with source code, credentials, configuration files, or unrelated datasets are never mounted.

## Resource limits and defaults

| Setting | Default | Validation range | Enforcement |
| --- | --- | --- | --- |
| `SANDBOX_MEMORY_LIMIT` | `512m` | 64 MiB–4 GiB | Docker `--memory` |
| `SANDBOX_MEMORY_SWAP_LIMIT` | Memory limit (swap disabled) | 64 MiB–4 GiB, at least memory | Docker `--memory-swap` |
| `SANDBOX_CPU_LIMIT` | `1.0` | 0.1–4.0 CPUs | Docker `--cpus` |
| `SANDBOX_PIDS_LIMIT` | `64` | 16–512 processes | Docker `--pids-limit` |
| `SANDBOX_TMP_SIZE` | `64m` | 8 MiB–1 GiB | Docker tmpfs `size=` |
| `SANDBOX_EXECUTION_TIMEOUT_SECONDS` | `30` | >0–600 seconds | Future runtime wall-clock timeout |
| `SANDBOX_MAX_STDOUT_BYTES` | `100000` | 1–2,000,000 bytes | Future runtime output bound |
| `SANDBOX_MAX_STDERR_BYTES` | `50000` | 1–2,000,000 bytes | Future runtime output bound |
| Open files | 256 soft/hard | Fixed conservative value | Docker `--ulimit nofile` |
| Core dumps | 0 | Fixed conservative value | Docker `--ulimit core` |

Defaults are conservative for a small analytics workload and remain configurable for later workload testing. Values are validated at application startup; unsafe values such as `SANDBOX_NETWORK_MODE=bridge`, `SANDBOX_READ_ONLY_ROOT=false`, a swap limit below the memory limit, or an unknown seccomp profile are rejected.

Execution requests cannot override these settings. `ExecutionRequest` rejects unknown fields such as `network_mode`, `privileged`, `cap_add`, `docker_image`, `memory_limit_bytes`, `pids_limit`, `mounts`, `environment`, or `container_name`. The future runtime must also clamp a requested timeout to the smaller of the request timeout, the execution maximum, and the sandbox hard timeout.

### Timeout vs. resource exhaustion

- An **execution timeout** means the future runtime's wall-clock limit expired and the container was stopped. It maps to `execution_timeout` and may be retryable.
- **Resource exhaustion** means Docker reports an out-of-memory kill (`State.OOMKilled`) or another configured resource was exhausted. It maps to `resource_limit_exceeded` and is not retryable by default.
- A process killed by a signal (for example exit code 137) is not classified from the exit code alone. The future runtime must pass the timeout, cancellation, and `OOMKilled` flags into `classify_sandbox_termination` so timeout, cancellation, and resource exhaustion remain distinguishable.

Docker does not natively truncate stdout or stderr, so byte limits remain application-level requirements for the future runtime. They are defined here so the request and result contracts can enforce them consistently.

## Process isolation and lifecycle

Each future execution uses its own container. The policy forbids shared writable filesystems, host PID namespaces, host IPC namespaces, and a shared network namespace. The process count is bounded by `SANDBOX_PIDS_LIMIT`; open files and core dumps are additionally bounded.

Container identity and tracking are generated, not user-supplied:

- Container names use `ai-data-analyst-sandbox-<canonical-execution-uuid>`.
- Every container carries `ai-data-analyst.sandbox=true` and `ai-data-analyst.execution-id=<uuid>` labels.
- The future backend can list managed containers with `docker ps --all --filter label=ai-data-analyst.sandbox=true`.
- The future runtime must remove its own container in a `finally` cleanup path on success, failure, timeout, cancellation, or startup error.

### Cleanup strategy

The policy exposes argument builders, not an executing job lifecycle:

- `sandbox_cleanup_args(execution_id)` returns `docker rm --force` for one generated container name.
- `sandbox_named_cleanup_args(name)` accepts only names generated by the policy.
- `sandbox_sweep_args()` lists all managed containers, including stopped ones.
- `sandbox_sweep_remove_args()` provides the removal prefix for a label-scoped orphan sweep.

A future runtime should combine immediate cleanup in `finally` with a startup or periodic label-scoped sweep for containers left behind by a crashed orchestrator. Cleanup must never delete host files or run host-level path removal. The policy deliberately does not use automatic removal alone, because explicit cleanup remains possible after a failed or timed-out container and can be reconciled by labels.

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

## Remaining limitations and operational considerations

- No generated Python code is executed, compiled, evaluated, or AST-validated in this phase.
- No API-to-Docker integration, job queue, worker orchestration, result retrieval, persistence, or agent self-correction exists.
- The sandbox policy is not yet applied by a running execution runtime. Passing these tests and verification does not prove that future generated code can be executed securely.
- Docker isolation is one layer. A future phase still needs request validation, wall-clock timeout and output enforcement, result sanitization, cancellation, and runtime error mapping.
- The current input mount is a single read-only file from the validated dataset root. The future runtime should stage a copy or use no-follow file handling to close the gap between path validation and container start.
- CPU and process limits reduce abuse but do not make arbitrary Python safe. A later phase should consider additional controls such as a stricter custom seccomp profile, read-only package installation prevention, and per-execution network policy review.
- Local Docker is trusted infrastructure. A compromised Docker daemon or host kernel is outside the isolation provided by this policy.
- Resource limits must be revisited after real workload testing; the defaults are intentionally small.

## Current limitations

- No Python code execution, `exec`, `eval`, `compile`, AST validation, or runtime sandbox enforcement.
- No API integration, job submission, result retrieval, queue, worker protocol, or orchestration.
- No network listener or exposed ports.
- No fake execution results and no claims that analysis has run.
- Startup health verification checks the runtime environment only; it does not prove that any future execution policy is secure.

The worker remains idle or exits after `--once` until a later phase defines and implements the execution protocol.
