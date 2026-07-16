"""Typed latency budgets (ADR 0011, agents.md typed-config invariant).

Every timing threshold the runtime enforces lives here, never inline. Override
any field from the environment with the ``LUCY_BUDGET_`` prefix, e.g.
``LUCY_BUDGET_TURN_TOTAL_MS=500``.
"""

from __future__ import annotations

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from lucy.limits import MAX_CONTROL_DURATION_MS
from lucy.jurisdiction import IsoCountryCode
from lucy.specs import AsteriskTransportMode, CpaasTransportMode


MIN_NETWORK_PORT = 1
MAX_NETWORK_PORT = 65_535
CPAAS_REQUIRED_ENVIRONMENT_VARIABLES = (
    "LUCY_CPAAAS_PROVIDER",
    "LUCY_CPAAAS_ACCOUNT_ID",
    "LUCY_CPAAAS_API_KEY",
    "LUCY_CPAAAS_FROM_NUMBER",
    "LUCY_CPAAAS_TO_NUMBER",
)
CPAAS_STREAM_ENVIRONMENT_VARIABLES = (
    "LUCY_CPAAAS_API_BASE_URL",
    "LUCY_CPAAAS_PUBLIC_WS_URL",
    "LUCY_CPAAAS_STREAM_AUTH_TOKEN",
)


