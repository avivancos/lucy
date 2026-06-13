"""Background worker entrypoint for Lucy."""

from __future__ import annotations

import asyncio
import logging


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logging.info("lucy-worker started")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())

