"""Static and local-runtime tests for the Phase 5.2 execution worker image."""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from typing import Any

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
WORKER_ROOT = BACKEND_ROOT / "execution_worker"
WORKER_PACKAGE = WORKER_ROOT / "worker"

sys.path.insert(0, str(WORKER_ROOT))

from worker import __version__ as worker_version
from worker import health as worker_health
from worker import main as worker_main

ALLOWED_REQUIREMENTS = {
    "numpy",
    "pandas",
    "python-dateutil",
    "pytz",
    "six",
    "tzdata",
}
FORBIDDEN_REQUIREMENTS = {
    "fastapi",
    "google-genai",
    "httpx",
    "pydantic",
    "pydantic-settings",
    "python-multipart",
    "scipy",
    "uvicorn",
}
PINNED_REQUIREMENT = re.compile(r"^[A-Za-z0-9_.-]+==[A-Za-z0-9_.+!-]+$")


def dockerfile_text() -> str:
    return (WORKER_ROOT / "Dockerfile").read_text()


def requirements() -> dict[str, str]:
    pins: dict[str, str] = {}
    for raw_line in (WORKER_ROOT / "requirements.txt").read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        assert PINNED_REQUIREMENT.fullmatch(line), line
        name, version = line.split("==", 1)
        pins[name.lower()] = version
    return pins


def healthy_payload() -> dict[str, Any]:
    return {
        "status": "ok",
        "worker": worker_health.WORKER_NAME,
        "version": worker_version,
        "python": "3.12.0",
        "python_supported": True,
        "expected_python": "3.12",
        "libraries": {
            "numpy": {"available": True, "version": "2.0.2", "error": None},
            "pandas": {"available": True, "version": "2.2.3", "error": None},
        },
    }


def test_worker_files_use_isolated_layout() -> None:
    expected = {
        "Dockerfile",
        "requirements.txt",
        ".dockerignore",
        "README.md",
        "verify_image.sh",
        "worker/__init__.py",
        "worker/health.py",
        "worker/main.py",
    }
    actual = {
        str(path.relative_to(WORKER_ROOT))
        for path in WORKER_ROOT.rglob("*")
        if path.is_file()
    }

    assert expected <= actual
    assert not (WORKER_ROOT / "app").exists()
    assert not (WORKER_ROOT / "tests").exists()


def test_dockerfile_uses_pinned_slim_base_and_non_root_runtime() -> None:
    text = dockerfile_text()

    assert text.splitlines()[0] == "FROM python:3.12-slim-bookworm"
    assert ":latest" not in text
    assert "WORKDIR /worker" in text
    assert 'org.opencontainers.image.title="ai-data-analyst-execution-worker"' in text
    assert 'org.opencontainers.image.version="0.1.0"' in text
    assert "USER 10001:10001" in text
    assert "groupadd --gid 10001 worker" in text
    assert "useradd --uid 10001 --gid worker" in text
    assert "--no-create-home" in text
    assert text.count("USER ") == 1


def test_dockerfile_uses_cache_friendly_minimal_copy_order() -> None:
    text = dockerfile_text()
    copy_lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip().startswith("COPY ")
    ]

    assert copy_lines == [
        "COPY requirements.txt ./",
        "COPY --chown=worker:worker worker ./worker",
    ]
    assert text.index("COPY requirements.txt ./") < text.index(
        "RUN python -m pip install"
    )
    assert text.index("RUN python -m pip install") < text.index(
        "COPY --chown=worker:worker worker ./worker"
    )
    assert "app/" not in text


def test_dockerfile_has_no_ports_build_tools_or_embedded_secrets() -> None:
    text = dockerfile_text()
    lowered = text.lower()

    assert "EXPOSE" not in text
    assert "apt-get" not in lowered
    assert "apk " not in lowered
    assert "curl" not in lowered
    assert "wget" not in lowered
    assert "git clone" not in lowered
    assert "gemini" not in lowered
    assert "api_key" not in lowered
    assert "password" not in lowered
    assert "secret" not in lowered
    assert ".env" not in lowered


def test_dockerfile_uses_healthcheck_and_worker_entrypoint() -> None:
    text = dockerfile_text()

    assert "STOPSIGNAL SIGTERM" in text
    assert "HEALTHCHECK" in text
    assert 'CMD ["python", "-m", "worker.health"]' in text
    assert 'ENTRYPOINT ["python", "-m", "worker.main"]' in text
    assert "uvicorn" not in text
    assert "fastapi" not in text
    assert "worker.main --once" not in text


def test_requirements_are_pinned_analytics_dependencies_only() -> None:
    pins = requirements()

    assert {"pandas", "numpy"} <= set(pins)
    assert set(pins) <= ALLOWED_REQUIREMENTS
    assert not set(pins) & FORBIDDEN_REQUIREMENTS
    assert "scipy" not in pins
    assert pins["pandas"] == "2.2.3"
    assert pins["numpy"] == "2.0.2"


def test_dockerignore_excludes_secrets_data_and_local_artifacts() -> None:
    patterns = {
        line.strip()
        for line in (WORKER_ROOT / ".dockerignore").read_text().splitlines()
        if line.strip()
    }

    for required in {
        ".git",
        "**/.env",
        "**/.env.*",
        "**/.venv",
        "**/__pycache__",
        "**/*.py[cod]",
        "**/*.csv",
        "**/*.parquet",
        "**/data",
        "**/datasets",
        "**/*.pem",
        "**/*.key",
    }:
        assert required in patterns


