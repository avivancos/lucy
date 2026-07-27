# Publication scrub (card 46)

This document records what the pre-publication scrub moved or renamed so
the public SDK repo stays free of private operating docs.

## Distribution rename

- PyPI / dist name: `lucy-ai` (`pyproject.toml` `[project].name`)
- Import package unchanged: `import lucy` from `src/lucy/`
- Plugin entry-point group unchanged: `lucy.plugins`
- Workspace members under `packages/*/` declare a dependency on
  `lucy-ai` (not `lucy`)
- Install: `pip install lucy-ai`

## Operating docs relocated to lucy-platform

Private operating material left this repo and now lives under
`/Users/agustin/Desarrollo/lucy-platform/ops/lucy-sdk/`:

| Former path (SDK) | New path (platform) |
| --- | --- |
| `agents.md` / `AGENTS.md` (same inode) | `ops/lucy-sdk/agents.md` (+ `AGENTS.md` hardlink) |
| `MEMORY.md` | `ops/lucy-sdk/MEMORY.md` |
| `backlog/` (entire tree) | `ops/lucy-sdk/backlog/` |
| `tests/test_project_contract.py` | `ops/lucy-sdk/tests/test_project_contract.py` |
| `tests/test_backlog_contract.py` | `ops/lucy-sdk/tests/test_backlog_contract.py` |

`tests/test_product_docs.py` was already at
`lucy-platform/tests/test_product_docs.py` (card 21) and was not moved
again.

`CLAUDE.md` (a loader that only `@`-included `agents.md` /
`backlog/agent_index.md`) was removed rather than left as a stub.

## Public scrub guards

`tests/test_publication_scrub.py` asserts the private paths stay gone,
SPDX headers and `TRADEMARKS.md` exist, the README quickstart matches
`examples/quickstart_voice_agent.py`, and release tooling
(`build`, `twine`) is pinned in the `dev` extra.

Secret-shaped test literals and CI gitleaks are documented in
[`docs/security/gitguardian-incidents-2026-07.md`](security/gitguardian-incidents-2026-07.md).

## Publish status

Artifacts build as `lucy_ai-0.1.0.tar.gz` and
`lucy_ai-0.1.0-py3-none-any.whl`. TestPyPI / PyPI upload (chips C7/C8)
requires human-provided `TWINE_*` tokens and is intentionally not done
from this scrub run.

## Platform contract tests after the move

`ops/lucy-sdk/tests/test_backlog_contract.py` keeps validating the private
backlog under `ops/lucy-sdk/`, but Cursor adapter and
`docs/agents/workflows/` checks resolve through `LUCY_SDK_ROOT` (default:
sibling `../lucy`) because those files remain in the public SDK.
