"""Typed OpenAI environment settings."""

from typing import Optional

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class OpenAiSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OPENAI_", extra="ignore")

    api_key: Optional[SecretStr] = None
    base_url: str = "https://api.openai.com/v1"
    realtime_url: str = "wss://api.openai.com/v1/realtime"
