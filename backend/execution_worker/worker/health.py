"""Startup health checks for the execution worker image."""

from __future__ import annotations

import importlib
import json
import sys
from typing import Any

from worker import __version__

WORKER_NAME = "ai-data-analyst-execution-worker"
EXPECTED_PYTHON_VERSION = (3, 12)
REQUIRED_LIBRARIES = ("numpy", "pandas")


def _python_version() -> str:
    return ".".join(str(part) for part in sys.version_info[:3])


def _library_status(name: str) -> dict[str, Any]:
    try:
        module = importlib.import_module(name)
    except Exception as exc:
        return {
            "available": False,
            "version": None,
            "error": type(exc).__name__,
        }
    return {
        "available": True,
        "version": str(getattr(module, "__version__", "unknown")),
        "error": None,
    }


def run_health_check() -> dict[str, Any]:
    """Return bounded worker metadata and required-library status."""
    libraries = {name: _library_status(name) for name in REQUIRED_LIBRARIES}
    python_supported = sys.version_info[:2] == EXPECTED_PYTHON_VERSION
    healthy = python_supported and all(
        details["available"] for details in libraries.values()
    )
    return {
        "status": "ok" if healthy else "error",
        "worker": WORKER_NAME,
        "version": __version__,
        "python": _python_version(),
        "python_supported": python_supported,
        "expected_python": ".".join(
            str(part) for part in EXPECTED_PYTHON_VERSION
        ),
        "libraries": libraries,
    }


def main() -> int:
    result = run_health_check()
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
