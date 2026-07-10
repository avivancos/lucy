"""Typed ElevenLabs environment settings."""

from typing import Optional

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ElevenLabsSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ELEVENLABS_", extra="ignore")

    api_key: Optional[SecretStr] = None
    voice_id: Optional[str] = None
    ws_url: str = "wss://api.elevenlabs.io"
    output_format: str = "pcm_16000"
    sync_alignment: bool = True
