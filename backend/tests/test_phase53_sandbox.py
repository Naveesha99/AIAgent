"""Static and Docker runtime tests for the Phase 5.3 sandbox policy."""

from __future__ import annotations

import dataclasses
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.sandbox import (
    DEFAULT_SANDBOX_EXECUTION_TIMEOUT_SECONDS,
    SANDBOX_INPUT_PATH,
    SANDBOX_MANAGED_LABEL,
    SANDBOX_NETWORK_MODE,
    SANDBOX_TMP_DIR,
    SANDBOX_WORKER_USER,
    SandboxConfigurationError,
    SandboxLimits,
    SandboxMount,
    SandboxPolicy,
    SandboxTermination,
    classify_sandbox_termination,
    parse_docker_size,
    sandbox_cleanup_args,
    sandbox_container_labels,
    sandbox_container_name,
    sandbox_named_cleanup_args,
    sandbox_sweep_args,
    sandbox_sweep_remove_args,
)
from app.schemas.execution import (
    ExecutionErrorCategory,
    ExecutionErrorCode,
    ExecutionRequest,
)
from app.services.sandbox_config import SandboxConfigurationService

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
SANDBOX_ENVIRONMENT_VARIABLES = (
    "SANDBOX_WORKER_IMAGE",
    "SANDBOX_NETWORK_MODE",
    "SANDBOX_READ_ONLY_ROOT",
    "SANDBOX_SECCOMP_PROFILE",
    "SANDBOX_MEMORY_LIMIT",
    "SANDBOX_MEMORY_SWAP_LIMIT",
    "SANDBOX_CPU_LIMIT",
    "SANDBOX_PIDS_LIMIT",
    "SANDBOX_TMP_SIZE",
    "SANDBOX_EXECUTION_TIMEOUT_SECONDS",
    "SANDBOX_MAX_STDOUT_BYTES",
    "SANDBOX_MAX_STDERR_BYTES",
)


@pytest.fixture(autouse=True)
def clear_sandbox_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in SANDBOX_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def default_settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)


def write_dataset(directory: Path, name: str = "data.csv") -> Path:
    path = directory / name
    path.write_text("a,b\n1,2\n", encoding="utf-8")
    return path


@pytest.fixture
def dataset(tmp_path: Path) -> tuple[Path, Path]:
    return write_dataset(tmp_path), tmp_path


@pytest.fixture
def service() -> SandboxConfigurationService:
    return SandboxConfigurationService(default_settings())


@pytest.fixture
def container_spec(service: SandboxConfigurationService, dataset: tuple[Path, Path]):
    dataset_file, dataset_root = dataset
    execution_id = uuid4()
    spec = service.build_container_spec(
        dataset_file=dataset_file,
        dataset_root=dataset_root,
        execution_id=execution_id,
    )
    return spec, execution_id


def test_sandbox_defaults_are_conservative_and_safe() -> None:
    settings = default_settings()

    assert settings.sandbox_worker_image == "ai-data-analyst-worker:dev"
    assert settings.sandbox_network_mode == "none"
    assert settings.sandbox_read_only_root is True
    assert settings.sandbox_seccomp_profile == "default"
    assert settings.sandbox_memory_limit == "512m"
    assert settings.sandbox_memory_swap_limit is None
    assert settings.sandbox_cpu_limit == 1.0
    assert settings.sandbox_pids_limit == 64
    assert settings.sandbox_tmp_size == "64m"
    assert settings.sandbox_execution_timeout_seconds == 30
    assert settings.sandbox_max_stdout_bytes == 100_000
    assert settings.sandbox_max_stderr_bytes == 50_000


def test_sandbox_settings_load_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SANDBOX_MEMORY_LIMIT", "256m")
    monkeypatch.setenv("SANDBOX_CPU_LIMIT", "0.5")
    monkeypatch.setenv("SANDBOX_PIDS_LIMIT", "32")
    monkeypatch.setenv("SANDBOX_TMP_SIZE", "32m")
    monkeypatch.setenv("SANDBOX_EXECUTION_TIMEOUT_SECONDS", "10")
    monkeypatch.setenv("SANDBOX_MAX_STDOUT_BYTES", "1234")
    monkeypatch.setenv("SANDBOX_MAX_STDERR_BYTES", "5678")

    settings = Settings(_env_file=None)

    assert settings.sandbox_memory_limit == "256m"
    assert settings.sandbox_cpu_limit == 0.5
    assert settings.sandbox_pids_limit == 32
    assert settings.sandbox_tmp_size == "32m"
    assert settings.sandbox_execution_timeout_seconds == 10
    assert settings.sandbox_max_stdout_bytes == 1234
    assert settings.sandbox_max_stderr_bytes == 5678


