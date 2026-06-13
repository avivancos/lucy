# 1 - Bootstrap project contract

**Epic:** Foundation
**Estimated effort:** ~2 h
**State:** done

## Goal

Adapt the operating contract for Lucy so every future task follows the same
language, backlog, spec, TDD, runtime, and audit rules.

## Spec

The repo contains Lucy-specific `agents.md`, `MEMORY.md`, and `backlog/`.
Placeholders are replaced with real project values. The backlog folders exist
and the task template matches the project Definition of Done.

## Files to create/modify

- `agents.md` - Lucy operating guide
- `MEMORY.md` - project memory index
- `backlog/agent_index.md` - canonical backlog rules
- `backlog/_TEMPLATE.md` - task template

## Definition of Done

- [x] No dangling placeholders remain in operating docs.
- [x] Backlog state folders exist.
- [x] Task template includes spec, DoD, improvements, and human-testing sections.
- [x] Targeted tests green in local runtime; Docker daemon unavailable in this session.
- [x] Post-task audit done

## Improvements noted

- Docker Compose verification should be rerun once the Docker daemon is active.
