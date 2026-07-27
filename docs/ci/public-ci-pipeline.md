# Public CI pipeline (card 50)

## Purpose

Prove the open SDK launch claim on every `main` push/PR: installable,
keyless, and offline-testable, with zero GitHub secrets.

## Workflow

File: `.github/workflows/ci.yml`

| Job | What it proves |
| --- | --- |
| `lint` | `ruff check` + `ruff format --check` over `src tests packages`; `mypy src` when `[tool.mypy]` exists |
| `tests` | Python 3.10–3.12 matrix; installs workspace `packages/*`; pytest with network tripwire |
| `backlog-contract` | `tests/test_backlog_contract.py` (guarded so it vanishes when backlog moves private) |
| `build` | `uv build --all-packages` + `twine check --strict` |
| `compose-config` | `docker compose config --quiet` only (no up/build) |
| `secret-scan` | `gitleaks detect` via Docker with [`.gitleaks.toml`](../../.gitleaks.toml) |

## Secret scan tripwire

The `secret-scan` job runs gitleaks on the full checkout. Lab-only Asterisk
defaults and the local Postgres URL in `.env.example` are allowlisted; new
findings block merge. Pytest complements this with
[`tests/test_repo_secret_tripwires.py`](../../tests/test_repo_secret_tripwires.py)
(no committed GitHub PAT-shaped literals). Test fixtures should use
[`tests/test_secret_fixtures.py`](../../tests/test_secret_fixtures.py) instead
of provider-shaped literals.

See also [`docs/security/gitguardian-incidents-2026-07.md`](../security/gitguardian-incidents-2026-07.md).

## Network tripwire

Canonical flags (local and CI):

```bash
pytest -q --disable-socket --allow-unix-socket --allow-hosts=127.0.0.1,::1
```

Loopback stays allowed for local protocol servers (ADR 0003). Durable
Postgres/Redis tests that resolve Compose DNS to bridge IPs skip under
the tripwire; GitHub runners typically lack those services and skip via
missing `LUCY_DATABASE_URL` / `LUCY_REDIS_URL`.

## Contract tests

`tests/test_ci_pipeline.py` parses the workflow YAML and `pyproject.toml`
(and asserts the README CI badge). Keep workflow edits in sync with those
tests.

## Toolchain pins

`ruff` and `pytest-socket` live only in `[project.optional-dependencies]
dev`. The workflow installs `-e ".[dev]"` and does not repeat version pins.
