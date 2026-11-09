#!/bin/sh
set -eu

IMAGE="${IMAGE:-ai-data-analyst-worker:dev}"
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
CONTAINER_NAME="ai-data-analyst-worker-verify-$$"

cleanup() {
    docker rm --force "$CONTAINER_NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

if ! command -v docker >/dev/null 2>&1; then
    echo "Docker is not available; image build and runtime checks were not run." >&2
    exit 1
fi

if find "$SCRIPT_DIR" \( -name '.env' -o -name '.env.*' \
    -o -name '*.csv' -o -name '*.tsv' -o -name '*.parquet' \
    -o -name '*.xlsx' -o -name '*.xls' -o -name '*.pem' \
    -o -name '*.key' -o -name '*.secret' \) -print | grep -q .; then
    echo "Refusing to build: secrets or local datasets exist in the build context." >&2
    exit 1
fi

docker build --tag "$IMAGE" "$SCRIPT_DIR"

docker run --rm "$IMAGE" --once

uid=$(docker run --rm --entrypoint id "$IMAGE" -u)
if [ "$uid" != "10001" ]; then
    echo "Expected worker UID 10001, got $uid." >&2
    exit 1
fi

docker run --rm --entrypoint python "$IMAGE" -c \
    'import sys; assert sys.version_info[:2] == (3, 12), sys.version'

docker run --rm --entrypoint python "$IMAGE" -c \
    'import numpy, pandas; print("pandas", pandas.__version__, "numpy", numpy.__version__)'

docker run --rm --entrypoint python "$IMAGE" -m worker.health

docker run --rm --entrypoint python "$IMAGE" -c \
    'import importlib.util; forbidden = ("fastapi", "google", "httpx", "pydantic", "uvicorn"); found = [name for name in forbidden if importlib.util.find_spec(name) is not None]; assert not found, found'

docker run --rm --entrypoint python "$IMAGE" -c \
    'from pathlib import Path; forbidden_suffixes = {".csv", ".tsv", ".parquet", ".xlsx", ".xls", ".env", ".pem", ".key"}; files = [p for p in Path("/worker").rglob("*") if p.is_file()]; assert not any(p.name.startswith(".env") or p.suffix in forbidden_suffixes for p in files)'

docker run --detach --name "$CONTAINER_NAME" "$IMAGE" >/dev/null
ready=0
for _ in $(seq 1 30); do
    if docker logs "$CONTAINER_NAME" 2>&1 | grep -q "ready and idle"; then
        ready=1
        break
    fi
    sleep 1
done
if [ "$ready" != "1" ]; then
    docker logs "$CONTAINER_NAME" >&2 || true
    echo "Worker did not reach idle state." >&2
    exit 1
fi

docker stop --time 10 "$CONTAINER_NAME" >/dev/null
exit_code=$(docker inspect --format '{{.State.ExitCode}}' "$CONTAINER_NAME")
if [ "$exit_code" != "0" ]; then
    echo "Worker exited with code $exit_code after SIGTERM." >&2
    exit 1
fi

image_size=$(docker image inspect --format '{{.Size}}' "$IMAGE")
echo "Verification passed for $IMAGE (size: $image_size bytes)."
