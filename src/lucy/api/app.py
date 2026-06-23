"""Deprecated: the framework app moved to lucy.serve.app (card 23).

The platform fleet routes were extracted to lucy-platform (card 21) and the Pili
vertical to the pili repo (card 20), so this shim now just re-exposes the
framework serving app under the legacy import path. Importing this module emits
DeprecationWarning. `uvicorn lucy.api.app:app` keeps working until the
compose/Dockerfile target flips to `lucy.serve.app:create_app`.
"""

from __future__ import annotations

import warnings

from fastapi import FastAPI

from lucy.serve.app import create_app as _create_framework_app

warnings.warn(
    "lucy.api.app is deprecated; use lucy.serve.app:create_app",
    DeprecationWarning,
    stacklevel=2,
)


def create_app() -> FastAPI:
    """Return the open framework serving app (process-local routes only)."""
    return _create_framework_app()


app = create_app()
