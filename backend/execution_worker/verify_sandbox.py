#!/usr/bin/env python3
"""Verify the Phase 5.3 Docker sandbox policy against a real worker image.

This script builds or reuses the worker image, starts short-lived containers
with the centralized sandbox flags, and inspects Docker's host configuration
and the container runtime. It does not accept jobs or execute generated code.

Run from the repository root with the backend virtual environment active:

    python backend/execution_worker/verify_sandbox.py

Use ``--build`` to rebuild the image first. If Docker or the image is
unavailable, the script exits with a non-zero status without claiming that
the runtime checks passed.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

EXECUTION_WORKER_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = EXECUTION_WORKER_DIR.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import Settings  # noqa: E402
from app.core.sandbox import (  # noqa: E402
    DEFAULT_SANDBOX_IMAGE,
    SandboxContainerSpec,
    format_docker_size,
    sandbox_named_cleanup_args,
    sandbox_sweep_args,
)
from app.services.sandbox_config import SandboxConfigurationService  # noqa: E402


class VerificationError(RuntimeError):
    """A sandbox verification check failed."""


def run(
    args: list[str],
    *,
    timeout: int = 120,
    check: bool = True,
) -> subprocess.CompletedProcess:
    completed = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and completed.returncode != 0:
        raise VerificationError(
            "Command failed: "
            + " ".join(args)
            + "\nstdout:\n"
            + completed.stdout
            + "\nstderr:\n"
            + completed.stderr
        )
    return completed


def ensure_docker() -> None:
    if shutil.which("docker") is None:
        raise VerificationError("Docker is not installed or not on PATH.")
    completed = run(["docker", "info"], check=False, timeout=30)
    if completed.returncode != 0:
        raise VerificationError(
            "Docker is not available. Start Docker and retry.\n"
            + completed.stderr
        )


def image_exists(image: str) -> bool:
    return run(
        ["docker", "image", "inspect", image],
        check=False,
        timeout=30,
    ).returncode == 0


def build_image(image: str) -> None:
    print(f"Building worker image {image} ...")
    run(
        ["docker", "build", "--tag", image, str(EXECUTION_WORKER_DIR)],
        timeout=600,
    )


def write_dataset(directory: Path, name: str) -> Path:
    path = directory / name
    path.write_text("a,b\n1,2\n", encoding="utf-8")
    return path


def build_spec(
    service: SandboxConfigurationService,
    *,
    dataset_file: Path,
    dataset_root: Path,
) -> SandboxContainerSpec:
    return service.build_container_spec(
        dataset_file=dataset_file,
        dataset_root=dataset_root,
        execution_id=uuid4(),
    )


def assert_host_configuration(
    container: dict[str, Any],
    spec: SandboxContainerSpec,
) -> None:
    host = container["HostConfig"]
    security_options = host.get("SecurityOpt") or []
    tmpfs = host.get("Tmpfs") or {}
    tmpfs_size = f"size={format_docker_size(spec.limits.tmp_bytes)}"
    checks = {
        "network mode": host.get("NetworkMode") == spec.network_mode,
        "read-only root": host.get("ReadonlyRootfs") is spec.read_only,
        "not privileged": host.get("Privileged") is False,
        "all capabilities dropped": host.get("CapDrop") == list(spec.cap_drop),
        "no new privileges": all(
            option in security_options for option in spec.security_opt
        ),
        "no host PID namespace": host.get("PidMode") not in {"host"},
        "no host IPC namespace": (
            host.get("IpcMode") not in {"host"}
            and spec.ipc_mode == "none"
        ),
        "memory limit": host.get("Memory") == spec.limits.memory_bytes,
        "swap limited to memory": (
            host.get("MemorySwap") == spec.limits.memory_swap_bytes
        ),
        "CPU limit": host.get("NanoCpus") == spec.limits.nano_cpus,
        "process limit": host.get("PidsLimit") == spec.limits.pids_limit,
        "no published ports": not host.get("PortBindings"),
        "temporary filesystem bounded": (
            "/tmp" in tmpfs and tmpfs_size in tmpfs["/tmp"]
        ),
    }
    mounts = container.get("Mounts") or []
    expected_mount = spec.mounts[0]
    checks["one read-only input bind"] = (
        len(mounts) == 1
        and mounts[0].get("Type") == "bind"
        and mounts[0].get("Destination") == expected_mount.target
        and mounts[0].get("RW") == (not expected_mount.read_only)
    )
    checks["no Docker socket mount"] = not any(
        "docker.sock" in (mount.get("Source") or "") for mount in mounts
    )
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise VerificationError(
            "Host configuration checks failed: " + ", ".join(failed)
        )
    print("  Host configuration checks passed.")


RUNTIME_CHECK = "\n".join(
    [
        "import os, socket",
        "assert os.getuid() == 10001, os.getuid()",
        "assert os.getgid() == 10001, os.getgid()",
        "status = open('/proc/self/status').read()",
        "fields = dict(",
        "    line.split(':', 1) for line in status.splitlines()",
        "    if ':' in line",
        ")",
        "assert fields['CapEff'].strip() == '0000000000000000', fields['CapEff']",
        "assert fields['NoNewPrivs'].strip() == '1', fields['NoNewPrivs']",
        "assert fields['Seccomp'].strip() == '2', fields['Seccomp']",
        "assert open('/input/dataset.csv').read() == 'a,b\\n1,2\\n'",
        "assert not os.path.exists('/input/other-dataset.csv')",
        "assert not os.path.exists('/var/run/docker.sock')",
        "assert not os.path.exists('/Users')",
        "assert not os.path.exists('/Volumes')",
        "for path in ('/input/dataset.csv', '/worker/forbidden.txt'):",
        "    try:",
        "        open(path, 'w')",
        "    except OSError:",
        "        pass",
        "    else:",
        "        raise SystemExit('write unexpectedly allowed: ' + path)",
        "open('/tmp/sandbox-check', 'w').write('ok')",
        "try:",
        "    socket.create_connection(('1.1.1.1', 80), timeout=2)",
        "except OSError:",
        "    pass",
        "else:",
        "    raise SystemExit('external network reachable')",
        "try:",
        "    socket.gethostbyname('example.com')",
        "except OSError:",
        "    pass",
        "else:",
        "    raise SystemExit('DNS unexpectedly available')",
        "print('runtime-security-ok')",
    ]
)


def verify_host_and_runtime(
    service: SandboxConfigurationService,
    *,
    dataset_file: Path,
    dataset_root: Path,
) -> None:
    spec = build_spec(
        service,
        dataset_file=dataset_file,
        dataset_root=dataset_root,
    )
    name = spec.name
    try:
        print("  Starting sandboxed health-check container ...")
        run(spec.docker_run_args() + ["--once"])
        inspected = run(["docker", "inspect", name])
        container = json.loads(inspected.stdout)[0]
        assert_host_configuration(container, spec)
    finally:
        run(["docker", "rm", "--force", name], check=False, timeout=30)

    runtime_spec = build_spec(
        service,
        dataset_file=dataset_file,
        dataset_root=dataset_root,
    )
    print("  Running container runtime security checks ...")
    completed = run(
        [
            "docker",
            "run",
            "--rm",
            *runtime_spec.docker_run_options(),
            "--entrypoint",
            "python",
            runtime_spec.image,
            "-c",
            RUNTIME_CHECK,
        ],
        check=False,
    )
    if completed.returncode != 0 or "runtime-security-ok" not in completed.stdout:
        run(
            sandbox_named_cleanup_args(runtime_spec.name),
            check=False,
            timeout=30,
        )
        raise VerificationError(
            "Runtime security checks failed.\nstdout:\n"
            + completed.stdout
            + "\nstderr:\n"
            + completed.stderr
        )
    print("  Runtime security checks passed.")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify the Docker sandbox security policy.",
    )
    parser.add_argument(
        "--image",
        default=os.environ.get("SANDBOX_WORKER_IMAGE", DEFAULT_SANDBOX_IMAGE),
        help="Worker image tag (default: %(default)s).",
    )
    parser.add_argument(
        "--build",
        action="store_true",
        help="Rebuild the worker image before verification.",
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Fail instead of building when the image is missing.",
    )
    args = parser.parse_args(argv)

    try:
        ensure_docker()
        if args.build or not image_exists(args.image):
            if args.skip_build:
                raise VerificationError(
                    f"Image {args.image} is missing and --skip-build was used."
                )
            build_image(args.image)

        settings = Settings(
            _env_file=None,
            sandbox_worker_image=args.image,
        )
        service = SandboxConfigurationService(settings)
        policy = service.policy
        print("Sandbox policy:")
        print(
            "  network=none, read-only root, non-root user, "
            "cap-drop=ALL, no-new-privileges, default seccomp"
        )
        print(
            "  limits: memory="
            f"{policy.limits.memory_bytes}B, "
            f"swap={policy.limits.memory_swap_bytes}B, "
            f"cpu={policy.limits.cpu_limit}, "
            f"pids={policy.limits.pids_limit}, "
            f"tmp={policy.limits.tmp_bytes}B, "
            f"timeout={policy.limits.execution_timeout_seconds}s, "
            f"stdout={policy.limits.max_stdout_bytes}B, "
            f"stderr={policy.limits.max_stderr_bytes}B"
        )
        print(
            "  managed container selector: "
            + " ".join(sandbox_sweep_args())
        )

        with tempfile.TemporaryDirectory(prefix="sandbox-verify-") as raw_dir:
            directory = Path(raw_dir)
            dataset_file = write_dataset(directory, "data.csv")
            write_dataset(directory, "other-dataset.csv")
            verify_host_and_runtime(
                service,
                dataset_file=dataset_file,
                dataset_root=directory,
            )
    except VerificationError as exc:
        print(f"Verification failed: {exc}", file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired as exc:
        print(f"Verification timed out: {exc}", file=sys.stderr)
        return 1

    print(f"Sandbox verification passed for {args.image}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
