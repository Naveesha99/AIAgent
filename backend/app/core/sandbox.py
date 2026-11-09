"""Centralized Docker sandbox policy for untrusted code execution.

Phase 5.3 defines and validates the security configuration for future
execution containers. This module does not launch containers, connect to the
API, accept jobs, or execute generated code. It only produces a validated,
reusable description of the restrictions that a future execution runtime must
apply.

Security is layered. The container policy below is one layer; a future runtime
must still enforce request validation, wall-clock timeout, output bounds, and
container cleanup.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Optional
from uuid import UUID, uuid4

SANDBOX_NETWORK_MODE = "none"
SANDBOX_WORKER_USER = "10001:10001"
SANDBOX_RUNTIME_DIR = "/worker"
SANDBOX_INPUT_DIR = "/input"
SANDBOX_INPUT_FILENAME = "dataset.csv"
SANDBOX_INPUT_PATH = f"{SANDBOX_INPUT_DIR}/{SANDBOX_INPUT_FILENAME}"
SANDBOX_TMP_DIR = "/tmp"
SANDBOX_CONTAINER_NAME_PREFIX = "ai-data-analyst-sandbox"
SANDBOX_MANAGED_LABEL = "ai-data-analyst.sandbox"
SANDBOX_EXECUTION_LABEL = "ai-data-analyst.execution-id"
SANDBOX_LABEL_SELECTOR = f"{SANDBOX_MANAGED_LABEL}=true"
SANDBOX_SECCOMP_PROFILE = "default"

DEFAULT_SANDBOX_IMAGE = "ai-data-analyst-worker:dev"
DEFAULT_SANDBOX_MEMORY_LIMIT = "512m"
DEFAULT_SANDBOX_CPU_LIMIT = 1.0
DEFAULT_SANDBOX_PIDS_LIMIT = 64
DEFAULT_SANDBOX_TMP_SIZE = "64m"
DEFAULT_SANDBOX_EXECUTION_TIMEOUT_SECONDS = 30.0
DEFAULT_SANDBOX_MAX_STDOUT_BYTES = 100_000
DEFAULT_SANDBOX_MAX_STDERR_BYTES = 50_000

MIN_SANDBOX_MEMORY_BYTES = 64 * 1024 * 1024
MAX_SANDBOX_MEMORY_BYTES = 4 * 1024 * 1024 * 1024
MIN_SANDBOX_TMP_BYTES = 8 * 1024 * 1024
MAX_SANDBOX_TMP_BYTES = 1024 * 1024 * 1024
MIN_SANDBOX_CPU_LIMIT = 0.1
MAX_SANDBOX_CPU_LIMIT = 4.0
MIN_SANDBOX_PIDS_LIMIT = 16
MAX_SANDBOX_PIDS_LIMIT = 512
MAX_SANDBOX_EXECUTION_TIMEOUT_SECONDS = 600.0
MAX_SANDBOX_STDOUT_BYTES = 2_000_000
MAX_SANDBOX_STDERR_BYTES = 2_000_000

SANDBOX_NOFILE_SOFT_LIMIT = 256
SANDBOX_NOFILE_HARD_LIMIT = 256
SANDBOX_CORE_LIMIT = 0
SANDBOX_STOP_TIMEOUT_SECONDS = 10
SANDBOX_TMPFS_MODE = "1777"

_DOCKER_SIZE_PATTERN = re.compile(
    r"^(?P<amount>[0-9]+)(?P<unit>[bkmg])?$",
    re.IGNORECASE,
)
_DOCKER_SIZE_MULTIPLIERS = {
    "": 1,
    "b": 1,
    "k": 1024,
    "m": 1024 * 1024,
    "g": 1024 * 1024 * 1024,
}
_SANDBOX_NAME_PATTERN = re.compile(
    rf"^{re.escape(SANDBOX_CONTAINER_NAME_PREFIX)}-"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_SANDBOX_IMAGE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]*$")
_ENVIRONMENT_KEY_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_FORBIDDEN_ENVIRONMENT_KEY = re.compile(
    r"(SECRET|TOKEN|PASSWORD|PASSWD|API_?KEY|PRIVATE|CREDENTIAL|AUTH)",
    re.IGNORECASE,
)
_FORBIDDEN_MOUNT_NAMES = frozenset({"docker.sock"})
_FORBIDDEN_MOUNT_SUFFIXES = frozenset({".sock"})
_FORBIDDEN_ENVIRONMENT_NAMES = frozenset(
    {
        "BASH_ENV",
        "ENV",
        "IFS",
        "LD_LIBRARY_PATH",
        "LD_PRELOAD",
        "PATH",
        "PERL5OPT",
        "PYTHONHOME",
        "PYTHONINSPECT",
        "PYTHONPATH",
        "PYTHONSTARTUP",
    }
)
_SAFE_BASE_ENVIRONMENT = {
    "HOME": SANDBOX_TMP_DIR,
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONUNBUFFERED": "1",
    "TMPDIR": SANDBOX_TMP_DIR,
}


class SandboxConfigurationError(ValueError):
    """The requested sandbox configuration is invalid or unsafe."""


class SandboxTermination(str, Enum):
    """How a future execution container stopped."""

    COMPLETED = "completed"
    TIMED_OUT = "timed_out"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    CANCELLED = "cancelled"
    FAILED = "failed"


def parse_docker_size(value: object, *, label: str = "size") -> int:
    """Parse a Docker-style size such as ``512m`` or ``1073741824`` to bytes."""
    if not isinstance(value, str):
        raise SandboxConfigurationError(
            f"{label} must be a string Docker size such as 512m."
        )
    text = value.strip().lower()
    match = _DOCKER_SIZE_PATTERN.fullmatch(text)
    if match is None:
        raise SandboxConfigurationError(
            f"{label} must be an integer with an optional b, k, m, or g "
            "suffix."
        )
    amount = int(match.group("amount"))
    unit = match.group("unit") or ""
    return amount * _DOCKER_SIZE_MULTIPLIERS[unit]


def normalize_docker_size(
    value: object,
    *,
    label: str,
    minimum: int,
    maximum: int,
) -> str:
    """Validate a bounded Docker size and return a normalized string."""
    size = parse_docker_size(value, label=label)
    if size < minimum or size > maximum:
        raise SandboxConfigurationError(
            f"{label} must be between {format_docker_size(minimum)} and "
            f"{format_docker_size(maximum)}."
        )
    return format_docker_size(size)


def format_docker_size(size_bytes: int) -> str:
    """Format a byte count using the largest exact Docker size unit."""
    for unit, multiplier in (
        ("g", 1024 * 1024 * 1024),
        ("m", 1024 * 1024),
        ("k", 1024),
    ):
        if size_bytes % multiplier == 0:
            return f"{size_bytes // multiplier}{unit}"
    return str(size_bytes)


def canonical_execution_id(value: object) -> UUID:
    """Return only a canonical, server-generated execution identifier."""
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        raise SandboxConfigurationError("Invalid execution identifier.")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise SandboxConfigurationError(
            "Invalid execution identifier."
        ) from None
    if str(parsed) != value:
        raise SandboxConfigurationError("Invalid execution identifier.")
    return parsed


def sandbox_container_name(execution_id: object) -> str:
    """Build a container name from a canonical execution identifier."""
    parsed = canonical_execution_id(execution_id)
    return f"{SANDBOX_CONTAINER_NAME_PREFIX}-{parsed}"


def validate_sandbox_container_name(name: object) -> str:
    """Accept only names generated by this module, never user-supplied names."""
    if not isinstance(name, str) or _SANDBOX_NAME_PATTERN.fullmatch(name) is None:
        raise SandboxConfigurationError(
            "Container names must be generated by the sandbox policy."
        )
    return name


def validate_sandbox_image(image: object) -> str:
    """Accept only a single safe Docker image reference."""
    if (
        not isinstance(image, str)
        or image != image.strip()
        or _SANDBOX_IMAGE_PATTERN.fullmatch(image) is None
    ):
        raise SandboxConfigurationError(
            "The sandbox image must be a single Docker image reference."
        )
    return image


def sandbox_container_labels(execution_id: object) -> dict[str, str]:
    """Return labels used to identify and reconcile managed containers."""
    parsed = canonical_execution_id(execution_id)
    return {
        SANDBOX_MANAGED_LABEL: "true",
        SANDBOX_EXECUTION_LABEL: str(parsed),
    }


def sandbox_cleanup_args(execution_id: object) -> list[str]:
    """Return the Docker command arguments that remove one managed container."""
    return [
        "docker",
        "rm",
        "--force",
        sandbox_container_name(execution_id),
    ]


def sandbox_named_cleanup_args(container_name: object) -> list[str]:
    """Return removal arguments for a validated generated container name."""
    return [
        "docker",
        "rm",
        "--force",
        validate_sandbox_container_name(container_name),
    ]


def sandbox_sweep_args() -> list[str]:
    """Return arguments that list managed sandbox containers, including stopped ones."""
    return [
        "docker",
        "ps",
        "--all",
        "--quiet",
        "--filter",
        f"label={SANDBOX_LABEL_SELECTOR}",
    ]


def sandbox_sweep_remove_args() -> list[str]:
    """Return the removal prefix for a label-scoped orphan sweep."""
    return ["docker", "rm", "--force"]


def classify_sandbox_termination(
    *,
    exit_code: int,
    oom_killed: bool = False,
    timed_out: bool = False,
    cancelled: bool = False,
) -> SandboxTermination:
    """Classify a container exit without relying on the exit code alone.

    Docker reports OOM kills through ``State.OOMKilled``. A process killed for
    exceeding the wall-clock timeout usually exits with 137 (SIGKILL) only
    after the future runtime has raised its own timeout flag. Exit codes alone
    cannot distinguish timeout, cancellation, and other forced stops.
    """

    if not isinstance(exit_code, int) or isinstance(exit_code, bool):
        raise SandboxConfigurationError("Container exit code must be an integer.")
    if cancelled:
        return SandboxTermination.CANCELLED
    if timed_out:
        return SandboxTermination.TIMED_OUT
    if oom_killed:
        return SandboxTermination.RESOURCE_EXHAUSTED
    if exit_code == 0:
        return SandboxTermination.COMPLETED
    return SandboxTermination.FAILED


def _require_int(value: object, *, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise SandboxConfigurationError(f"{label} must be an integer.")
    return value


def _require_number(value: object, *, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise SandboxConfigurationError(f"{label} must be a number.")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise SandboxConfigurationError(f"{label} must be finite.")
    return number


@dataclass(frozen=True)
class SandboxMount:
    """One validated host bind mount inside an execution container."""

    source: Path
    target: str = SANDBOX_INPUT_PATH
    read_only: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.source, Path):
            object.__setattr__(self, "source", Path(self.source))
        if self.target != SANDBOX_INPUT_PATH:
            raise SandboxConfigurationError(
                f"Sandbox mounts must target {SANDBOX_INPUT_PATH}."
            )
        if self.read_only is not True:
            raise SandboxConfigurationError("Sandbox mounts must be read-only.")

    def validate(self, *, allowed_root: Path) -> Path:
        """Resolve and validate the source against the permitted dataset root."""
        source = self.source
        if not source.is_absolute():
            raise SandboxConfigurationError(
                "Sandbox mount sources must be absolute paths."
            )
        if (
            source.name in _FORBIDDEN_MOUNT_NAMES
            or source.suffix.lower() in _FORBIDDEN_MOUNT_SUFFIXES
        ):
            raise SandboxConfigurationError(
                "Sandbox mounts must not include sockets or host control files."
            )
        try:
            root = Path(allowed_root).resolve(strict=True)
            resolved = source.resolve(strict=True)
        except (OSError, ValueError):
            raise SandboxConfigurationError(
                "The sandbox input file is unavailable."
            ) from None
        try:
            resolved.relative_to(root)
        except ValueError:
            raise SandboxConfigurationError(
                "The sandbox input file must be inside the permitted dataset "
                "root."
            ) from None
        if resolved == root or not resolved.is_file():
            raise SandboxConfigurationError(
                "The sandbox input must be a regular dataset file."
            )
        return resolved

    def docker_mount_arg(self) -> str:
        """Return a ``--mount`` value that exposes only this file read-only."""
        if not self.source.is_absolute():
            raise SandboxConfigurationError(
                "Sandbox mount sources must be absolute paths."
            )
        source = str(self.source)
        if any(character in source for character in (",", "\n", "\r", "\x00")):
            raise SandboxConfigurationError(
                "Sandbox mount sources contain unsupported characters."
            )
        return f"type=bind,source={source},target={self.target},readonly"


@dataclass(frozen=True)
class SandboxLimits:
    """Validated per-execution resource limits."""

    memory_bytes: int
    memory_swap_bytes: int
    cpu_limit: float
    pids_limit: int
    tmp_bytes: int
    execution_timeout_seconds: float
    max_stdout_bytes: int
    max_stderr_bytes: int

    def __post_init__(self) -> None:
        memory = _require_int(self.memory_bytes, label="Sandbox memory limit")
        swap = _require_int(self.memory_swap_bytes, label="Sandbox swap limit")
        cpu = _require_number(self.cpu_limit, label="Sandbox CPU limit")
        pids = _require_int(self.pids_limit, label="Sandbox process limit")
        tmp = _require_int(self.tmp_bytes, label="Sandbox temporary storage limit")
        timeout = _require_number(
            self.execution_timeout_seconds,
            label="Sandbox execution timeout",
        )
        stdout = _require_int(
            self.max_stdout_bytes,
            label="Sandbox standard output limit",
        )
        stderr = _require_int(
            self.max_stderr_bytes,
            label="Sandbox standard error limit",
        )

        if not MIN_SANDBOX_MEMORY_BYTES <= memory <= MAX_SANDBOX_MEMORY_BYTES:
            raise SandboxConfigurationError(
                "Sandbox memory limit is outside the allowed range."
            )
        if swap < memory:
            raise SandboxConfigurationError(
                "Sandbox swap limit must be greater than or equal to memory."
            )
        if swap > MAX_SANDBOX_MEMORY_BYTES:
            raise SandboxConfigurationError(
                "Sandbox swap limit is outside the allowed range."
            )
        if not MIN_SANDBOX_CPU_LIMIT <= cpu <= MAX_SANDBOX_CPU_LIMIT:
            raise SandboxConfigurationError(
                "Sandbox CPU limit is outside the allowed range."
            )
        if not MIN_SANDBOX_PIDS_LIMIT <= pids <= MAX_SANDBOX_PIDS_LIMIT:
            raise SandboxConfigurationError(
                "Sandbox process limit is outside the allowed range."
            )
        if not MIN_SANDBOX_TMP_BYTES <= tmp <= MAX_SANDBOX_TMP_BYTES:
            raise SandboxConfigurationError(
                "Sandbox temporary storage limit is outside the allowed range."
            )
        if not 0 < timeout <= MAX_SANDBOX_EXECUTION_TIMEOUT_SECONDS:
            raise SandboxConfigurationError(
                "Sandbox execution timeout is outside the allowed range."
            )
        if not 1 <= stdout <= MAX_SANDBOX_STDOUT_BYTES:
            raise SandboxConfigurationError(
                "Sandbox standard output limit is outside the allowed range."
            )
        if not 1 <= stderr <= MAX_SANDBOX_STDERR_BYTES:
            raise SandboxConfigurationError(
                "Sandbox standard error limit is outside the allowed range."
            )

    @property
    def nano_cpus(self) -> int:
        """Return the Docker CPU quota in nanocpus."""
        return int(round(self.cpu_limit * 1_000_000_000))


@dataclass(frozen=True)
class SandboxContainerSpec:
    """A fully resolved, validated description of one execution container."""

    image: str
    name: str
    labels: Mapping[str, str]
    user: str
    working_dir: str
    network_mode: str
    read_only: bool
    privileged: bool
    cap_drop: tuple[str, ...]
    cap_add: tuple[str, ...]
    security_opt: tuple[str, ...]
    seccomp_profile: str
    pid_mode: Optional[str]
    ipc_mode: str
    environment: Mapping[str, str]
    mounts: tuple[SandboxMount, ...]
    dataset_root: Path
    tmpfs: Mapping[str, str]
    limits: SandboxLimits
    ulimits: Mapping[str, tuple[int, int]]
    stop_timeout_seconds: int
    stdin_open: bool = False
    tty: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.dataset_root, Path):
            object.__setattr__(self, "dataset_root", Path(self.dataset_root))
        self.validate()

    def validate(self) -> None:
        """Reject any specification that weakens the sandbox policy."""
        validate_sandbox_image(self.image)
        validate_sandbox_container_name(self.name)
        if self.labels.get(SANDBOX_MANAGED_LABEL) != "true":
            raise SandboxConfigurationError(
                "Sandbox containers must carry the managed label."
            )
        if not self.labels.get(SANDBOX_EXECUTION_LABEL):
            raise SandboxConfigurationError(
                "Sandbox containers must carry an execution identifier label."
            )
        if self.user != SANDBOX_WORKER_USER:
            raise SandboxConfigurationError(
                f"Sandbox containers must run as {SANDBOX_WORKER_USER}."
            )
        if self.working_dir != SANDBOX_RUNTIME_DIR:
            raise SandboxConfigurationError(
                f"Sandbox runtime must be mounted at {SANDBOX_RUNTIME_DIR}."
            )
        if self.network_mode != SANDBOX_NETWORK_MODE:
            raise SandboxConfigurationError(
                "Sandbox network mode must be 'none'."
            )
        if self.read_only is not True:
            raise SandboxConfigurationError(
                "The sandbox root filesystem must be read-only."
            )
        if self.privileged is not False:
            raise SandboxConfigurationError(
                "Sandbox containers must never be privileged."
            )
        if self.cap_drop != ("ALL",) or self.cap_add:
            raise SandboxConfigurationError(
                "Sandbox containers must drop all capabilities."
            )
        if "no-new-privileges:true" not in self.security_opt:
            raise SandboxConfigurationError(
                "Sandbox containers must enable no-new-privileges."
            )
        if any("unconfined" in option for option in self.security_opt):
            raise SandboxConfigurationError(
                "Sandbox containers must not disable mandatory access controls."
            )
        if self.seccomp_profile != SANDBOX_SECCOMP_PROFILE:
            raise SandboxConfigurationError(
                "Sandbox containers must use Docker's default seccomp profile."
            )
        if self.pid_mode in {"host", "container"}:
            raise SandboxConfigurationError(
                "Sandbox containers must not share a host PID namespace."
            )
        if self.ipc_mode in {"host", "shareable", "container"}:
            raise SandboxConfigurationError(
                "Sandbox containers must not share a host IPC namespace."
            )
        if self.ipc_mode != "none":
            raise SandboxConfigurationError(
                "Sandbox IPC namespace must be isolated."
            )
        if len(self.mounts) != 1:
            raise SandboxConfigurationError(
                "Sandbox containers must have exactly one read-only dataset "
                "mount."
            )
        mount = self.mounts[0]
        if mount.target != SANDBOX_INPUT_PATH or mount.read_only is not True:
            raise SandboxConfigurationError(
                "The dataset mount must be read-only at the input path."
            )
        mount.validate(allowed_root=self.dataset_root)
        if set(self.tmpfs) != {SANDBOX_TMP_DIR}:
            raise SandboxConfigurationError(
                "Sandbox containers may only use the restricted temporary "
                "filesystem."
            )
        tmpfs_options = self.tmpfs[SANDBOX_TMP_DIR]
        if not self._tmpfs_options_are_safe(tmpfs_options):
            raise SandboxConfigurationError(
                "The sandbox temporary filesystem options are unsafe."
            )
        for key in self.environment:
            if (
                _FORBIDDEN_ENVIRONMENT_KEY.search(key)
                or key in _FORBIDDEN_ENVIRONMENT_NAMES
            ):
                raise SandboxConfigurationError(
                    "Unsafe environment variables are not allowed in the "
                    "sandbox."
                )

    @staticmethod
    def _tmpfs_options_are_safe(options: str) -> bool:
        parts = {part.strip() for part in options.split(",") if part.strip()}
        required = {"rw", "noexec", "nosuid", "nodev"}
        if not required <= parts:
            return False
        for part in parts:
            if part.startswith("size="):
                try:
                    size = parse_docker_size(part.split("=", 1)[1])
                except SandboxConfigurationError:
                    return False
                if not MIN_SANDBOX_TMP_BYTES <= size <= MAX_SANDBOX_TMP_BYTES:
                    return False
                break
        else:
            return False
        return True

    def docker_run_options(self) -> list[str]:
        """Return Docker run flags before the image name.

        This method never includes ``--privileged``, ``--publish``,
        ``--pid=host``, ``--ipc=host``, ``--network=host``, a Docker socket
        mount, or an environment file.
        """
        options = [
            "--name",
            self.name,
            "--label",
            f"{SANDBOX_MANAGED_LABEL}=true",
            "--label",
            f"{SANDBOX_EXECUTION_LABEL}={self.labels[SANDBOX_EXECUTION_LABEL]}",
            "--user",
            self.user,
            "--workdir",
            self.working_dir,
            f"--network={self.network_mode}",
            f"--ipc={self.ipc_mode}",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
            f"--pids-limit={self.limits.pids_limit}",
            f"--memory={self.limits.memory_bytes}",
            f"--memory-swap={self.limits.memory_swap_bytes}",
            f"--cpus={self.limits.cpu_limit:g}",
            f"--tmpfs={SANDBOX_TMP_DIR}:{self.tmpfs[SANDBOX_TMP_DIR]}",
            f"--mount={self.mounts[0].docker_mount_arg()}",
            f"--stop-timeout={self.stop_timeout_seconds}",
        ]
        for key in sorted(self.environment):
            options.extend(["--env", f"{key}={self.environment[key]}"])
        for name in sorted(self.ulimits):
            soft, hard = self.ulimits[name]
            options.extend(["--ulimit", f"{name}={soft}:{hard}"])
        return options

    def docker_run_args(self) -> list[str]:
        """Return a complete ``docker run`` argument list without a command."""
        return ["docker", "run", *self.docker_run_options(), self.image]

    def effective_timeout_seconds(self, requested_seconds: float) -> float:
        """Return a timeout that cannot exceed the sandbox hard limit."""
        requested = _require_number(
            requested_seconds,
            label="Requested execution timeout",
        )
        if requested <= 0:
            raise SandboxConfigurationError(
                "Requested execution timeout must be positive."
            )
        return min(requested, self.limits.execution_timeout_seconds)


def _safe_environment(
    extra: Optional[Mapping[str, str]] = None,
) -> dict[str, str]:
    environment = dict(_SAFE_BASE_ENVIRONMENT)
    if extra is None:
        return environment
    for key, value in extra.items():
        if not isinstance(key, str) or _ENVIRONMENT_KEY_PATTERN.fullmatch(
            key
        ) is None:
            raise SandboxConfigurationError(
                "Sandbox environment variable names must be uppercase."
            )
        if _FORBIDDEN_ENVIRONMENT_KEY.search(key):
            raise SandboxConfigurationError(
                "Sandbox environment variables must not contain secrets."
            )
        if key in _FORBIDDEN_ENVIRONMENT_NAMES:
            raise SandboxConfigurationError(
                "Sandbox environment variables must not override process "
                "loading or search paths."
            )
        if not isinstance(value, str) or "\x00" in value:
            raise SandboxConfigurationError(
                "Sandbox environment variable values must be valid strings."
            )
        environment[key] = value
    return environment


@dataclass(frozen=True)
class SandboxPolicy:
    """Reusable security policy that produces per-execution container specs."""

    image: str
    limits: SandboxLimits
    network_mode: str = SANDBOX_NETWORK_MODE
    read_only_root: bool = True
    seccomp_profile: str = SANDBOX_SECCOMP_PROFILE

    def __post_init__(self) -> None:
        validate_sandbox_image(self.image)
        if self.network_mode != SANDBOX_NETWORK_MODE:
            raise SandboxConfigurationError(
                "The sandbox network mode must be 'none'."
            )
        if self.read_only_root is not True:
            raise SandboxConfigurationError(
                "The sandbox root filesystem must be read-only."
            )
        if self.seccomp_profile != SANDBOX_SECCOMP_PROFILE:
            raise SandboxConfigurationError(
                "The sandbox must use Docker's default seccomp profile."
            )

    @classmethod
    def from_settings(cls, settings: Any) -> "SandboxPolicy":
        """Build and validate a policy from the application settings object."""
        memory_limit = getattr(
            settings,
            "sandbox_memory_limit",
            DEFAULT_SANDBOX_MEMORY_LIMIT,
        )
        memory_bytes = parse_docker_size(
            normalize_docker_size(
                memory_limit,
                label="SANDBOX_MEMORY_LIMIT",
                minimum=MIN_SANDBOX_MEMORY_BYTES,
                maximum=MAX_SANDBOX_MEMORY_BYTES,
            ),
            label="SANDBOX_MEMORY_LIMIT",
        )
        swap_value = getattr(settings, "sandbox_memory_swap_limit", None)
        if swap_value in (None, ""):
            swap_bytes = memory_bytes
        else:
            swap_bytes = parse_docker_size(
                normalize_docker_size(
                    swap_value,
                    label="SANDBOX_MEMORY_SWAP_LIMIT",
                    minimum=MIN_SANDBOX_MEMORY_BYTES,
                    maximum=MAX_SANDBOX_MEMORY_BYTES,
                ),
                label="SANDBOX_MEMORY_SWAP_LIMIT",
            )
        tmp_bytes = parse_docker_size(
            normalize_docker_size(
                getattr(settings, "sandbox_tmp_size", DEFAULT_SANDBOX_TMP_SIZE),
                label="SANDBOX_TMP_SIZE",
                minimum=MIN_SANDBOX_TMP_BYTES,
                maximum=MAX_SANDBOX_TMP_BYTES,
            ),
            label="SANDBOX_TMP_SIZE",
        )
        limits = SandboxLimits(
            memory_bytes=memory_bytes,
            memory_swap_bytes=swap_bytes,
            cpu_limit=_require_number(
                getattr(
                    settings,
                    "sandbox_cpu_limit",
                    DEFAULT_SANDBOX_CPU_LIMIT,
                ),
                label="SANDBOX_CPU_LIMIT",
            ),
            pids_limit=_require_int(
                getattr(
                    settings,
                    "sandbox_pids_limit",
                    DEFAULT_SANDBOX_PIDS_LIMIT,
                ),
                label="SANDBOX_PIDS_LIMIT",
            ),
            tmp_bytes=tmp_bytes,
            execution_timeout_seconds=_require_number(
                getattr(
                    settings,
                    "sandbox_execution_timeout_seconds",
                    DEFAULT_SANDBOX_EXECUTION_TIMEOUT_SECONDS,
                ),
                label="SANDBOX_EXECUTION_TIMEOUT_SECONDS",
            ),
            max_stdout_bytes=_require_int(
                getattr(
                    settings,
                    "sandbox_max_stdout_bytes",
                    DEFAULT_SANDBOX_MAX_STDOUT_BYTES,
                ),
                label="SANDBOX_MAX_STDOUT_BYTES",
            ),
            max_stderr_bytes=_require_int(
                getattr(
                    settings,
                    "sandbox_max_stderr_bytes",
                    DEFAULT_SANDBOX_MAX_STDERR_BYTES,
                ),
                label="SANDBOX_MAX_STDERR_BYTES",
            ),
        )
        return cls(
            image=getattr(settings, "sandbox_worker_image", DEFAULT_SANDBOX_IMAGE),
            limits=limits,
            network_mode=getattr(
                settings,
                "sandbox_network_mode",
                SANDBOX_NETWORK_MODE,
            ),
            read_only_root=getattr(
                settings,
                "sandbox_read_only_root",
                True,
            ),
            seccomp_profile=getattr(
                settings,
                "sandbox_seccomp_profile",
                SANDBOX_SECCOMP_PROFILE,
            ),
        )

    def container_spec(
        self,
        *,
        dataset_file: Path,
        dataset_root: Path,
        execution_id: Optional[object] = None,
        environment: Optional[Mapping[str, str]] = None,
    ) -> SandboxContainerSpec:
        """Build one validated execution-container specification.

        The caller supplies only the selected dataset file. Arbitrary mounts,
        images, ports, capabilities, and resource limits are not accepted from
        execution requests.
        """
        parsed_execution_id = (
            uuid4()
            if execution_id is None
            else canonical_execution_id(execution_id)
        )
        mount = SandboxMount(source=Path(dataset_file))
        resolved_source = mount.validate(allowed_root=Path(dataset_root))
        mount = SandboxMount(source=resolved_source)
        spec = SandboxContainerSpec(
            image=self.image,
            name=sandbox_container_name(parsed_execution_id),
            labels=sandbox_container_labels(parsed_execution_id),
            user=SANDBOX_WORKER_USER,
            working_dir=SANDBOX_RUNTIME_DIR,
            network_mode=self.network_mode,
            read_only=self.read_only_root,
            privileged=False,
            cap_drop=("ALL",),
            cap_add=(),
            security_opt=("no-new-privileges:true",),
            seccomp_profile=self.seccomp_profile,
            pid_mode=None,
            ipc_mode="none",
            environment=_safe_environment(environment),
            mounts=(mount,),
            dataset_root=Path(dataset_root),
            tmpfs={
                SANDBOX_TMP_DIR: (
                    f"rw,noexec,nosuid,nodev,"
                    f"size={format_docker_size(self.limits.tmp_bytes)},"
                    f"mode={SANDBOX_TMPFS_MODE}"
                )
            },
            limits=self.limits,
            ulimits={
                "nofile": (
                    SANDBOX_NOFILE_SOFT_LIMIT,
                    SANDBOX_NOFILE_HARD_LIMIT,
                ),
                "core": (SANDBOX_CORE_LIMIT, SANDBOX_CORE_LIMIT),
            },
            stop_timeout_seconds=SANDBOX_STOP_TIMEOUT_SECONDS,
        )
        return spec