class LatencyBudgets(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LUCY_BUDGET_", extra="ignore")

    endpoint_silence_ms: int = 150  # trailing silence that ends the caller's turn
    stt_final_ms: int = 60  # partial -> final settle time
    control_transport_ms: int = 10  # one control-channel hop
    control_commit_ms: int = Field(
        default=1_000,
        ge=1,
        le=MAX_CONTROL_DURATION_MS,
        strict=True,
    )  # directive commit before transport abort
    graph_dispatch_ms: int = 10  # dispatch a turn into the graph runtime
    llm_first_clause_ms: int = 380  # first speakable clause from the LLM
    tts_first_byte_ms: int = 150  # first audio byte from TTS
    gateway_pacing_ms: int = 30  # gateway playback pacing granularity
    turn_total_ms: int = 800  # end-to-end p50 turn budget
    max_tool_rounds_per_turn: int = 3  # tool-call rounds before forcing a reply


class SpeculationSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LUCY_SPECULATION_", extra="ignore")

    enabled_rag_prefetch: bool = True
    enabled_llm_start: bool = False
    rag_prefetch_stability: float = 0.6
    llm_start_stability: float = 0.9

    @model_validator(mode="after")
    def _validate_threshold_order(self) -> "SpeculationSettings":
        if not (0.0 <= self.rag_prefetch_stability <= self.llm_start_stability <= 1.0):
            raise ValueError(
                "expected 0.0 <= rag_prefetch_stability <= llm_start_stability <= 1.0"
            )
        return self


class GraphLimits(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LUCY_GRAPH_", extra="ignore")

    max_supersteps_per_turn: int = 16


class GatewayControlSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LUCY_GATEWAY_", extra="ignore", env_ignore_empty=True
    )

    control_token: SecretStr | None = None

    @field_validator("control_token")
    @classmethod
    def validate_control_token(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        token = value.get_secret_value()
        if not token or any(
            character.isspace() or ord(character) < 32 or ord(character) == 127
            for character in token
        ):
            raise ValueError("control token must be non-empty text without whitespace")
        return value


class AsteriskTransportSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LUCY_TELEPHONY_",
        extra="ignore",
        populate_by_name=True,
    )

    mode: AsteriskTransportMode = Field(
        default=AsteriskTransportMode.AUDIO_SOCKET,
        validation_alias="LUCY_TELEPHONY_TRANSPORT_MODE",
    )
    audio_socket_host: str = "lucy-media-gateway"
    audio_socket_port: int = Field(
        default=9092,
        ge=MIN_NETWORK_PORT,
        le=MAX_NETWORK_PORT,
    )
    media_websocket_host: str = "lucy-media-gateway"
    media_websocket_port: int = Field(
        default=9093,
        ge=MIN_NETWORK_PORT,
        le=MAX_NETWORK_PORT,
    )
    ari_host: str = "asterisk"
    ari_port: int = Field(
        default=8088,
        ge=MIN_NETWORK_PORT,
        le=MAX_NETWORK_PORT,
    )
    ari_user: str | None = None
    ari_password: SecretStr | None = None
    external_media_rtp_host: str = "0.0.0.0"
    external_media_rtp_port: int = Field(
        default=10_000,
        ge=MIN_NETWORK_PORT,
        le=MAX_NETWORK_PORT,
    )

    @field_validator(
        "audio_socket_port",
        "media_websocket_port",
        "ari_port",
        "external_media_rtp_port",
        mode="before",
    )
    @classmethod
    def reject_boolean_port(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("port must be an integer")
        return value

    @field_validator(
        "audio_socket_host",
        "media_websocket_host",
        "ari_host",
        "external_media_rtp_host",
    )
    @classmethod
    def require_host(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("host cannot be blank")
        return normalized

    @model_validator(mode="after")
    def require_ari_credentials_for_external_media(self) -> AsteriskTransportSettings:
        if self.mode is AsteriskTransportMode.ARI_EXTERNAL_MEDIA and (
            self.ari_user is None
            or not self.ari_user.strip()
            or self.ari_password is None
            or not self.ari_password.get_secret_value()
        ):
            raise ValueError("ARI credentials are required for ari_external_media mode")
        return self


class CpaasTransportSettings(BaseSettings):
    """Optional CPaaS credentials for explicitly selected PSTN transports."""

    model_config = SettingsConfigDict(
        env_prefix="LUCY_CPAAAS_",
        extra="ignore",
        env_ignore_empty=True,
    )

    provider: CpaasTransportMode | None = None
    account_id: SecretStr | None = None
    api_key: SecretStr | None = None
    from_number: SecretStr | None = None
    to_number: SecretStr | None = None
    api_base_url: str | None = None
    public_ws_url: str | None = None
    stream_auth_token: SecretStr | None = None
    country_code: IsoCountryCode | None = None
    allow_insecure_local: bool = False
    request_timeout_seconds: float = Field(default=10.0, gt=0.0, le=60.0)

    @property
    def missing_credentials(self) -> tuple[str, ...]:
        values = (
            self.provider,
            self.account_id,
            self.api_key,
            self.from_number,
            self.to_number,
        )
        return tuple(
            variable
            for variable, value in zip(CPAAS_REQUIRED_ENVIRONMENT_VARIABLES, values)
            if value is None
        )

    @property
    def missing_stream_configuration(self) -> tuple[str, ...]:
        values = (
            self.api_base_url,
            self.public_ws_url,
            self.stream_auth_token,
        )
        return tuple(
            variable
            for variable, value in zip(CPAAS_STREAM_ENVIRONMENT_VARIABLES, values)
            if value is None
            and not (
                variable == "LUCY_CPAAAS_STREAM_AUTH_TOKEN"
                and self.provider is CpaasTransportMode.TWILIO
            )
        )

    @field_validator(
        "account_id", "api_key", "from_number", "to_number", "stream_auth_token"
    )
    @classmethod
    def reject_blank_cpaas_secret(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value().strip():
            raise ValueError("CPaaS secret values cannot be blank")
        return value

    @field_validator("api_base_url", "public_ws_url")
    @classmethod
    def reject_unsafe_cpaas_url_shape(cls, value: str | None) -> str | None:
        if value is None:
            return None
        from urllib.parse import urlsplit

        parsed = urlsplit(value)
        if (
            not parsed.scheme
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "CPaaS URLs cannot contain credentials, query, or fragment"
            )
        return value.rstrip("/")


class LlmPricing(BaseSettings):
    """Compatibility input for pre-card-66 driver construction.

    Arithmetic is delegated to ``PriceBook``; new code injects one price book
    into ``VoiceSession`` instead.
    """

    model_config = SettingsConfigDict(env_prefix="LUCY_LLM_PRICE_", extra="ignore")

    prompt_per_1k: float = 0.0
    completion_per_1k: float = 0.0

    def as_pricebook(self):
        from lucy.pricing import PriceBook

        return PriceBook(
            version="legacy-llm-pricing",
            llm_prompt_per_1k=self.prompt_per_1k,
            llm_completion_per_1k=self.completion_per_1k,
        )
