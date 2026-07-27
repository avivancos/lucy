# Copyright 2026 Lucy contributors
# SPDX-License-Identifier: Apache-2.0
"""Synthetic credential-shaped strings built at runtime for tests.

Avoid committing literals that match secret scanners (GitGuardian, gitleaks).
"""

from __future__ import annotations


def synthetic_bearer_credential() -> str:
    return "Bearer " + "opaque-fixture-credential"


def synthetic_sk_opaque() -> str:
    return "sk-" + "fixture-opaque-credential-0000000000"


def synthetic_jwt_shape() -> str:
    return "eyJ" + "header." + "payload." + "signature"


def synthetic_configured_secret() -> str:
    return "lucy-test-configured-secret-" + "fixture0001"


def synthetic_cpaas_api_key() -> str:
    return "fixture-cpaas-api-credential"


def synthetic_cpaas_stream_token() -> str:
    return "fixture-cpaas-stream-token"


def synthetic_telnyx_api_key() -> str:
    return "fixture-telnyx-api-credential"


def synthetic_twilio_auth_token() -> str:
    return "fixture-twilio-auth-token"


def synthetic_ari_password() -> str:
    return "local-ari-" + "fixture-credential"


def synthetic_blob_bearer_session() -> str:
    return "Bearer " + "fixture-blob-session"