@pytest.mark.parametrize(
    "image",
    ["--privileged", "worker:dev --privileged", "worker:dev\n--privileged"],
)
def test_sandbox_settings_reject_unsafe_image_references(image: str) -> None:
    with pytest.raises(ValidationError):
        default_settings(sandbox_worker_image=image)


def test_default_policy_builds_hardened_container_spec(
    container_spec: tuple[Any, Any],
) -> None:
    spec, execution_id = container_spec

    assert spec.user == SANDBOX_WORKER_USER == "10001:10001"
    assert spec.working_dir == "/worker"
    assert spec.network_mode == SANDBOX_NETWORK_MODE == "none"
    assert spec.read_only is True
    assert spec.privileged is False
    assert spec.cap_drop == ("ALL",)
    assert spec.cap_add == ()
    assert "no-new-privileges:true" in spec.security_opt
    assert spec.seccomp_profile == "default"
    assert spec.pid_mode is None
    assert spec.ipc_mode == "none"
    assert spec.name == sandbox_container_name(execution_id)
    assert spec.labels == sandbox_container_labels(execution_id)
    assert spec.labels[SANDBOX_MANAGED_LABEL] == "true"
    assert spec.limits.memory_bytes == 512 * 1024 * 1024
    assert spec.limits.memory_swap_bytes == spec.limits.memory_bytes
    assert spec.limits.cpu_limit == 1.0
    assert spec.limits.nano_cpus == 1_000_000_000
    assert spec.limits.pids_limit == 64
    assert spec.limits.tmp_bytes == 64 * 1024 * 1024
    assert spec.limits.execution_timeout_seconds == (
        DEFAULT_SANDBOX_EXECUTION_TIMEOUT_SECONDS
    )
    assert spec.limits.max_stdout_bytes == 100_000
    assert spec.limits.max_stderr_bytes == 50_000
    assert len(spec.mounts) == 1
    assert spec.mounts[0].target == SANDBOX_INPUT_PATH == "/input/dataset.csv"
    assert spec.mounts[0].read_only is True
    assert set(spec.tmpfs) == {SANDBOX_TMP_DIR}
    assert set(spec.environment) == {
        "HOME",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONUNBUFFERED",
        "TMPDIR",
    }
    assert spec.environment["HOME"] == "/tmp"
    assert spec.environment["TMPDIR"] == "/tmp"


def test_container_docker_args_contain_only_restrictive_options(
    container_spec: tuple[Any, Any],
) -> None:
    spec, _ = container_spec
    args = spec.docker_run_args()

    assert args[:2] == ["docker", "run"]
    assert args[-1] == "ai-data-analyst-worker:dev"
    assert "--network=none" in args
    assert "--ipc=none" in args
    assert "--read-only" in args
    assert "--cap-drop=ALL" in args
    assert "--security-opt=no-new-privileges:true" in args
    assert "--pids-limit=64" in args
    assert "--memory=536870912" in args
    assert "--memory-swap=536870912" in args
    assert "--cpus=1" in args
    assert any(item.startswith("--tmpfs=/tmp:") for item in args)
    assert any(
        item.startswith("--mount=type=bind,")
        and "target=/input/dataset.csv,readonly" in item
        for item in args
    )
    assert "--privileged" not in args
    assert not any(item.startswith("--publish") for item in args)
    assert not any(item.startswith("--expose") for item in args)
    assert not any(item in {"--pid=host", "--ipc=host"} for item in args)
    assert not any(item.startswith("--network=host") for item in args)
    assert not any("docker.sock" in item for item in args)
    assert not any(item.startswith("--env-file") for item in args)


