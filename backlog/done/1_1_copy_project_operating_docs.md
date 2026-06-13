# 1_1 - Copy project operating docs

**Epic:** Foundation
**Estimated effort:** ~45 min
**State:** done

## Goal

Create Lucy's local operating documents from the shared agent templates so the
repo has its own source of truth for agent behavior.

## Spec

`agents.md`, `MEMORY.md`, `backlog/agent_index.md`, and `backlog/_TEMPLATE.md`
exist in the Lucy repo. They use Lucy-specific names, paths, runtime commands,
language, and state folders.

## Files to create/modify

- `agents.md` - project operating guide
- `MEMORY.md` - project memory index
- `backlog/agent_index.md` - backlog rules
- `backlog/_TEMPLATE.md` - backlog task template

## Definition of Done

- [x] Operating docs exist at the repo root or under `backlog/`.
- [x] Docs use Lucy as the project name.
- [x] Docs name Docker Compose as the official runtime.
- [x] Targeted tests green in local runtime; Docker daemon unavailable in this session.
- [x] Post-task audit done

## Improvements noted

- Docker Compose verification should be rerun once the Docker daemon is active.