def test_build_context_contains_no_secrets_or_local_datasets() -> None:
    for path in WORKER_ROOT.rglob("*"):
        if not path.is_file():
            continue
        assert not path.name.startswith(".env")
        assert path.suffix.lower() not in {
            ".csv",
            ".tsv",
            ".parquet",
            ".xlsx",
            ".xls",
            ".pem",
            ".key",
        }
        assert ".git" not in path.parts
        assert ".venv" not in path.parts
        assert "__pycache__" not in path.parts


def test_worker_execution_is_confined_to_runner_without_backend_imports() -> None:
    forbidden_calls = {"eval", "__import__"}
    execution_calls: set[str] = set()
    forbidden_modules = {
        "fastapi",
        "google",
        "httpx",
        "pydantic",
        "socket",
        "subprocess",
        "uvicorn",
    }

    for path in WORKER_PACKAGE.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in forbidden_calls, path
                if node.func.id in {"exec", "compile"}:
                    assert path.name == "execute.py", path
                    execution_calls.add(node.func.id)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".", 1)[0]
                    assert root not in forbidden_modules, path
            if isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".", 1)[0]
                assert root not in forbidden_modules, path

    assert execution_calls == {"exec", "compile"}


def test_health_check_reports_worker_identity_and_libraries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeModule:
        def __init__(self, version: str) -> None:
            self.__version__ = version

    versions = {"numpy": "2.0.2", "pandas": "2.2.3"}
    monkeypatch.setattr(worker_health.sys, "version_info", (3, 12, 0))
    monkeypatch.setattr(
        worker_health.importlib,
        "import_module",
        lambda name: FakeModule(versions[name]),
    )

    result = worker_health.run_health_check()

    assert result["status"] == "ok"
    assert result["worker"] == "ai-data-analyst-execution-worker"
    assert result["version"] == worker_version
    assert result["python"] == "3.12.0"
    assert result["python_supported"] is True
    assert result["libraries"]["pandas"]["version"] == "2.2.3"
    assert result["libraries"]["numpy"]["version"] == "2.0.2"


def test_health_check_fails_when_required_library_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeModule:
        __version__ = "2.0.2"

    def import_module(name: str) -> FakeModule:
        if name == "pandas":
            raise ImportError("pandas is unavailable")
        return FakeModule()

    monkeypatch.setattr(worker_health.sys, "version_info", (3, 12, 0))
    monkeypatch.setattr(worker_health.importlib, "import_module", import_module)

    result = worker_health.run_health_check()

    assert result["status"] == "error"
    assert result["libraries"]["pandas"] == {
        "available": False,
        "version": None,
        "error": "ImportError",
    }


def test_health_check_fails_on_unexpected_python_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker_health.sys, "version_info", (3, 11, 9))
    monkeypatch.setattr(
        worker_health.importlib,
        "import_module",
        lambda _name: type("FakeModule", (), {"__version__": "pinned"})(),
    )

    result = worker_health.run_health_check()

    assert result["status"] == "error"
    assert result["python_supported"] is False
    assert result["expected_python"] == "3.12"


def test_once_mode_exits_zero_after_successful_health_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        worker_main,
        "run_health_check",
        lambda: healthy_payload(),
    )

    assert worker_main.main(["--once"]) == 0


def test_once_mode_exits_nonzero_after_failed_health_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        worker_main,
        "run_health_check",
        lambda: {**healthy_payload(), "status": "error"},
    )

    assert worker_main.main(["--once"]) == 1


def test_wait_for_shutdown_returns_when_event_is_set() -> None:
    stop_event = threading.Event()
    stop_event.set()

    worker_main.wait_for_shutdown(stop_event)


def test_worker_exits_cleanly_on_sigterm() -> None:
    program = textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, {str(WORKER_ROOT)!r})
        import worker.main as worker_main
        worker_main.run_health_check = lambda: {healthy_payload()!r}
        raise SystemExit(worker_main.main([]))
        """
    )
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", program],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    output: list[str] = []
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            line = process.stdout.readline()
            if line:
                output.append(line)
                if "ready and idle" in line:
                    break
            elif process.poll() is not None:
                break
        else:
            process.kill()
            output.append(process.communicate(timeout=10)[0] or "")
            pytest.fail("Worker did not reach idle state.")

        assert process.poll() is None
        process.terminate()
        stdout, _ = process.communicate(timeout=10)
        output.append(stdout or "")
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)

    assert process.returncode == 0
    combined = "".join(output)
    assert "ready and idle" in combined
    assert "shutdown complete" in combined.lower()


def test_verification_script_covers_build_runtime_and_shutdown() -> None:
    script = (WORKER_ROOT / "verify_image.sh").read_text()

    assert "docker build" in script
    assert "--once" in script
    assert "worker.health" in script
    assert "--entrypoint id" in script
    assert "State.ExitCode" in script
    assert "docker stop" in script
    assert "fastapi" in script
    assert "forbidden_suffixes" in script


def test_worker_version_is_declared() -> None:
    assert worker_version == "0.1.0"
    assert worker_main.__version__ == worker_version
    assert worker_health.__version__ == worker_version
