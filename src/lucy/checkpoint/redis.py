"""Redis hot-tier checkpoint store using append-only per-thread lists."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from lucy.state import Checkpoint, CheckpointStore

try:
    from redis.asyncio import Redis
except ImportError:  # pragma: no cover - exercised by installations without extra.
    Redis = None  # type: ignore[assignment,misc]


REDIS_CHECKPOINT_KEY_PREFIX = "lucy:checkpoints"
DEFAULT_CHECKPOINT_TTL_SECONDS = 86_400


class RedisCheckpointDependencyError(RuntimeError):
    """Raised when the optional Redis dependency is not installed."""


class RedisCheckpointStore(CheckpointStore):
    def __init__(
        self,
        url: str,
        *,
        ttl_seconds: int = DEFAULT_CHECKPOINT_TTL_SECONDS,
        key_prefix: str = REDIS_CHECKPOINT_KEY_PREFIX,
    ) -> None:
        if not url.strip():
            raise ValueError("Redis checkpoint URL cannot be blank")
        if ttl_seconds <= 0:
            raise ValueError("Redis checkpoint TTL must be greater than zero")
        if not key_prefix.strip():
            raise ValueError("Redis checkpoint key prefix cannot be blank")
        if Redis is None:
            raise RedisCheckpointDependencyError(
                "install lucy[checkpoint-redis] to use RedisCheckpointStore"
            )
        self._client: Any = Redis.from_url(url, decode_responses=True)
        self._ttl_seconds = ttl_seconds
        self._key_prefix = key_prefix

    def _key(self, thread_id: str) -> str:
        return "%s:%s" % (self._key_prefix, thread_id)

    async def save(self, checkpoint: Checkpoint) -> None:
        pipeline = self._client.pipeline(transaction=True)
        pipeline.rpush(self._key(checkpoint.thread_id), checkpoint.model_dump_json())
        pipeline.expire(self._key(checkpoint.thread_id), self._ttl_seconds)
        await pipeline.execute()

    async def load_latest(self, thread_id: str) -> Optional[Checkpoint]:
        history = await self.history(thread_id)
        return history[-1].model_copy(deep=True) if history else None

    async def history(self, thread_id: str) -> List[Checkpoint]:
        payloads = await self._client.lrange(self._key(thread_id), 0, -1)
        first_position: Dict[str, int] = {}
        latest: Dict[str, Checkpoint] = {}
        for index, payload in enumerate(payloads):
            checkpoint = Checkpoint.model_validate_json(payload)
            first_position.setdefault(checkpoint.checkpoint_id, index)
            latest[checkpoint.checkpoint_id] = checkpoint
        return [
            latest[checkpoint_id].model_copy(deep=True)
            for checkpoint_id in sorted(
                latest,
                key=first_position.__getitem__,
            )
        ]

    async def clear_thread(self, thread_id: str) -> None:
        await self._client.delete(self._key(thread_id))

    async def thread_ttl(self, thread_id: str) -> int:
        return int(await self._client.ttl(self._key(thread_id)))

    async def close(self) -> None:
        await self._client.aclose()
