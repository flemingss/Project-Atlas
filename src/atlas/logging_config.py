"""Logging setup for the Atlas application loggers.

Why this exists at all: under uvicorn only the ``uvicorn.*`` loggers get a
handler, and ``logging.basicConfig()`` is a no-op once uvicorn has installed
its own root configuration. The practical effect was that every ``atlas.*``
record — session evictions, ledger write failures, maintenance actions — was
silently discarded, which is why lifecycle events left no trace in
``docker logs`` and one module had resorted to a bare ``print()`` to be seen.

Attaching the ``atlas`` package logger to uvicorn's handler puts application
logs beside request logs in the same stream, with the same formatting.
"""
from __future__ import annotations

import logging

_PACKAGE_LOGGER = "atlas"


def configure_logging(level: str = "INFO") -> None:
    """Make ``atlas.*`` log records visible at *level*.

    Safe to call more than once — handlers are never duplicated.
    """
    resolved = getattr(logging, level.upper(), logging.INFO)

    app_log = logging.getLogger(_PACKAGE_LOGGER)
    app_log.setLevel(resolved)

    # Prefer uvicorn's handler so app logs share the server's stream and format.
    uvicorn_error = logging.getLogger("uvicorn.error")
    handlers = uvicorn_error.handlers or logging.getLogger().handlers

    if handlers:
        # Propagating as well would double every line, since our own handler
        # already writes to the same stream the root handler uses.
        app_log.propagate = False
        existing = set(app_log.handlers)
        for h in handlers:
            if h not in existing:
                app_log.addHandler(h)
    else:
        # Standalone (CLI, tests): no server handler to borrow.
        logging.basicConfig(level=resolved)
        app_log.propagate = True

    app_log.debug("Atlas logging configured at %s", level.upper())
