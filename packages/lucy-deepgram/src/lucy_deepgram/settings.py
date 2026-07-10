"""Typed Deepgram environment settings."""

from typing import Optional

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class DeepgramSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DEEPGRAM_", extra="ignore")

    api_key: Optional[SecretStr] = None
    realtime_url: str = "wss://api.deepgram.com/v1/listen"
    encoding: str = "linear16"
    sample_rate: int = 16_000
    channels: int = 1
    interim_results: bool = True
    endpointing_ms: int = 300
    frame_bytes: int = 3_200
