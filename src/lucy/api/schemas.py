"""Deprecated: framework schemas moved to lucy.serve.schemas (card 23).

The platform fleet response models were extracted to lucy-platform (card 21) and
the Pili response models to the pili repo (card 20). This module now only
re-exports the framework base schemas for backward compatibility. Importing from
here emits DeprecationWarning.
"""

from __future__ import annotations

import warnings

from lucy.serve.schemas import HealthResponse, LucyApiModel

warnings.warn(
    "lucy.api.schemas is deprecated; framework schemas live in lucy.serve.schemas",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "LucyApiModel",
    "HealthResponse",
]
