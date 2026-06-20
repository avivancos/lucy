"""Deprecated location: the worker moved to lucy.serve.worker (card 23).

`python -m lucy.worker` keeps working as a transition shim until the compose
target flips (card 21). New deployments should target `lucy.serve.worker`.
"""

from __future__ import annotations

import asyncio
import warnings

from lucy.serve.worker import main

__all__ = ["main"]

warnings.warn(
    "lucy.worker moved to lucy.serve.worker; use `python -m lucy.serve.worker`",
    DeprecationWarning,
    stacklevel=2,
)


if __name__ == "__main__":
    asyncio.run(main())
