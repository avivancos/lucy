"""Deterministic BlobStore failure simulator for recording lifecycle tests."""

from __future__ import annotations

import asyncio
from typing import Optional

from lucy.transport.schema import RecordingUploaded


class RecordingBlobStoreSimulator:
    def __init__(
        self,
        *,
        fail_on_prepare: Optional[int] = None,
        block_on_prepare: Optional[int] = None,
        block_on_discard_upload: Optional[int] = None,
        fail_on_discard_upload: Optional[int] = None,
        fail_on_discard_blob: Optional[int] = None,
    ) -> None:
        self.fail_on_prepare = fail_on_prepare
        self.block_on_prepare = block_on_prepare
        self.block_on_discard_upload = block_on_discard_upload
        self.fail_on_discard_upload = fail_on_discard_upload
        self.fail_on_discard_blob = fail_on_discard_blob
        self.prepare_calls = 0
        self.discard_upload_calls = 0
        self.discard_blob_calls = 0
        self.prepared: list[str] = []
        self.prepare_started = asyncio.Event()
        self.release_prepare = asyncio.Event()
        self.discard_upload_started = asyncio.Event()
        self.release_discard_upload = asyncio.Event()

    async def prepare_upload(self, blob_id: str) -> str:
        self.prepare_calls += 1
        if self.block_on_prepare == self.prepare_calls:
            self.prepare_started.set()
            await self.release_prepare.wait()
        if self.fail_on_prepare == self.prepare_calls:
            raise RuntimeError(f"target {self.prepare_calls} failed")
        upload_ref = f"upload-{blob_id}"
        self.prepared.append(upload_ref)
        return upload_ref

    async def discard_upload(self, upload_url_ref: str) -> None:
        self.discard_upload_calls += 1
        if self.block_on_discard_upload == self.discard_upload_calls:
            self.discard_upload_started.set()
            await self.release_discard_upload.wait()
        if self.fail_on_discard_upload == self.discard_upload_calls:
            raise RuntimeError(f"upload discard {self.discard_upload_calls} failed")
        if upload_url_ref in self.prepared:
            self.prepared.remove(upload_url_ref)

    async def confirm_upload(self, event: RecordingUploaded) -> bool:
        return False

    async def discard_blob(self, blob_id: str) -> None:
        self.discard_blob_calls += 1
        if self.fail_on_discard_blob == self.discard_blob_calls:
            raise RuntimeError(f"blob discard {self.discard_blob_calls} failed")
        return None
