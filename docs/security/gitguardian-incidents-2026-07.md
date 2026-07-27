# GitGuardian incidents (July 2026)

This record maps GitGuardian alerts on `avivancos/lucy` to repository
evidence and the remediation applied in the forward-fix branch. It does
not replace the GitGuardian dashboard for validity checks.

## Incident summary

| GitGuardian type | Commit | Source in repo | Risk | Repo action |
| --- | --- | --- | --- | --- |
| GitHub Personal Access Token | `a02bec3` | Synthetic `ghp_…` in `tests/test_observability.py` (card 98) | False positive — sequential test fixture, not a live PAT | Replaced with `synthetic_configured_secret()`; tripwire forbids `ghp_` literals |
| Generic Password | `47a44f3`, `fa8b6a4` | Asterisk lab defaults (`lucy-lab-only`, `password = …`) in `.env.example`, Compose, templates | Localhost-only lab credentials | Documented rotation via `openssl rand -hex 16`; gitleaks allowlist for lab defaults |
| Username Password | `fa8b6a4` | ARI Basic auth wiring (card 41) | Lab credentials only | Same as generic password row |
| Bearer Token | `fa8b6a4` | Test literals such as `Bearer secret` in observability/CPaaS tests | False positive — shape tests for telemetry validation | Runtime-built bearer fixtures; no `Bearer secret*` literals |

## Human follow-up (GitGuardian UI)

1. Open each incident and confirm **secret validity** (expect invalid / test for the PAT).
2. Resolve as **false positive** or **revoked test credential** with a link to this doc.
3. **Rotate** only if GitGuardian or GitHub marks a token as valid or you reused a real PAT elsewhere.

## Guards added

- [`tests/test_repo_secret_tripwires.py`](../tests/test_repo_secret_tripwires.py) — fails CI if `ghp_[A-Za-z0-9]{20,}` appears in tracked sources.
- [`tests/test_secret_fixtures.py`](../tests/test_secret_fixtures.py) — shared synthetic credentials for tests.
- [`.gitleaks.toml`](../.gitleaks.toml) + CI job `secret-scan` in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml).

## History rewrite

Not required when the PAT is confirmed synthetic. Use `git filter-repo` only if
policy demands secrets never appear in history or GitGuardian reports a **valid**
leaked token.
