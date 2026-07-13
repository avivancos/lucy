"""Shared environment resolution for Lucy cloud clients."""

from __future__ import annotations

import os
from typing import Optional, Tuple

ENV_API_KEY = "LUCY_API_KEY"
ENV_BLOB_UPLOAD_ORIGINS = "LUCY_BLOB_UPLOAD_ORIGINS"
ENV_ENDPOINT = "LUCY_ENDPOINT"
ENV_PROJECT = "LUCY_PROJECT"


def cloud_enabled_from_env() -> bool:
    return bool(os.environ.get(ENV_API_KEY))


def blob_upload_origins_from_env() -> Optional[Tuple[str, ...]]:
    raw = os.environ.get(ENV_BLOB_UPLOAD_ORIGINS)
    if raw is None:
        return None
    origins = tuple(value.strip() for value in raw.split(",") if value.strip())
    if not origins:
        raise ValueError("Lucy blob upload origins cannot be empty")
    return origins


def resolve_cloud_connection(
    endpoint: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Tuple[str, str]:
    resolved_endpoint = (
        endpoint if endpoint is not None else os.environ.get(ENV_ENDPOINT)
    )
    resolved_api_key = api_key if api_key is not None else os.environ.get(ENV_API_KEY)
    if not resolved_endpoint or not resolved_api_key:
        raise ValueError("Lucy cloud endpoint and API key are required")
    return resolved_endpoint, resolved_api_key
