"""Postgres checkpoint store backed by an idempotent JSONB event table."""

from __future__ import annotations

import asyncio
from typing import Any, List, Optional

from lucy.state import Checkpoint, CheckpointStore

try:
    import asyncpg
except ImportError:  # pragma: no cover - exercised by installations without extra.
    asyncpg = None  # type: ignore[assignment]


CHECKPOINT_TABLE = "lucy_checkpoints"
CHECKPOINT_THREAD_INDEX = "ix_lucy_checkpoints_thread_sequence"


class PostgresCheckpointDependencyError(RuntimeError):
    """Raised when the optional Postgres dependency is not installed."""


class PostgresCheckpointStore(CheckpointStore):
    def __init__(self, dsn: str) -> None:
        if not dsn.strip():
            raise ValueError("Postgres checkpoint DSN cannot be blank")
        if asyncpg is None:
            raise PostgresCheckpointDependencyError(
                "install lucy[checkpoint-postgres] to use PostgresCheckpointStore"
            )
        self._dsn = dsn
        self._pool: Any = None
        self._initialize_lock = asyncio.Lock()

    async def _pool_ready(self) -> Any:
        if self._pool is not None:
            return self._pool
        async with self._initialize_lock:
            if self._pool is None:
                self._pool = await asyncpg.create_pool(self._dsn)
                async with self._pool.acquire() as connection:
                    await connection.execute(
                        f"""
                        CREATE TABLE IF NOT EXISTS {CHECKPOINT_TABLE} (
                            sequence BIGSERIAL PRIMARY KEY,
                            checkpoint_id TEXT UNIQUE NOT NULL,
                            thread_id TEXT NOT NULL,
                            session_id TEXT NOT NULL,
                            created_at_ms BIGINT NOT NULL,
                            payload JSONB NOT NULL
                        )
                        """
                    )
                    await connection.execute(
                        f"""
                        CREATE INDEX IF NOT EXISTS {CHECKPOINT_THREAD_INDEX}
                        ON {CHECKPOINT_TABLE} (thread_id, sequence)
                        """
                    )
        return self._pool

    async def save(self, checkpoint: Checkpoint) -> None:
        pool = await self._pool_ready()
        async with pool.acquire() as connection:
            await connection.execute(
                f"""
                INSERT INTO {CHECKPOINT_TABLE} (
                    checkpoint_id, thread_id, session_id, created_at_ms, payload
                ) VALUES ($1, $2, $3, $4, $5::jsonb)
                ON CONFLICT (checkpoint_id) DO UPDATE SET
                    thread_id = EXCLUDED.thread_id,
                    session_id = EXCLUDED.session_id,
                    created_at_ms = EXCLUDED.created_at_ms,
                    payload = EXCLUDED.payload
                """,
                checkpoint.checkpoint_id,
                checkpoint.thread_id,
                checkpoint.session_id,
                checkpoint.created_at_ms,
                checkpoint.model_dump_json(),
            )

    async def load_latest(self, thread_id: str) -> Optional[Checkpoint]:
        pool = await self._pool_ready()
        async with pool.acquire() as connection:
            payload = await connection.fetchval(
                f"""
                SELECT payload::text
                FROM {CHECKPOINT_TABLE}
                WHERE thread_id = $1
                ORDER BY sequence DESC
                LIMIT 1
                """,
                thread_id,
            )
        return Checkpoint.model_validate_json(payload) if payload is not None else None

    async def history(self, thread_id: str) -> List[Checkpoint]:
        pool = await self._pool_ready()
        async with pool.acquire() as connection:
            rows = await connection.fetch(
                f"""
                SELECT payload::text AS payload
                FROM {CHECKPOINT_TABLE}
                WHERE thread_id = $1
                ORDER BY sequence ASC
                """,
                thread_id,
            )
        return [Checkpoint.model_validate_json(row["payload"]) for row in rows]

    async def clear_thread(self, thread_id: str) -> None:
        pool = await self._pool_ready()
        async with pool.acquire() as connection:
            await connection.execute(
                f"DELETE FROM {CHECKPOINT_TABLE} WHERE thread_id = $1",
                thread_id,
            )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