def test_custom_limits_are_applied(
    dataset: tuple[Path, Path],
) -> None:
    dataset_file, dataset_root = dataset
    settings = default_settings(
        sandbox_memory_limit="1g",
        sandbox_memory_swap_limit="2g",
        sandbox_cpu_limit=0.5,
        sandbox_pids_limit=32,
        sandbox_tmp_size="32m",
        sandbox_execution_timeout_seconds=10,
        sandbox_max_stdout_bytes=2_000,
        sandbox_max_stderr_bytes=1_000,
    )
    service = SandboxConfigurationService(settings)
    spec = service.build_container_spec(
        dataset_file=dataset_file,
        dataset_root=dataset_root,
    )

    assert spec.limits.memory_bytes == 1024 * 1024 * 1024
    assert spec.limits.memory_swap_bytes == 2 * 1024 * 1024 * 1024
    assert spec.limits.cpu_limit == 0.5
    assert spec.limits.pids_limit == 32
    assert spec.limits.tmp_bytes == 32 * 1024 * 1024
    assert spec.limits.execution_timeout_seconds == 10
    assert spec.limits.max_stdout_bytes == 2_000
    assert spec.limits.max_stderr_bytes == 1_000


@pytest.mark.parametrize(
    "overrides",
    [
        {"sandbox_network_mode": "bridge"},
        {"sandbox_network_mode": "host"},
        {"sandbox_read_only_root": False},
        {"sandbox_seccomp_profile": "unconfined"},
        {"sandbox_worker_image": " "},
        {"sandbox_worker_image": "bad image"},
        {"sandbox_memory_limit": "32m"},
        {"sandbox_memory_limit": "5g"},
        {"sandbox_memory_limit": "lots"},
        {"sandbox_memory_swap_limit": "256m"},
        {"sandbox_cpu_limit": 0.05},
        {"sandbox_cpu_limit": 4.5},
        {"sandbox_pids_limit": 8},
        {"sandbox_pids_limit": 1_024},
        {"sandbox_tmp_size": "4m"},
        {"sandbox_tmp_size": "2g"},
        {"sandbox_execution_timeout_seconds": 0},
        {"sandbox_execution_timeout_seconds": 601},
        {"sandbox_max_stdout_bytes": 0},
        {"sandbox_max_stdout_bytes": 2_000_001},
        {"sandbox_max_stderr_bytes": 0},
        {"sandbox_max_stderr_bytes": 2_000_001},
    ],
)
def test_invalid_or_unsafe_sandbox_settings_are_rejected(
    overrides: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        default_settings(**overrides)


@pytest.mark.parametrize(
    "changes",
    [
        {"network_mode": "bridge"},
        {"read_only": False},
        {"privileged": True},
        {"cap_drop": ()},
        {"cap_add": ("SYS_ADMIN",)},
        {"security_opt": ()},
        {"seccomp_profile": "unconfined"},
        {"pid_mode": "host"},
        {"ipc_mode": "host"},
        {"mounts": ()},
        {"tmpfs": {"/tmp": "rw,size=64m"}},
        {"tmpfs": {"/tmp": "rw,noexec,nosuid,nodev,size=4m"}},
        {"environment": {"API_TOKEN": "secret"}},
        {"user": "0:0"},
    ],
)
def test_container_spec_rejects_weakened_security(
    container_spec: tuple[Any, Any],
    changes: dict[str, Any],
) -> None:
    spec, _ = container_spec

    with pytest.raises(SandboxConfigurationError):
        dataclasses.replace(spec, **changes)


def test_policy_rejects_unsafe_settings_directly() -> None:
    limits = SandboxLimits(
        memory_bytes=512 * 1024 * 1024,
        memory_swap_bytes=512 * 1024 * 1024,
        cpu_limit=1.0,
        pids_limit=64,
        tmp_bytes=64 * 1024 * 1024,
        execution_timeout_seconds=30,
        max_stdout_bytes=100_000,
        max_stderr_bytes=50_000,
    )

    with pytest.raises(SandboxConfigurationError):
        SandboxPolicy(image="worker:dev", limits=limits, network_mode="bridge")
    with pytest.raises(SandboxConfigurationError):
        SandboxPolicy(image="worker:dev", limits=limits, read_only_root=False)
    with pytest.raises(SandboxConfigurationError):
        SandboxPolicy(image="worker:dev", limits=limits, seccomp_profile="unconfined")
    with pytest.raises(SandboxConfigurationError):
        SandboxPolicy(image="--privileged", limits=limits)
    with pytest.raises(SandboxConfigurationError):
        SandboxPolicy(image="worker:dev --privileged", limits=limits)


def test_sandbox_mount_rejects_host_control_sockets_and_escape(
    tmp_path: Path,
) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    dataset_file = write_dataset(allowed)
    outside = write_dataset(tmp_path, "outside.csv")

    mount = SandboxMount(source=dataset_file)
    assert mount.validate(allowed_root=allowed) == dataset_file.resolve()

    with pytest.raises(SandboxConfigurationError):
        SandboxMount(source=outside).validate(allowed_root=allowed)
    with pytest.raises(SandboxConfigurationError):
        SandboxMount(source=Path("/var/run/docker.sock")).validate(
            allowed_root=Path("/var/run")
        )
    with pytest.raises(SandboxConfigurationError):
        SandboxMount(source=dataset_file, read_only=False)
    with pytest.raises(SandboxConfigurationError):
        SandboxMount(source=dataset_file, target="/worker/dataset.csv")


def test_environment_rejects_secret_like_values(
    service: SandboxConfigurationService,
    dataset: tuple[Path, Path],
) -> None:
    dataset_file, dataset_root = dataset

    with pytest.raises(SandboxConfigurationError):
        service.build_container_spec(
            dataset_file=dataset_file,
            dataset_root=dataset_root,
            environment={"API_KEY": "secret"},
        )
    with pytest.raises(SandboxConfigurationError):
        service.build_container_spec(
            dataset_file=dataset_file,
            dataset_root=dataset_root,
            environment={"lower_case": "value"},
        )

    spec = service.build_container_spec(
        dataset_file=dataset_file,
        dataset_root=dataset_root,
        environment={"LOG_LEVEL": "INFO"},
    )
    assert spec.environment["LOG_LEVEL"] == "INFO"


def test_environment_rejects_process_loading_overrides(
    service: SandboxConfigurationService,
    dataset: tuple[Path, Path],
) -> None:
    dataset_file, dataset_root = dataset

    for key in ("PATH", "LD_PRELOAD", "PYTHONPATH"):
        with pytest.raises(SandboxConfigurationError):
            service.build_container_spec(
                dataset_file=dataset_file,
                dataset_root=dataset_root,
                environment={key: "/tmp"},
            )


def test_container_spec_rejects_mount_outside_dataset_root(
    container_spec: tuple[Any, Any],
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    spec, _ = container_spec
    outside_root = tmp_path_factory.mktemp("outside")
    outside = write_dataset(outside_root)

    with pytest.raises(SandboxConfigurationError):
        dataclasses.replace(spec, mounts=(SandboxMount(source=outside),))


def test_mount_rejects_unsupported_path_characters() -> None:
    with pytest.raises(SandboxConfigurationError):
        SandboxMount(source=Path("/tmp/a,b.csv")).docker_mount_arg()


def test_container_names_and_cleanup_are_generated_and_scoped() -> None:
    execution_id = uuid4()
    name = sandbox_container_name(execution_id)

    assert name == f"ai-data-analyst-sandbox-{execution_id}"
    assert sandbox_cleanup_args(execution_id) == [
        "docker",
        "rm",
        "--force",
        name,
    ]
    assert sandbox_named_cleanup_args(name) == [
        "docker",
        "rm",
        "--force",
        name,
    ]
    assert sandbox_sweep_args() == [
        "docker",
        "ps",
        "--all",
        "--quiet",
        "--filter",
        "label=ai-data-analyst.sandbox=true",
    ]
    assert sandbox_sweep_remove_args() == ["docker", "rm", "--force"]

    with pytest.raises(SandboxConfigurationError):
        sandbox_cleanup_args("user-supplied-name")
    with pytest.raises(SandboxConfigurationError):
        sandbox_named_cleanup_args("ai-data-analyst-sandbox-not-a-uuid")
    with pytest.raises(SandboxConfigurationError):
        sandbox_container_name(str(execution_id).upper())


def test_effective_timeout_cannot_exceed_sandbox_cap() -> None:
    service = SandboxConfigurationService(
        default_settings(
            sandbox_execution_timeout_seconds=5,
            execution_max_timeout_seconds=10,
        )
    )

    assert service.effective_timeout_seconds(3) == 3
    assert service.effective_timeout_seconds(5) == 5
    assert service.effective_timeout_seconds(30) == 5

    with pytest.raises(SandboxConfigurationError):
        service.effective_timeout_seconds(0)
    with pytest.raises(SandboxConfigurationError):
        service.effective_timeout_seconds(-1)


@pytest.mark.parametrize(
    "extra",
    [
        {"network_mode": "bridge"},
        {"network_enabled": True},
        {"privileged": True},
        {"cap_add": ["SYS_ADMIN"]},
        {"docker_image": "python:3.12"},
        {"memory_limit_bytes": 1024},
        {"pids_limit": 1},
        {"tmp_size": "1g"},
        {"read_only": False},
        {"mounts": ["/var/run/docker.sock"]},
        {"environment": {"API_KEY": "secret"}},
        {"container_name": "attacker-controlled"},
    ],
)
def test_execution_requests_cannot_override_sandbox_settings(
    extra: dict[str, Any],
) -> None:
    payload = {
        "dataset_id": str(uuid4()),
        "code": "result = df.shape",
        "timeout_seconds": 10,
    }
    payload.update(extra)

    with pytest.raises(ValidationError):
        ExecutionRequest.from_payload(payload)


@pytest.mark.parametrize(
    ("exit_code", "oom_killed", "timed_out", "cancelled", "expected"),
    [
        (0, False, False, False, SandboxTermination.COMPLETED),
        (1, False, False, False, SandboxTermination.FAILED),
        (137, False, True, False, SandboxTermination.TIMED_OUT),
        (137, True, False, False, SandboxTermination.RESOURCE_EXHAUSTED),
        (143, False, False, True, SandboxTermination.CANCELLED),
        (137, False, False, False, SandboxTermination.FAILED),
    ],
)
def test_termination_classification(
    exit_code: int,
    oom_killed: bool,
    timed_out: bool,
    cancelled: bool,
    expected: SandboxTermination,
) -> None:
    assert (
        classify_sandbox_termination(
            exit_code=exit_code,
            oom_killed=oom_killed,
            timed_out=timed_out,
            cancelled=cancelled,
        )
        is expected
    )


def test_structured_errors_for_sandbox_termination() -> None:
    service = SandboxConfigurationService(default_settings())

    assert service.error_for_termination(SandboxTermination.COMPLETED) is None

    timeout = service.error_for_termination(SandboxTermination.TIMED_OUT)
    assert timeout is not None
    assert timeout.code is ExecutionErrorCode.EXECUTION_TIMEOUT
    assert timeout.category is ExecutionErrorCategory.EXECUTION_TIMEOUT
    assert timeout.retryable is True

    exhausted = service.error_for_termination(
        SandboxTermination.RESOURCE_EXHAUSTED
    )
    assert exhausted is not None
    assert exhausted.code is ExecutionErrorCode.RESOURCE_LIMIT_EXCEEDED
    assert exhausted.category is ExecutionErrorCategory.RESOURCE_LIMIT_EXCEEDED
    assert exhausted.retryable is False

    cancelled = service.error_for_termination(SandboxTermination.CANCELLED)
    assert cancelled is not None
    assert cancelled.code is ExecutionErrorCode.EXECUTION_CANCELLED

    failed = service.error_for_termination(SandboxTermination.FAILED)
    assert failed is not None
    assert failed.code is ExecutionErrorCode.EXECUTION_FAILED


def test_docker_sizes_are_parsed_and_formatted() -> None:
    assert parse_docker_size("512m") == 512 * 1024 * 1024
    assert parse_docker_size("1G") == 1024 * 1024 * 1024
    assert parse_docker_size("65536k") == 64 * 1024 * 1024
    assert parse_docker_size("1048576") == 1024 * 1024

    with pytest.raises(SandboxConfigurationError):
        parse_docker_size("1.5g")


def test_env_examples_document_sandbox_defaults() -> None:
    expected_lines = {
        "SANDBOX_NETWORK_MODE=none",
        "SANDBOX_READ_ONLY_ROOT=true",
        "SANDBOX_MEMORY_LIMIT=512m",
        "SANDBOX_CPU_LIMIT=1.0",
        "SANDBOX_PIDS_LIMIT=64",
        "SANDBOX_TMP_SIZE=64m",
        "SANDBOX_EXECUTION_TIMEOUT_SECONDS=30",
        "SANDBOX_MAX_STDOUT_BYTES=100000",
        "SANDBOX_MAX_STDERR_BYTES=50000",
    }
    for example in (
        BACKEND_ROOT / ".env.example",
        REPOSITORY_ROOT / ".env.example",
    ):
        text = example.read_text(encoding="utf-8")
        for line in expected_lines:
            assert line in text, (example, line)


def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    completed = subprocess.run(
        ["docker", "info"],
        capture_output=True,
        text=True,
        timeout=20,
    )
    return completed.returncode == 0


def image_available(image: str) -> bool:
    completed = subprocess.run(
        ["docker", "image", "inspect", image],
        capture_output=True,
        text=True,
        timeout=20,
    )
    return completed.returncode == 0


DOCKER_IMAGE = "ai-data-analyst-worker:dev"
DOCKER_SKIP_REASON = (
    "Docker or the worker image is unavailable; run "
    "backend/execution_worker/verify_sandbox.py to build and verify it."
)
requires_docker = pytest.mark.skipif(
    not docker_available() or not image_available(DOCKER_IMAGE),
    reason=DOCKER_SKIP_REASON,
)


def run_docker(args: list[str], *, timeout: int = 90) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@requires_docker
def test_docker_host_configuration_matches_sandbox_policy(
    dataset: tuple[Path, Path],
) -> None:
    dataset_file, dataset_root = dataset
    service = SandboxConfigurationService(
        default_settings(sandbox_worker_image=DOCKER_IMAGE)
    )
    spec = service.build_container_spec(
        dataset_file=dataset_file,
        dataset_root=dataset_root,
        execution_id=uuid4(),
    )
    name = spec.name

    try:
        completed = run_docker(spec.docker_run_args() + ["--once"])
        assert completed.returncode == 0, completed.stderr
        inspected = run_docker(["docker", "inspect", name])
        assert inspected.returncode == 0, inspected.stderr
        container = json.loads(inspected.stdout)[0]
        host = container["HostConfig"]

        assert host["NetworkMode"] == "none"
        assert host["ReadonlyRootfs"] is True
        assert host["Privileged"] is False
        assert host["CapDrop"] == ["ALL"]
        assert any(
            "no-new-privileges" in option for option in host["SecurityOpt"]
        )
        assert host["PidMode"] != "host"
        assert host["IpcMode"] != "host"
        assert host["Memory"] == 512 * 1024 * 1024
        assert host["MemorySwap"] == 512 * 1024 * 1024
        assert host["NanoCpus"] == 1_000_000_000
        assert host["PidsLimit"] == 64
        assert not host["PortBindings"]
        mounts = container.get("Mounts") or []
        assert len(mounts) == 1
        assert mounts[0]["Type"] == "bind"
        assert mounts[0]["Destination"] == "/input/dataset.csv"
        assert mounts[0]["RW"] is False
        assert "docker.sock" not in (mounts[0].get("Source") or "")
        assert "/tmp" in host["Tmpfs"]
        assert "size=64m" in host["Tmpfs"]["/tmp"]
    finally:
        run_docker(["docker", "rm", "--force", name])


@requires_docker
def test_docker_runtime_enforces_security_restrictions(
    dataset: tuple[Path, Path],
) -> None:
    dataset_file, dataset_root = dataset
    outside_dataset = write_dataset(dataset_root, "other-dataset.csv")
    service = SandboxConfigurationService(
        default_settings(sandbox_worker_image=DOCKER_IMAGE)
    )
    spec = service.build_container_spec(
        dataset_file=dataset_file,
        dataset_root=dataset_root,
        execution_id=uuid4(),
    )
    program = "\n".join(
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
    args = [
        "docker",
        "run",
        "--rm",
        *spec.docker_run_options(),
        "--entrypoint",
        "python",
        spec.image,
        "-c",
        program,
    ]

    completed = run_docker(args)

    assert completed.returncode == 0, completed.stderr
    assert "runtime-security-ok" in completed.stdout
    assert outside_dataset.exists()
