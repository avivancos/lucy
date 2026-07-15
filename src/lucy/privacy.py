"""Shared secret and PII detection for outbound identifiers and telemetry."""

from __future__ import annotations

import re
from typing import Iterable, Mapping, Tuple

REDACTED = "[REDACTED]"

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<![A-Za-z0-9])\+?\d[\d\s().-]{7,}\d(?![A-Za-z0-9])")
_AUTH_HEADER = re.compile(
    r"(?im)\b(authorization|proxy-authorization)\s*[:=]\s*[^\r\n]+"
)
_COOKIE_HEADER = re.compile(r"(?im)\b(set-cookie|cookie)\s*[:=]\s*(?=\S)[^\r\n]+")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_BASIC_AUTH = re.compile(r"(?i)\bbasic(?:\s*[:=]\s*|\s+)[A-Za-z0-9+/=]+")
_URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)([^/@\s:]*):([^/@\s]+)@")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_CLOUD_ACCESS_KEY = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
_OPAQUE_SECRET = re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b", re.IGNORECASE)
_PEM_PRIVATE_KEY = re.compile(
    r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----.*?"
    r"-----END(?: [A-Z0-9]+)? PRIVATE KEY-----",
    re.DOTALL,
)
_TRUNCATED_PEM_PRIVATE_KEY = re.compile(
    r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----(?=\s*\S)[\s\S]+"
)
_INLINE_SECRET = re.compile(
    r"(?i)\b(password|passwd|secret|api[_-]?key|authorization|"
    r"client[_-]?secret|credentials?|private[_-]?key|"
    r"aws[_-]?access[_-]?key[_-]?id|aws[_-]?secret[_-]?access[_-]?key|"
    r"access[_-]?key[_-]?id|secret[_-]?access[_-]?key|"
    r"access[_-]?token|refresh[_-]?token|token)\s*[:=]\s*[^\s,;]+"
)
_KEY_NORMALIZER = re.compile(r"[^a-z0-9]+")
_SECRET_KEYS = frozenset(
    {
        "access_key_id",
        "access_token",
        "api_key",
        "apikey",
        "auth",
        "authentication",
        "authorization",
        "aws_access_key_id",
        "aws_secret_access_key",
        "client_secret",
        "cookie",
        "credential",
        "credentials",
        "jwt",
        "password",
        "passwd",
        "private_key",
        "proxy_authorization",
        "refresh_token",
        "secret",
        "secret_key",
        "secret_access_key",
        "set_cookie",
        "token",
    }
)


def scrub_secrets(text: str) -> str:
    """Mask common credentials in arbitrary runtime text."""
    text = _PEM_PRIVATE_KEY.sub(REDACTED, text)
    text = _TRUNCATED_PEM_PRIVATE_KEY.sub(REDACTED, text)
    text = _AUTH_HEADER.sub(lambda match: f"{match.group(1)}: {REDACTED}", text)
    text = _COOKIE_HEADER.sub(lambda match: f"{match.group(1)}: {REDACTED}", text)
    text = _BEARER.sub(f"Bearer {REDACTED}", text)
    text = _BASIC_AUTH.sub(f"Basic {REDACTED}", text)
    text = _URL_USERINFO.sub(
        lambda match: f"{match.group(1)}{REDACTED}:{REDACTED}@", text
    )
    text = _JWT.sub(REDACTED, text)
    text = _CLOUD_ACCESS_KEY.sub(REDACTED, text)
    text = _OPAQUE_SECRET.sub(REDACTED, text)
    return _INLINE_SECRET.sub(lambda match: f"{match.group(1)}={REDACTED}", text)


def redact_pii(text: str) -> str:
    """Mask email and phone-like PII in arbitrary text."""
    return _PHONE.sub(REDACTED, _EMAIL.sub(REDACTED, text))


def redact_text(text: str) -> str:
    """Mask runtime secrets, emails, and phone-like digit runs."""
    return redact_pii(scrub_secrets(text))


def contains_sensitive_text(text: str) -> bool:
    """Return whether sanitization would alter an opaque identifier."""
    return redact_text(text) != text


def is_secret_key(key: str) -> bool:
    """Return whether a map key names credential-bearing data."""
    normalized = _KEY_NORMALIZER.sub("_", key.lower()).strip("_")
    return (
        normalized in _SECRET_KEYS
        or normalized.endswith(
            (
                "_access_key",
                "_access_key_id",
                "_api_key",
                "_auth",
                "_authorization",
                "_cookie",
                "_credential",
                "_credentials",
                "_jwt",
                "_passwd",
                "_password",
                "_private_key",
                "_secret",
                "_secret_key",
                "_token",
            )
        )
        or normalized.startswith("secret_key_")
    )


def configured_secret_values(environment: Mapping[str, str]) -> Tuple[str, ...]:
    """Collect nonempty values whose environment keys are credential-bearing."""
    return tuple(
        value for name, value in environment.items() if value and is_secret_key(name)
    )


def contains_configured_secret(text: str, secrets: Iterable[str]) -> bool:
    """Return whether text contains any explicitly configured secret value."""
    return any(secret and secret in text for secret in secrets)


def scrub_configured_secrets(
    text: str,
    secrets: Iterable[str],
    *,
    replacement: str = REDACTED,
) -> str:
    """Replace configured secret occurrences, longest first."""
    configured = sorted({secret for secret in secrets if secret}, key=len, reverse=True)
    for secret in configured:
        text = text.replace(secret, replacement)
    return text
