"""Idle execution worker entry point with startup health verification."""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
from typing import Optional, Sequence

from worker import __version__
from worker.health import WORKER_NAME, run_health_check

logger = logging.getLogger(WORKER_NAME)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Start the isolated execution worker. It performs a health check "
            "and remains idle until the process receives SIGINT or SIGTERM."
        )
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run the startup health check and exit without entering idle mode.",
    )
    return parser


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )


def _install_shutdown_handlers(stop_event: threading.Event) -> None:
    def request_shutdown(signum: int, _frame: object) -> None:
        logger.info("Received signal %s; shutting down.", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)


def wait_for_shutdown(stop_event: threading.Event) -> None:
    """Block until the shutdown event is set by a signal handler."""
    stop_event.wait()


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    _configure_logging()

    stop_event = threading.Event()
    _install_shutdown_handlers(stop_event)

    health = run_health_check()
    logger.info(
        "Worker health: %s",
        json.dumps(health, sort_keys=True),
    )
    if health["status"] != "ok":
        logger.error(
            "%s %s failed startup health verification.",
            WORKER_NAME,
            __version__,
        )
        return 1

    if args.once:
        logger.info(
            "%s %s startup health check passed.",
            WORKER_NAME,
            __version__,
        )
        return 0

    logger.info(
        "%s %s is ready and idle. No execution protocol is active.",
        WORKER_NAME,
        __version__,
    )
    wait_for_shutdown(stop_event)
    logger.info("%s %s shutdown complete.", WORKER_NAME, __version__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
